"""Symétrie d'une paire différentielle : longueurs P / N, skew intra-paire et où il s'accumule.

Le skew (écart de temps de propagation entre P et N) convertit une partie du signal différentiel en
mode commun : l'œil se ferme et le rayonnement (CEM) augmente. C'est, avec Zdiff et les
discontinuités, le point principal d'une paire.

  * longueurs : chemins principaux (le plus long) de P et de N, dans le plan (fûts de vias non comptés) ;
  * délai : Δt = ΔL · √εeff / c, εeff = médiane de εeff en mode impair sur les points calculés
    (3,0 à défaut) ;
  * écart cumulé ΔL(s) = s_P − s_N(s), s_N = abscisse sur le brin N du point en face de s (projection) :
    ses marches montrent les virages et détours qui créent le déséquilibre, donc où le compenser
    (au plus près de la cause, idéalement).
  * conversion de mode due au seul skew (signal.py) : |Scd21| ≈ |sin(π f Δt)| à la fréquence clé.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from shapely.geometry import LineString, Point

from .discontinuities import main_path_id

C_LIGHT = 299_792_458.0


def _line(path) -> Optional[LineString]:
    pts, _, _ = path.polyline()
    return LineString(pts) if len(pts) > 1 else None


def pair_symmetry(target, p_paths, n_paths, samples) -> Optional[dict]:
    if not p_paths or not n_paths:
        return None
    p_main = next(p for p in p_paths if p.path_id == main_path_id(p_paths))
    n_main = next(p for p in n_paths if p.path_id == main_path_id(n_paths))
    lp, ln = p_main.length, n_main.length
    eps = [s.result["eps_eff_odd"] for s in samples
           if s.valid and s.result and math.isfinite(s.result.get("eps_eff_odd", float("nan")))]
    e = float(np.median(eps)) if eps else 3.0
    dl = lp - ln
    out = {"p_net": target.nets[0], "n_net": target.nets[1], "len_p_mm": lp * 1e3, "len_n_mm": ln * 1e3,
           "delta_mm": dl * 1e3, "eps_eff": e, "skew_ps": dl * math.sqrt(e) / C_LIGHT * 1e12,
           "layers_p": sorted({x.seg.layer for x in p_main.elems}),
           "layers_n": sorted({x.seg.layer for x in n_main.elems}),
           "curve_s_mm": [], "curve_delta_mm": []}
    nl = _line(n_main)
    if nl is None:
        return out
    pts = sorted([s for s in samples if s.path_id == p_main.path_id and s.net == target.nets[0]
                  and math.isfinite(s.nx)], key=lambda s: s.s)
    # seulement là où les brins cheminent ensemble : aux extrémités découplées (connecteur, boîtier)
    # le point « en face » n'a plus de sens ; l'écart qui s'y crée est donné à part
    # critère : distance P–N au plus 1,5 × la distance typique des points calculés de la paire
    dist = [math.hypot(s.x - s.nx, s.y - s.ny) for s in pts]
    ref = [d for d, s in zip(dist, pts) if s.valid]
    d_ref = float(np.median(ref)) if ref else float(np.median(dist)) if dist else 0.0
    coupled = [k for k, d in enumerate(dist) if d <= 1.5 * d_ref + 1e-6]
    if coupled:
        pts = pts[coupled[0]:coupled[-1] + 1]
    for s in pts:
        sn = nl.project(Point(s.nx, s.ny))
        out["curve_s_mm"].append(s.s * 1e3)
        out["curve_delta_mm"].append((s.s - sn) * 1e3)
    if out["curve_delta_mm"]:
        # part de l'écart créée hors de la partie couplée (extrémités : sorties de boîtier, connecteur)
        out["uncoupled_delta_mm"] = out["delta_mm"] - (out["curve_delta_mm"][-1] - out["curve_delta_mm"][0])
    # plus forte variation locale de l'écart (où le déséquilibre se crée)
    if len(pts) > 2:
        d = np.array(out["curve_delta_mm"])
        x = np.array(out["curve_s_mm"])
        jumps = np.abs(np.diff(d))
        k = int(np.argmax(jumps))
        out["worst_step_mm"] = float(jumps[k])
        out["worst_step_at_mm"] = float(0.5 * (x[k] + x[k + 1]))
    return out
