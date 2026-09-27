"""Calcul inverse : largeur (et gap) de piste pour une impédance cible sur une couche du stackup.

Coupe canonique : plans de masse pleins sur les couches de référence (par défaut, les
couches cuivre adjacentes), masque conforme sur les faces externes, diélectriques du
stackup. Résolution par la méthode de Brent sur log(w) (Z décroît avec w).
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

from scipy.optimize import brentq

from .extraction.crosssection import _base_at_bottom, _truncate_behind_full_planes
from .solver import Conductor, CrossSection, MaskSpec, Slab, SolveOptions, solve_cross_section
from .solver.geometry import GROUND, SIGNAL
from .solver.lines import LineResult
from .stackup.model import Stackup


def default_references(stackup: Stackup, layer: str) -> List[str]:
    names = stackup.copper_names
    i = names.index(layer)
    return [names[j] for j in (i - 1, i + 1) if 0 <= j < len(names)]


def stackup_section(stackup: Stackup, layer: str, w: float, s: Optional[float] = None,
                    refs: Optional[Sequence[str]] = None, window: Optional[float] = None,
                    etch_factor: float = 0.0, coplanar_gap: Optional[float] = None) -> CrossSection:
    lay = stackup.vertical_layout()
    ycu = lay["copper"]
    refs = list(refs) if refs is not None else default_references(stackup, layer)
    h = stackup.nearest_plane_distance(layer)
    span = w if s is None else 2 * w + s
    if coplanar_gap:
        span += 2 * coplanar_gap
    win = window or max(12 * span, 14 * h, span + 12 * h)
    lo, hi = -win / 2, win / 2
    conds: List[Conductor] = []
    for r in refs:
        y0, y1 = ycu[r]
        conds.append(Conductor(lo, hi, y0, y1, GROUND, name=f"plan {r}", layer=r))
    y0, y1 = ycu[layer]
    t = y1 - y0
    base_bottom = _base_at_bottom(stackup, layer)

    def trace(x0, x1, idx):
        if etch_factor > 0 and t > 0:
            d = min(t / etch_factor, 0.45 * (x1 - x0))
            if base_bottom:
                return Conductor(x0, x1, y0, y1, SIGNAL, idx, x0t=x0 + d, x1t=x1 - d, layer=layer)
            return Conductor(x0 + d, x1 - d, y0, y1, SIGNAL, idx, x0t=x0, x1t=x1, layer=layer)
        return Conductor(x0, x1, y0, y1, SIGNAL, idx, layer=layer)

    if s is None:
        conds.append(trace(-w / 2, w / 2, 0))
        el, er_ = -w / 2, w / 2
    else:
        conds.append(trace(-s / 2 - w, -s / 2, 0))
        conds.append(trace(s / 2, s / 2 + w, 1))
        el, er_ = -s / 2 - w, s / 2 + w
    if coplanar_gap:
        conds.append(Conductor(lo, el - coplanar_gap, y0, y1, GROUND, layer=layer))
        conds.append(Conductor(er_ + coplanar_gap, hi, y0, y1, GROUND, layer=layer))
    slabs = [Slab(d[0], d[1], d[2], d[3]) for d in lay["dielectrics"]]
    for L, e in lay["copper_fill_er"].items():
        a, b = ycu[L]
        slabs.append(Slab(a, b, e, f"résine {L}"))
    masks = []
    mt, mb = stackup.mask("top"), stackup.mask("bottom")
    if mt is not None and mt.thickness > 0:
        masks.append(MaskSpec("top", lay["top_surface"], mt.thickness,
                              mt.mask_c2 if mt.mask_c2 is not None else mt.thickness, mt.er))
    if mb is not None and mb.thickness > 0:
        masks.append(MaskSpec("bottom", lay["bottom_surface"], mb.thickness,
                              mb.mask_c2 if mb.mask_c2 is not None else mb.thickness, mb.er))
    xs = CrossSection(slabs=slabs, conductors=conds, x_min=lo, x_max=hi, masks=masks)
    return _truncate_behind_full_planes(xs, layer, stackup.copper_names, ycu)


def impedance(stackup: Stackup, layer: str, w: float, s: Optional[float] = None, mode: str = "fast",
              **kw) -> LineResult:
    res, _ = solve_cross_section(stackup_section(stackup, layer, w, s, **kw), SolveOptions(mode=mode))
    return res


def solve_width(stackup: Stackup, layer: str, z_target: float, s: Optional[float] = None, mode: str = "fast",
                w_min: float = 0.03e-3, w_max: float = 6e-3, **kw) -> float:
    """Largeur donnant Z0 (s None) ou Zdiff (gap s fixé) = z_target."""
    def f(lw):
        return impedance(stackup, layer, math.exp(lw), s, mode, **kw).z_main - z_target
    a, b = math.log(w_min), math.log(w_max)
    fa, fb = f(a), f(b)
    if fa * fb > 0:
        raise ValueError(f"Z cible {z_target:.1f} Ω hors de portée sur {layer} "
                         f"(Z de {fb + z_target:.1f} à {fa + z_target:.1f} Ω pour w ∈ [{w_min * 1e3:.2f}, {w_max * 1e3:.2f}] mm)")
    return math.exp(brentq(f, a, b, xtol=1e-4, rtol=1e-4))


def solve_gap(stackup: Stackup, layer: str, z_target: float, w: float, mode: str = "fast",
              s_min: float = 0.03e-3, s_max: float = 5e-3, **kw) -> float:
    """Gap donnant Zdiff = z_target pour une largeur w fixée (Zdiff croît avec s)."""
    def f(ls):
        return impedance(stackup, layer, w, math.exp(ls), mode, **kw).zdiff - z_target
    a, b = math.log(s_min), math.log(s_max)
    fa, fb = f(a), f(b)
    if fa * fb > 0:
        raise ValueError(f"Zdiff cible {z_target:.1f} Ω hors de portée avec w = {w * 1e3:.3f} mm "
                         f"(Zdiff de {fa + z_target:.1f} à {fb + z_target:.1f} Ω)")
    return math.exp(brentq(f, a, b, xtol=1e-4, rtol=1e-4))
