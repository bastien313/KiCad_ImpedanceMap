"""Constructeurs de coupes canoniques (validation, calcul inverse, démonstrations).

Toutes les dimensions en mètres. Les plans de masse sont des conducteurs GROUND
couvrant toute la fenêtre ; les fenêtres par défaut sont assez larges pour que
les résultats soient indépendants de la troncature (voir tests de sensibilité).
"""

from __future__ import annotations

from typing import Optional

from .geometry import Conductor, CrossSection, MaskSpec, Slab, GROUND, SIGNAL


def _trace(x0, x1, y0, t, idx, etch_factor=0.0, base_at_bottom=True, name="sig"):
    """Conducteur de largeur de base x1-x0, trapézoïdal si etch_factor > 0.

    etch_factor = t / retrait latéral (IPC) ; 0 => rectangle.
    """
    if etch_factor and t > 0:
        d = t / etch_factor
        if base_at_bottom:
            return Conductor(x0, x1, y0, y0 + t, SIGNAL, idx, x0t=x0 + d, x1t=x1 - d, name=name)
        return Conductor(x0 + d, x1 - d, y0, y0 + t, SIGNAL, idx, x0t=x0, x1t=x1, name=name)
    return Conductor(x0, x1, y0, y0 + t, SIGNAL, idx, name=name)


def stripline(w, b, er, t=0.0, s: Optional[float] = None, window=None, etch_factor=0.0):
    """Stripline (ou paire si s donné) centrée entre deux plans espacés de b (t = épaisseur trace)."""
    span = w if s is None else 2 * w + s
    win = window or max(10 * span, 10 * b)
    y0 = (b - t) / 2
    conds = [Conductor(-win / 2, win / 2, 0, 0, GROUND, name="plan_bas"),
             Conductor(-win / 2, win / 2, b, b, GROUND, name="plan_haut")]
    if s is None:
        conds.append(_trace(-w / 2, w / 2, y0, t, 0, etch_factor))
    else:
        conds.append(_trace(-s / 2 - w, -s / 2, y0, t, 0, etch_factor, name="P"))
        conds.append(_trace(s / 2, s / 2 + w, y0, t, 1, etch_factor, name="N"))
    return CrossSection(slabs=[Slab(0, b, er, "diel")], conductors=conds,
                        x_min=-win / 2, x_max=win / 2, air_above=b, air_below=b)


def microstrip(w, h, er, t=0.0, s: Optional[float] = None, window=None, air=None,
               mask: Optional[tuple] = None, etch_factor=0.0, coplanar_gap: Optional[float] = None):
    """Microstrip (ou paire si s donné) sur un diélectrique h au-dessus d'un plan.

    mask         : (c1, c2, er_mask) pour un masque conforme, ou None.
    coplanar_gap : si donné, ajoute une masse coplanaire de chaque côté (CPWG).
    """
    span = w if s is None else 2 * w + s
    if coplanar_gap is not None:
        span += 2 * coplanar_gap
    win = window or max(20 * span, 30 * h)
    conds = [Conductor(-win / 2, win / 2, 0, 0, GROUND, name="plan")]
    if s is None:
        conds.append(_trace(-w / 2, w / 2, h, t, 0, etch_factor))
        edge_l, edge_r = -w / 2, w / 2
    else:
        conds.append(_trace(-s / 2 - w, -s / 2, h, t, 0, etch_factor, name="P"))
        conds.append(_trace(s / 2, s / 2 + w, h, t, 1, etch_factor, name="N"))
        edge_l, edge_r = -s / 2 - w, s / 2 + w
    if coplanar_gap is not None:
        conds.append(Conductor(-win / 2, edge_l - coplanar_gap, h, h + t, GROUND, name="gnd_g"))
        conds.append(Conductor(edge_r + coplanar_gap, win / 2, h, h + t, GROUND, name="gnd_d"))
    masks = []
    if mask:
        c1, c2, erm = mask
        masks.append(MaskSpec("top", h, c1, c2, erm))
    return CrossSection(slabs=[Slab(0, h, er, "diel")], conductors=conds, masks=masks,
                        x_min=-win / 2, x_max=win / 2, air_above=air or max(20 * h, 2 * span), air_below=h)
