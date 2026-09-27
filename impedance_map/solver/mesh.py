"""Génération de la grille rectilinéaire non uniforme.

Principe : on impose des lignes de grille sur tous les points clés (arêtes des
conducteurs, interfaces diélectriques, bords du masque, bords du domaine). À
chaque point clé on associe une taille de maille locale ; la taille autorisée
croît ensuite linéairement avec la distance (h(x) = s_k + (r-1)|x-p_k|), ce qui
correspond exactement à une progression géométrique de raison r. Les nœuds sont
placés en équirépartissant l'intégrale de 1/h(x) sur chaque intervalle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np

from .geometry import CrossSection, SIGNAL


@dataclass
class MeshSettings:
    """Paramètres de maillage.

    n_res     : nombre de mailles sur la dimension caractéristique la plus petite
                (largeur de piste, épaisseur de diélectrique ou gap) -> taille fine.
    growth    : raison de progression géométrique loin des arêtes.
    n_layer   : nombre minimal de mailles dans l'épaisseur de chaque diélectrique.
    max_cells_x / max_cells_y : garde-fous.
    """
    n_res: float = 24.0
    growth: float = 1.3
    n_layer: int = 4
    max_cells_x: int = 900
    max_cells_y: int = 900

    def refined(self, level: int, factor: float = 1.5) -> "MeshSettings":
        f = factor ** level
        return MeshSettings(
            n_res=self.n_res * f,
            growth=1.0 + (self.growth - 1.0) / f,
            n_layer=int(round(self.n_layer * f)),
            max_cells_x=self.max_cells_x,
            max_cells_y=self.max_cells_y,
        )


FAST = MeshSettings(n_res=20.0, growth=1.35, n_layer=4)
PRECISE = MeshSettings(n_res=40.0, growth=1.2, n_layer=6)


def _merge_points(points: Sequence[Tuple[float, float]], tol: float):
    """Trie et fusionne les points clés (position, taille) trop proches."""
    pts = sorted(points)
    out: List[List[float]] = []
    for p, s in pts:
        if out and p - out[-1][0] <= tol:
            out[-1][1] = min(out[-1][1], s)
        else:
            out.append([p, s])
    return np.array([o[0] for o in out]), np.array([o[1] for o in out])


def graded_axis(keypoints: Sequence[Tuple[float, float]], lo: float, hi: float,
                growth: float, h_max: float, caps: Sequence[Tuple[float, float, float]] = (),
                tol: float = 1e-12) -> np.ndarray:
    """Construit un axe 1D gradué.

    keypoints : [(position, taille_locale)] ; les positions deviennent des nœuds exacts.
    caps      : [(a, b, h_cap)] taille maximale imposée sur [a, b] (ex. épaisseur / n).
    """
    pts = [(lo, h_max), (hi, h_max)] + [(p, s) for p, s in keypoints if lo - tol <= p <= hi + tol]
    pts = [(min(max(p, lo), hi), s) for p, s in pts]
    P, S = _merge_points(pts, tol=max(tol, 1e-9 * (hi - lo)))
    S = np.minimum(S, h_max)
    g = growth - 1.0

    def h_of(x):
        h = np.full_like(x, h_max)
        for p, s in zip(P, S):
            h = np.minimum(h, s + g * np.abs(x - p))
        for a, b, hc in caps:
            m = (x >= a - tol) & (x <= b + tol)
            h[m] = np.minimum(h[m], hc)
        return h

    nodes = [P[0]]
    for a, b in zip(P[:-1], P[1:]):
        L = b - a
        if L <= 0:
            continue
        hmin = max(min(S.min(), L) * 0.05, L * 1e-6)
        # échantillonnage dense près des bornes, grossier au milieu
        u = np.concatenate([
            np.linspace(0.0, 1.0, 401),
            np.geomspace(hmin / L, 1.0, 120),
            1.0 - np.geomspace(hmin / L, 1.0, 120),
        ])
        u = np.unique(np.clip(u, 0.0, 1.0))
        xs = a + L * u
        inv = 1.0 / h_of(xs)
        F = np.concatenate([[0.0], np.cumsum(0.5 * (inv[1:] + inv[:-1]) * np.diff(xs))])
        n = max(1, int(np.ceil(F[-1] - 1e-9)))
        if n > 1:
            targets = np.linspace(0.0, F[-1], n + 1)[1:-1]
            nodes.extend(np.interp(targets, F, xs).tolist())
        nodes.append(b)
    return np.array(nodes)


@dataclass
class Grid:
    x: np.ndarray   # abscisses des nœuds (Nx)
    y: np.ndarray   # ordonnées des nœuds (Ny)

    @property
    def shape(self):
        return (len(self.y), len(self.x))


def build_grid(xs: CrossSection, settings: MeshSettings) -> Tuple[Grid, float, float]:
    """Construit la grille pour une coupe. Retourne (grid, y_bottom, y_top)."""
    feat = xs.feature_size()
    h_f = feat / settings.n_res
    width = xs.x_max - xs.x_min
    y_lo_c, y_hi_c = xs.content_bounds_y()
    sub_lo, sub_hi = xs.substrate_bounds()
    board_t = max(sub_hi - sub_lo, feat)
    air_above = xs.air_above if xs.air_above is not None else max(10.0 * board_t, 1.0 * width)
    air_below = xs.air_below if xs.air_below is not None else max(10.0 * board_t, 1.0 * width)
    y_bot = y_lo_c - air_below
    y_top = y_hi_c + air_above

    # --------------------------------------------------------------- axe x
    kx: List[Tuple[float, float]] = []
    for c in xs.conductors:
        near = c.role == SIGNAL or _near_signal(xs, c, 4.0 * feat + 0.0)
        s = h_f if near else 2.5 * h_f
        for v in (c.x0b, c.x1b, c.x0t, c.x1t):
            if xs.x_min < v < xs.x_max:
                kx.append((v, s))
    for m in xs.masks:
        for c in xs.conductors:
            on = (m.side == "top" and abs(c.y0 - m.y_surface) < 1e-12) or \
                 (m.side == "bottom" and abs(c.y1 - m.y_surface) < 1e-12)
            if on and m.c2 > 0:
                for v in (c.xmin - m.c2, c.xmax + m.c2):
                    if xs.x_min < v < xs.x_max:
                        kx.append((v, max(h_f, m.c2 / 2)))
    h_max_x = width / 12.0
    x = graded_axis(kx, xs.x_min, xs.x_max, settings.growth, h_max_x)

    # --------------------------------------------------------------- axe y
    ky: List[Tuple[float, float]] = []
    caps: List[Tuple[float, float, float]] = []
    for s in xs.slabs:
        hs = s.thickness / settings.n_layer
        ky.append((s.y0, hs))
        ky.append((s.y1, hs))
        if s.thickness > 0:
            caps.append((s.y0, s.y1, hs))
    for c in xs.conductors:
        near = c.role == SIGNAL or _near_signal(xs, c, 4.0 * feat)
        s = h_f if near else 2.0 * h_f
        t = c.y1 - c.y0
        if t > 0:
            s_t = min(s, t / 2.0)
            ky.append((c.y0, s_t))
            ky.append((c.y1, s_t))
            caps.append((c.y0, c.y1, max(t / 2.0, 1e-12)))
        else:
            ky.append((c.y0, s))
    for m in xs.masks:
        sign = 1.0 if m.side == "top" else -1.0
        if m.c1 > 0:
            ky.append((m.y_surface + sign * m.c1, max(h_f, m.c1 / 2)))
            a, b = sorted((m.y_surface, m.y_surface + sign * m.c1))
            caps.append((a, b, m.c1 / 2))
        if m.c2 > 0:
            for c in xs.conductors:
                if m.side == "top" and abs(c.y0 - m.y_surface) < 1e-12:
                    ky.append((c.y1 + m.c2, max(h_f, m.c2 / 2)))
                if m.side == "bottom" and abs(c.y1 - m.y_surface) < 1e-12:
                    ky.append((c.y0 - m.c2, max(h_f, m.c2 / 2)))
    h_max_y = max(board_t, width / 12.0)
    y = graded_axis(ky, y_bot, y_top, settings.growth, h_max_y, caps)

    if len(x) - 1 > settings.max_cells_x or len(y) - 1 > settings.max_cells_y:
        raise MemoryError(f"Maillage trop grand ({len(x)}x{len(y)}) : réduire la précision ou la fenêtre")
    return Grid(x=x, y=y), y_bot, y_top


def _near_signal(xs: CrossSection, c, dist: float) -> bool:
    for s in xs.signals:
        dx = max(c.xmin - s.xmax, s.xmin - c.xmax, 0.0)
        dy = max(c.y0 - s.y1, s.y0 - c.y1, 0.0)
        if dx <= dist and dy <= dist:
            return True
    return False
