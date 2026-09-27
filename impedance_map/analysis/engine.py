"""Moteur d'analyse : cibles -> échantillons -> coupes -> solveur (cache, parallèle) -> résultats.

Utilisable sans KiCad (BoardModel lu depuis un fichier) : c'est ce que font la CLI et les tests.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import math
import multiprocessing
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from shapely.geometry import MultiLineString, Point
from shapely.ops import nearest_points

from .. import __version__
from ..extraction.board_model import BoardModel
from ..extraction.crosssection import CutOptions, build_cut
from ..extraction.sampling import SamplingOptions, build_paths, sample_path
from .discontinuities import build_elements, main_path_id
from ..extraction.targets import Target
from ..solver import SolveOptions, solve_cross_section
from ..solver.geometry import CrossSection
from ..solver.lines import LineResult
from ..stackup.model import Stackup
from .cache import ResultCache


class Cancelled(Exception):
    pass


@dataclass
class AnalysisOptions:
    step: float = 0.5e-3                  # pas d'échantillonnage (m)
    window: Optional[float] = None        # largeur de fenêtre imposée (m) ; sinon règle ci-dessous
    window_w_factor: float = 10.0         # fenêtre = max(k_w·w, k_h·h_ref) (+ empreinte de la paire)
    window_h_factor: float = 6.0
    mode: str = "fast"                    # "fast" / "precise"
    etch_factor: float = 0.0              # 0 = rectangle
    corner_angle_deg: float = 5.0
    corner_zone: Optional[float] = None   # défaut max(w, 1,5·h_ref)
    arc_min_radius_factor: float = 1.0    # arcs de rayon < facteur × demi-fenêtre -> discontinuité
    quantum: float = 1e-6
    workers: Optional[int] = None         # None = auto ; 1 = série
    pair_max_distance_factor: float = 4.0 # distance P-N max = facteur × (w + gap nominal)
    return_via_distance: float = 1.0e-3   # bonus : via de retour attendu à moins de cette distance
    persist_cache: bool = True

    def to_dict(self):
        return asdict(self)


@dataclass
class SampleResult:
    target: str
    net: str
    path_id: int
    s: float
    x: float
    y: float
    tx: float
    ty: float
    layer: str
    width: float
    status: str                       # ok / no_reference / discontinuity / error
    reason: str = ""
    z: float = float("nan")           # Z0 (simple) ou Zdiff (paire)
    gap: float = float("nan")
    references: List[str] = field(default_factory=list)
    key: str = ""
    result: Optional[dict] = None     # LineResult sérialisé
    cx: float = float("nan")          # centre de la coupe (axe de la paire ; = x,y en simple)
    cy: float = float("nan")
    si: Optional[dict] = None         # modèle localisé (intégrité du signal) : pad fusionné ou brin de stub

    @property
    def valid(self) -> bool:
        """Valeur exploitable (statistiques, couleur). Les pertes de référence gardent leur Z
        calculée dans `z`/`result` pour information mais sont exclues (dépend de la boîte de calcul)."""
        return self.status == "ok" and math.isfinite(self.z)


@dataclass
class TargetReport:
    target: dict
    samples: List[SampleResult]
    stats: dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    signal: Optional[dict] = None     # intégrité du signal (analysis/signal.py), si calculée
    main_path: Optional[int] = None   # chemin principal (le plus long) du net P / du net
    elements: List[dict] = field(default_factory=list)       # vias, coins, fentes, stubs (discontinuities.py)
    element_notes: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.target["label"]


@dataclass
class AnalysisRun:
    board: str
    stackup: dict
    options: dict
    targets: List[TargetReport]
    warnings: List[str] = field(default_factory=list)
    timing: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)
    version: str = __version__
    sections: Dict[str, CrossSection] = field(default_factory=dict, repr=False)   # non sérialisé

    def to_dict(self):
        d = asdict(self)
        d.pop("sections", None)
        return d

    def to_json(self, path=None) -> str:
        s = json.dumps(self.to_dict(), indent=1, ensure_ascii=False, default=float)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(s)
        return s

    @staticmethod
    def from_json(path) -> "AnalysisRun":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        tr = [TargetReport(target=t["target"], samples=[SampleResult(**s) for s in t["samples"]],
                           stats=t.get("stats", {}), warnings=t.get("warnings", []), signal=t.get("signal"),
                           main_path=t.get("main_path"), elements=t.get("elements", []),
                           element_notes=t.get("element_notes", []))
              for t in d["targets"]]
        return AnalysisRun(board=d["board"], stackup=d["stackup"], options=d["options"], targets=tr,
                           warnings=d.get("warnings", []), timing=d.get("timing", {}), cache=d.get("cache", {}),
                           version=d.get("version", "?"))


# ============================================================================ calcul (processus fils)
def _solve_chunk(items: List[Tuple[str, CrossSection]], mode: str):
    out = []
    for key, xs in items:
        try:
            res, _ = solve_cross_section(xs, SolveOptions(mode=mode))
            out.append((key, res.to_dict(), None))
        except Exception as e:  # noqa: BLE001 — une coupe défaillante ne doit pas tuer l'analyse
            out.append((key, None, f"{type(e).__name__}: {e}"))
    return out


# ============================================================================ helpers
def _normal(t):
    return (-t[1], t[0])


def _tangent_near(seg, p):
    """Tangente unitaire du segment/arc `seg` au point le plus proche de p."""
    pts = seg.points()
    best, bd = (1.0, 0.0), float("inf")
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 <= 0:
            continue
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
        d = math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1])
        if d < bd:
            L = math.sqrt(L2)
            best, bd = (dx / L, dy / L), d
    return best


def _window(opt: AnalysisOptions, w: float, h_ref: float) -> float:
    if opt.window:
        return opt.window
    return max(opt.window_w_factor * w, opt.window_h_factor * h_ref)


def _weights(samples: List[SampleResult]) -> np.ndarray:
    """Poids en longueur de chaque échantillon (demi-intervalles voisins, par chemin)."""
    w = np.zeros(len(samples))
    by_path: Dict[Tuple[str, int], List[int]] = {}
    for i, s in enumerate(samples):
        by_path.setdefault((s.net, s.path_id), []).append(i)
    for idx in by_path.values():
        ss = np.array([samples[i].s for i in idx])
        if len(ss) == 1:
            w[idx[0]] = 1.0
            continue
        mid = 0.5 * (ss[1:] + ss[:-1])
        lo = np.concatenate([[ss[0]], mid])
        hi = np.concatenate([mid, [ss[-1]]])
        for k, i in enumerate(idx):
            w[i] = max(hi[k] - lo[k], 1e-9)
    return w


def compute_stats(tr: TargetReport):
    t = tr.target
    zt, tol = t["z_target"], t["tol"]
    zmin, zmax = zt * (1 - tol), zt * (1 + tol)
    W = _weights(tr.samples)
    valid = np.array([s.valid for s in tr.samples])
    z = np.array([s.z for s in tr.samples])
    total_len = float(W.sum())
    st = {"n_samples": len(tr.samples), "n_valid": int(valid.sum()),
          "n_discontinuity": int(sum(s.status == "discontinuity" for s in tr.samples)),
          "n_no_reference": int(sum(s.status == "no_reference" for s in tr.samples)),
          "n_error": int(sum(s.status == "error" for s in tr.samples)),
          "length_mm": total_len * 1e3, "z_target": zt, "tol": tol}
    if valid.any():
        zv, wv = z[valid], W[valid]
        out = (zv < zmin) | (zv > zmax)
        st.update(z_min=float(zv.min()), z_max=float(zv.max()), z_mean=float(np.average(zv, weights=wv)),
                  pct_out=float(100 * wv[out].sum() / wv.sum()),
                  pct_high=float(100 * wv[zv > zmax].sum() / wv.sum()),
                  pct_low=float(100 * wv[zv < zmin].sum() / wv.sum()),
                  pct_valid_length=float(100 * wv.sum() / max(total_len, 1e-12)))
        dev = np.abs(z - zt)
        dev[~valid] = -1
        st["worst_index"] = int(np.argmax(dev))
    tr.stats = st


# ============================================================================ moteur
class Engine:
    def __init__(self, bm: BoardModel, stackup: Stackup, options: Optional[AnalysisOptions] = None,
                 progress: Optional[Callable[[float, str], None]] = None,
                 cancel: Optional[threading.Event] = None, cache: Optional[ResultCache] = None):
        self.bm = bm
        self.stackup = stackup
        self.opt = options or AnalysisOptions()
        self.progress = progress or (lambda f, m: None)
        self.cancel = cancel or threading.Event()
        self.cache = cache or ResultCache(persist=self.opt.persist_cache)
        missing = [n for n in bm.copper_names if n not in stackup.copper_names]
        if missing:
            raise ValueError(f"Le stackup choisi ne contient pas les couches {missing} du board "
                             f"(stackup : {stackup.copper_names})")

    def _check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled()

    # ------------------------------------------------------------------ échantillons
    def _samples_single(self, t: Target):
        net = t.nets[0]
        paths = build_paths(self.bm.segs_of(net))
        out, cuts, si_cuts = [], [], []
        for path in paths:
            smp = sample_path(path, self._sampling(path), self.stackup.nearest_plane_distance, self._via_radius_at)
            for s in smp:
                self._check_cancel()
                h = self.stackup.nearest_plane_distance(s.layer)
                half = _window(self.opt, s.width, h) / 2
                sr = SampleResult(t.label, net, path.path_id, s.s, s.pos[0], s.pos[1], s.tangent[0], s.tangent[1],
                                  s.layer, s.width, s.status, s.reason, cx=s.pos[0], cy=s.pos[1])
                out.append(sr)
                tg = [(net, 0.0, s.width)]
                if s.status == "ok":
                    cuts.append((sr, s.pos, _normal(s.tangent), half, tg, s.layer))
                elif self.bm.pads_at(s.pos, s.layer, net):
                    # point exclu à l'échantillonnage (coin, extrémité…) mais sur un pad du net : modèle localisé
                    si_cuts.append((sr, s.pos, _normal(s.tangent), half, tg, s.layer, "main"))
        return out, cuts, si_cuts, paths

    def _samples_pair(self, t: Target):
        p_net, n_net = t.nets
        n_segs = self.bm.segs_of(n_net)
        n_lines: Dict[str, MultiLineString] = {}
        n_width: Dict[str, List] = {}
        for sg in n_segs:
            n_lines.setdefault(sg.layer, []).append(sg.points())
            n_width.setdefault(sg.layer, []).append(sg)
        n_lines = {L: MultiLineString(v) for L, v in n_lines.items()}
        nc = self.bm.netclasses.get(t.netclass)
        paths = build_paths(self.bm.segs_of(p_net))
        main_id = main_path_id(paths)
        out, cuts, si_cuts = [], [], []
        for path in paths:
            smp = sample_path(path, self._sampling(path), self.stackup.nearest_plane_distance, self._via_radius_at)
            for s in smp:
                self._check_cancel()
                sr = SampleResult(t.label, p_net, path.path_id, s.s, s.pos[0], s.pos[1], s.tangent[0], s.tangent[1],
                                  s.layer, s.width, s.status, s.reason, cx=s.pos[0], cy=s.pos[1])
                out.append(sr)
                if path.path_id != main_id:
                    # branche (stub) du brin P : impédance simple brin pour le modèle de stub
                    h = self.stackup.nearest_plane_distance(s.layer)
                    si_cuts.append((sr, s.pos, _normal(s.tangent), _window(self.opt, s.width, h) / 2,
                                    [(p_net, 0.0, s.width)], s.layer, "leg"))
                if s.status != "ok":
                    continue
                mls = n_lines.get(s.layer)
                if mls is None:
                    sr.status, sr.reason = "discontinuity", "brin N absent de cette couche"
                    continue
                pt = Point(s.pos)
                q = nearest_points(mls, pt)[0]
                dist = pt.distance(q)
                gap_nom = (nc.dp_gap if nc and nc.dp_gap else s.width)
                if dist > self.opt.pair_max_distance_factor * (s.width + gap_nom):
                    sr.status, sr.reason = "discontinuity", f"paire séparée ({dist * 1e3:.2f} mm)"
                    continue
                nseg = min(n_width[s.layer], key=lambda g: g.geometry().distance(q))
                wn = nseg.width
                tn = _tangent_near(nseg, (q.x, q.y))
                if abs(s.tangent[0] * tn[1] - s.tangent[1] * tn[0]) > math.sin(math.radians(15)):
                    sr.status, sr.reason = "discontinuity", "brins non parallèles"
                    continue
                c = ((s.pos[0] + q.x) / 2, (s.pos[1] + q.y) / 2)
                sr.cx, sr.cy = c
                nrm = _normal(s.tangent)
                up = (s.pos[0] - c[0]) * nrm[0] + (s.pos[1] - c[1]) * nrm[1]
                un = (q.x - c[0]) * nrm[0] + (q.y - c[1]) * nrm[1]
                h = self.stackup.nearest_plane_distance(s.layer)
                half = _window(self.opt, max(s.width, wn), h) / 2 + abs(up - un) / 2 + max(s.width, wn)
                cuts.append((sr, c, nrm, half, [(p_net, up, s.width), (n_net, un, wn, wn + gap_nom)], s.layer))
        return out, cuts, si_cuts, paths

    def _via_radius_at(self, p, tol: float = 5e-6) -> float:
        r = 0.0
        for v in self.bm.vias:
            if abs(v.pos[0] - p[0]) < tol and abs(v.pos[1] - p[1]) < tol:
                r = max(r, v.diameter / 2)
        return r

    def _sampling(self, path) -> SamplingOptions:
        w = max(e.seg.width for e in path.elems)
        h = min(self.stackup.nearest_plane_distance(e.seg.layer) for e in path.elems)
        half = _window(self.opt, w, h) / 2
        return SamplingOptions(step=self.opt.step, corner_angle_deg=self.opt.corner_angle_deg,
                               corner_zone=self.opt.corner_zone,
                               arc_min_radius=self.opt.arc_min_radius_factor * half)

    # ------------------------------------------------------------------ exécution
    def run(self, targets: Sequence[Target]) -> AnalysisRun:
        t0 = time.perf_counter()
        warnings: List[str] = list(self.bm.warnings)
        uz = self.bm.unfilled_zones()
        if uz:
            warnings.append(f"{len(uz)} zone(s) non remplie(s) ignorée(s) : " +
                            ", ".join(sorted({z.name or z.net for z in uz})) +
                            " — remplissez les zones (touche B) pour un calcul correct.")
        reports: List[TargetReport] = []
        all_cuts = []
        all_si = []
        target_paths = []
        self.progress(0.0, "Échantillonnage et construction des coupes")
        for ti, t in enumerate(targets):
            samples, cuts, si_cuts, paths = (self._samples_pair(t) if t.kind == "pair" else self._samples_single(t))
            all_si.extend(si_cuts)
            target_paths.append(paths)
            tr = TargetReport(target=asdict(t), samples=samples)
            if not samples:
                tr.warnings.append("aucune piste routée pour cette cible")
            tr.warnings.extend(self._return_via_warnings(t))
            reports.append(tr)
            all_cuts.extend(cuts)

        sections: Dict[str, CrossSection] = {}
        pending: Dict[str, List[SampleResult]] = {}
        n_cut = len(all_cuts)
        for i, (sr, c, nrm, half, tg, layer) in enumerate(all_cuts):
            self._check_cancel()
            if i % 20 == 0:
                self.progress(0.05 + 0.15 * i / max(n_cut, 1), f"Coupes : {i}/{n_cut}")
            xs, info = build_cut(self.bm, self.stackup, c, nrm, half, tg, layer,
                                 CutOptions(quantum=self.opt.quantum, etch_factor=self.opt.etch_factor))
            sr.references = info.references
            if not math.isnan(info.gap):
                sr.gap = info.gap
            if xs is None or info.status in ("discontinuity", "error"):
                sr.status, sr.reason = info.status, info.reason
                if info.status == "discontinuity" and (info.reason.startswith("pad") or
                                                       info.reason.startswith("cuivre du même net")):
                    # pad / cuivre du même net sur le trajet : coupe « fusionnée » pour le modèle localisé
                    all_si.append((sr, c, nrm, half, tg, layer, "main"))
                continue
            sr.status, sr.reason = info.status, info.reason
            key = ResultCache.key(xs.quantized_key(self.opt.quantum / 10), self.opt.mode)
            sr.key = key
            sections.setdefault(key, xs)
            pending.setdefault(key, []).append(sr)

        # coupes des modèles localisés (pads fusionnés, brins de stubs de paire)
        pending_si: Dict[str, List[Tuple[SampleResult, str]]] = {}
        for sr, c, nrm, half, tg, layer, mode in all_si:
            self._check_cancel()
            xs, info = build_cut(self.bm, self.stackup, c, nrm, half, tg, layer,
                                 CutOptions(quantum=self.opt.quantum, etch_factor=self.opt.etch_factor,
                                            merge_same_net=True))
            if xs is None or info.status == "error":
                continue
            key = ResultCache.key(xs.quantized_key(self.opt.quantum / 10), self.opt.mode)
            sections.setdefault(key, xs)
            pending_si.setdefault(key, []).append((sr, mode))

        t_cut = time.perf_counter()
        keys = list(pending) + [k for k in pending_si if k not in pending]
        todo = [(k, sections[k]) for k in keys if self.cache.get(k) is None]
        hits = len(pending) - len(todo)
        self.cache.hits += hits
        self.cache.misses += len(todo)
        errors = self._solve_all(todo)
        t_solve = time.perf_counter()

        for key, srs in pending.items():
            res = self.cache.get(key)
            for sr in srs:
                if res is None:
                    sr.status, sr.reason = "error", errors.get(key, "échec du solveur")
                    continue
                sr.result = res.to_dict()
                sr.z = res.z_main
        for key, lst in pending_si.items():
            res = self.cache.get(key)
            if res is None:
                continue
            for sr, mode in lst:
                if res.kind == "pair":
                    Cm = np.array(res.C)
                    sr.si = {"mode": mode, "z": res.zdiff, "e": res.eps_eff_odd, "c": float(Cm[0, 0] - Cm[0, 1])}
                else:
                    sr.si = {"mode": mode, "z": res.z0, "e": res.eps_eff, "c": float(np.array(res.C)[0, 0])}
        for tr, t, paths in zip(reports, targets, target_paths):
            try:
                tr.main_path, tr.elements, tr.element_notes = build_elements(
                    self.bm, self.stackup, t, paths, tr.samples, self.opt.return_via_distance,
                    self.opt.corner_angle_deg)
            except Exception as e:  # noqa: BLE001 — l'analyse d'impédance reste valable sans ces modèles
                tr.element_notes = [f"modèles localisés indisponibles : {e}"]
        for tr in reports:
            compute_stats(tr)
        self.cache.save()
        run = AnalysisRun(board=self.bm.name, stackup=self.stackup.to_dict(), options=self.opt.to_dict(),
                          targets=reports, warnings=warnings,
                          timing={"total_s": time.perf_counter() - t0, "cuts_s": t_cut - t0,
                                  "solve_s": t_solve - t_cut, "n_samples": sum(len(r.samples) for r in reports),
                                  "n_cuts": n_cut, "n_unique": len(keys), "n_solved": len(todo)},
                          cache={"hits": hits, "solved": len(todo)}, sections=sections)
        self.progress(1.0, "Terminé")
        return run

    def _solve_all(self, todo: List[Tuple[str, CrossSection]]) -> Dict[str, str]:
        errors: Dict[str, str] = {}
        if not todo:
            return errors
        workers = self.opt.workers
        if workers is None:
            workers = max(1, min((os.cpu_count() or 2) - 1, 16))
        total = len(todo)
        done = 0

        def consume(batch):
            nonlocal done
            for key, rd, err in batch:
                if rd is not None:
                    rd["z_self"] = tuple(rd.get("z_self") or ())
                    self.cache.put(key, LineResult(**rd))
                else:
                    errors[key] = err
                done += 1
            self.progress(0.2 + 0.8 * done / total, f"Solveur : {done}/{total} coupes uniques")

        if workers <= 1 or total < 8:
            for item in todo:
                self._check_cancel()
                consume(_solve_chunk([item], self.opt.mode))
            return errors
        chunk = max(1, min(8, total // (workers * 4) or 1))
        chunks = [todo[i:i + chunk] for i in range(0, total, chunk)]
        ctx = multiprocessing.get_context("spawn")
        ex = cf.ProcessPoolExecutor(max_workers=workers, mp_context=ctx)
        try:
            futs = [ex.submit(_solve_chunk, ch, self.opt.mode) for ch in chunks]
            for f in cf.as_completed(futs):
                if self.cancel.is_set():
                    raise Cancelled()
                consume(f.result())
        finally:
            ex.shutdown(wait=not self.cancel.is_set(), cancel_futures=True)
        return errors

    # ------------------------------------------------------------------ bonus : vias de retour
    def _return_via_warnings(self, t: Target) -> List[str]:
        """Changements de couche du signal sans via d'un net de plan à moins de return_via_distance."""
        plane_nets = {z.net for z in self.bm.zones if z.net and z.is_filled} - set(t.nets)
        if not plane_nets:
            return []
        out = []
        dmax = self.opt.return_via_distance
        for net in t.nets:
            layers_at: Dict[Tuple[int, int], set] = {}
            for sg in self.bm.segs_of(net):
                for p in (sg.start, sg.end):
                    layers_at.setdefault((round(p[0] * 1e6), round(p[1] * 1e6)), set()).add(sg.layer)
            for v in self.bm.vias:
                if v.net != net:
                    continue
                k = (round(v.pos[0] * 1e6), round(v.pos[1] * 1e6))
                if len(layers_at.get(k, ())) < 2:
                    continue
                near = [u for u in self.bm.vias if u.net in plane_nets and
                        math.hypot(u.pos[0] - v.pos[0], u.pos[1] - v.pos[1]) <= dmax]
                if not near:
                    out.append(f"{net} : changement de couche {'/'.join(sorted(layers_at[k]))} en "
                               f"({v.pos[0] * 1e3:.2f}, {v.pos[1] * 1e3:.2f}) mm sans via de retour "
                               f"(net de plan) à moins de {dmax * 1e3:.1f} mm")
        return out

    # ------------------------------------------------------------------ coupe détaillée (rapport)
    def detailed_section(self, run: AnalysisRun, sr: SampleResult):
        """(CrossSection, FieldSolution, LineResult) du point `sr` avec le champ, pour le rapport."""
        xs = run.sections.get(sr.key)
        if xs is None:
            return None
        res, sol = solve_cross_section(xs, SolveOptions(mode=self.opt.mode), keep_field=True)
        return xs, sol, res
