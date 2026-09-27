"""Plan de l'overlay (pur Python, sans KiCad) : tronçons colorés par couche, étiquettes, marqueurs.

KiCad 10 ne permet pas de colorer un objet individuellement via l'API (seules les
couleurs de couche et de net/netclass existent, globales). On découpe donc chaque
piste analysée en tronçons de Z quasi constante placés sur des couches User :
  * couche "ok"    : Z dans la tolérance ;
  * couche "high"  : Z trop haute ;
  * couche "low"   : Z trop basse ;
  * couche "annot" : étiquettes, marqueurs de discontinuité et de perte de référence,
                     tracé fin des zones non calculées.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..analysis.engine import AnalysisRun, SampleResult

Pt = Tuple[float, float]


@dataclass
class OverlayOptions:
    layer_ok: str = "User.1"
    layer_high: str = "User.2"
    layer_low: str = "User.3"
    layer_annot: str = "User.4"
    line_width_factor: float = 0.5        # × largeur de piste
    min_line_width: float = 0.05e-3
    label_interval: float = 10e-3         # étiquette au moins tous les 10 mm
    label_change: float = 0.03            # nouvelle étiquette si Z varie de plus de 3 %
    label_min_spacing: float = 2e-3
    text_size: float = 0.5e-3
    group_prefix: str = "ImpedanceMap"
    labels: bool = True
    markers: bool = True


@dataclass
class OvSeg:
    layer: str
    a: Pt
    b: Pt
    width: float


@dataclass
class OvCircle:
    layer: str
    c: Pt
    r: float
    width: float


@dataclass
class OvText:
    layer: str
    pos: Pt
    text: str
    size: float


@dataclass
class OverlayPlan:
    segs: List[OvSeg] = field(default_factory=list)
    circles: List[OvCircle] = field(default_factory=list)
    texts: List[OvText] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def n_items(self) -> int:
        return len(self.segs) + len(self.circles) + len(self.texts)


def classify(sr: SampleResult, zmin: float, zmax: float) -> str:
    if sr.status == "no_reference":
        return "noref"
    if not sr.valid:
        return "disc"
    if sr.z > zmax:
        return "high"
    if sr.z < zmin:
        return "low"
    return "ok"


def _pt(sr: SampleResult) -> Pt:
    if math.isfinite(sr.cx) and math.isfinite(sr.cy):
        return (sr.cx, sr.cy)
    return (sr.x, sr.y)


def fmt_z(z: float, kind: str) -> str:
    v = f"{z:.1f}" if z < 100 else f"{z:.0f}"
    return f"Zdiff {v} Ω" if kind == "pair" else f"{v} Ω"


def plan_overlay(run: AnalysisRun, opt: Optional[OverlayOptions] = None) -> OverlayPlan:
    opt = opt or OverlayOptions()
    layer_of = {"ok": opt.layer_ok, "high": opt.layer_high, "low": opt.layer_low,
                "disc": opt.layer_annot, "noref": opt.layer_annot}
    plan = OverlayPlan()
    for tr in run.targets:
        t = tr.target
        zmin, zmax = t["z_target"] * (1 - t["tol"]), t["z_target"] * (1 + t["tol"])
        by_path: Dict[Tuple[str, int], List[SampleResult]] = {}
        for sr in tr.samples:
            by_path.setdefault((sr.net, sr.path_id), []).append(sr)
        for samples in by_path.values():
            samples.sort(key=lambda s: s.s)
            _plan_path(plan, samples, t["kind"], zmin, zmax, layer_of, opt)
    return plan


def _plan_path(plan, samples, kind, zmin, zmax, layer_of, opt):
    n = len(samples)
    if n == 0:
        return
    pts = [_pt(s) for s in samples]
    cls = [classify(s, zmin, zmax) for s in samples]
    mids = [((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2) for i in range(n - 1)]
    for i, s in enumerate(samples):
        plan.counts[cls[i]] = plan.counts.get(cls[i], 0) + 1
        w = max(opt.min_line_width, s.width * opt.line_width_factor)
        if cls[i] in ("disc", "noref"):
            w = max(opt.min_line_width, w * 0.4)
        layer = layer_of[cls[i]]
        a = mids[i - 1] if i > 0 else pts[i]
        b = mids[i] if i < n - 1 else pts[i]
        for p, q in ((a, pts[i]), (pts[i], b)):
            if math.hypot(q[0] - p[0], q[1] - p[1]) > 1e-7:
                plan.segs.append(OvSeg(layer, p, q, w))

    # fusion des segments colinéaires consécutifs de même couche et largeur (moins d'objets)
    plan.segs[:] = _merge_collinear(plan.segs)

    if opt.markers:
        for i, s in enumerate(samples):
            starts = i == 0 or cls[i] != cls[i - 1]
            if cls[i] in ("disc", "noref") and starts:
                r = max(0.2e-3, s.width)
                plan.circles.append(OvCircle(opt.layer_annot, pts[i], r, max(opt.min_line_width, 0.05e-3)))
                if cls[i] == "noref":
                    nrm = (-s.ty, s.tx)
                    off = r + 1.2 * opt.text_size
                    plan.texts.append(OvText(opt.layer_annot, (pts[i][0] + nrm[0] * off, pts[i][1] + nrm[1] * off),
                                             "perte réf.", opt.text_size))

    if opt.labels:
        last_z, last_s, last_pos = None, -1e9, None
        for i, s in enumerate(samples):
            if cls[i] in ("disc", "noref"):
                continue
            change = last_z is None or abs(s.z - last_z) / last_z > opt.label_change
            far = s.s - last_s >= opt.label_interval
            if not (change or far):
                continue
            if last_pos is not None and math.hypot(pts[i][0] - last_pos[0], pts[i][1] - last_pos[1]) < opt.label_min_spacing \
                    and not far:
                continue
            nrm = (-s.ty, s.tx)
            half_span = (s.gap / 2 + s.width) if (kind == "pair" and math.isfinite(s.gap)) else s.width / 2
            off = half_span + 1.0 * opt.text_size + 0.1e-3
            pos = (pts[i][0] + nrm[0] * off, pts[i][1] + nrm[1] * off)
            plan.texts.append(OvText(opt.layer_annot, pos, fmt_z(s.z, kind), opt.text_size))
            last_z, last_s, last_pos = s.z, s.s, pts[i]


def _merge_collinear(segs: List[OvSeg], tol: float = 1e-9) -> List[OvSeg]:
    out: List[OvSeg] = []
    for sg in segs:
        if out:
            p = out[-1]
            if p.layer == sg.layer and abs(p.width - sg.width) < 1e-12 and \
                    math.hypot(p.b[0] - sg.a[0], p.b[1] - sg.a[1]) < 1e-9:
                d1 = (p.b[0] - p.a[0], p.b[1] - p.a[1])
                d2 = (sg.b[0] - sg.a[0], sg.b[1] - sg.a[1])
                cross = d1[0] * d2[1] - d1[1] * d2[0]
                n1, n2 = math.hypot(*d1), math.hypot(*d2)
                if n1 > 0 and n2 > 0 and abs(cross) / (n1 * n2) < 1e-6 and d1[0] * d2[0] + d1[1] * d2[1] > 0:
                    out[-1] = OvSeg(p.layer, p.a, sg.b, p.width)
                    continue
        out.append(sg)
    return out
