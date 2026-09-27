"""Assemblage et résolution de l'équation de Laplace généralisée div(ε grad φ) = 0.

Discrétisation : volumes finis sur grille rectilinéaire non uniforme, potentiels
aux nœuds, permittivité constante par cellule (les interfaces diélectriques sont
des lignes de grille). Schéma à 5 points ; la matrice de raideur K (N x N) est
symétrique définie positive une fois les conditions de Dirichlet imposées.

Conditions aux limites :
  * bords haut et bas du domaine (loin dans l'air) : Dirichlet 0 V ("infini") ;
  * bords latéraux : Neumann homogène (symétrie miroir) — un plan coupé au bord
    de la fenêtre est ainsi prolongé à l'infini, ce qui est le comportement voulu.

Conducteurs :
  * SIGNAL   : potentiel imposé (1 V pour la colonne j de C, 0 sinon) ;
  * GROUND   : 0 V ;
  * FLOATING : tous les nœuds d'un même corps sont fusionnés en une inconnue
               unique ; la ligne correspondante (somme des lignes) impose une
               charge nette nulle (loi de Gauss discrète).

Capacités : Q = Fᵀ K φ (charge de Gauss discrète, identique à la formulation
énergétique C_ij = φ_iᵀ K φ_j) -> matrice de Maxwell C symétrique, en F/m.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .geometry import CrossSection, SIGNAL, FLOATING
from .mesh import Grid, MeshSettings, build_grid

EPS0 = 8.8541878128e-12


@dataclass
class FieldSolution:
    C: np.ndarray                 # matrice de Maxwell (n_sig x n_sig), F/m
    grid: Grid
    phi: Optional[np.ndarray]     # potentiels (Ny, Nx, n_sig) si demandés
    eps_cells: Optional[np.ndarray]
    n_nodes: int


def cell_permittivity(xs: CrossSection, grid: Grid) -> np.ndarray:
    """εr par cellule (Ny-1, Nx-1), évaluée au centre des cellules."""
    xc = 0.5 * (grid.x[1:] + grid.x[:-1])
    yc = 0.5 * (grid.y[1:] + grid.y[:-1])
    X, Y = np.meshgrid(xc, yc)
    eps = np.ones_like(X)
    for m in xs.masks:
        if m.side == "top":
            reg = (Y >= m.y_surface) & (Y <= m.y_surface + m.c1)
            for c in xs.conductors:
                if abs(c.y0 - m.y_surface) < 1e-12:
                    reg |= (X >= c.xmin - m.c2) & (X <= c.xmax + m.c2) & \
                           (Y >= m.y_surface) & (Y <= c.y1 + m.c2)
        else:
            reg = (Y <= m.y_surface) & (Y >= m.y_surface - m.c1)
            for c in xs.conductors:
                if abs(c.y1 - m.y_surface) < 1e-12:
                    reg |= (X >= c.xmin - m.c2) & (X <= c.xmax + m.c2) & \
                           (Y <= m.y_surface) & (Y >= c.y0 - m.c2)
        eps[reg] = m.er
    # les tranches du substrat ont priorité (le masque ne pénètre pas le substrat)
    for s in xs.slabs:
        eps[(Y >= s.y0) & (Y < s.y1)] = s.er
    return eps


def conductor_labels(xs: CrossSection, grid: Grid, tol_rel: float = 1e-9) -> np.ndarray:
    """Étiquette de chaque nœud : -1 libre, sinon indice du conducteur dans xs.conductors."""
    Ny, Nx = grid.shape
    lab = -np.ones((Ny, Nx), dtype=np.int64)
    span = max(grid.x[-1] - grid.x[0], grid.y[-1] - grid.y[0])
    tol = tol_rel * span
    for k, c in enumerate(xs.conductors):
        jy = np.nonzero((grid.y >= c.y0 - tol) & (grid.y <= c.y1 + tol))[0]
        if len(jy) == 0:
            # conducteur plus fin qu'une maille : ligne de grille la plus proche
            jy = np.array([int(np.argmin(np.abs(grid.y - 0.5 * (c.y0 + c.y1))))])
        for j in jy:
            xl, xr = c.x_bounds_at(min(max(grid.y[j], c.y0), c.y1))
            ix = np.nonzero((grid.x >= xl - tol) & (grid.x <= xr + tol))[0]
            if len(ix) == 0:
                ix = np.array([int(np.argmin(np.abs(grid.x - 0.5 * (xl + xr))))])
            lab[j, ix] = k
    return lab


def _stiffness(grid: Grid, eps: np.ndarray) -> sp.csr_matrix:
    x, y = grid.x, grid.y
    Nx, Ny = len(x), len(y)
    dx = np.diff(x)
    dy = np.diff(y)
    idx = np.arange(Nx * Ny).reshape(Ny, Nx)

    # arêtes horizontales (i,j)-(i+1,j) : flux à travers la demi-hauteur des 2 cellules
    w_up = np.zeros((Ny, Nx - 1))
    w_up[:-1, :] = eps * dy[:, None] * 0.5          # cellule au-dessus de la ligne j
    w_dn = np.zeros((Ny, Nx - 1))
    w_dn[1:, :] = eps * dy[:, None] * 0.5           # cellule au-dessous
    gh = (w_up + w_dn) / dx[None, :]
    # arêtes verticales (i,j)-(i,j+1)
    w_r = np.zeros((Ny - 1, Nx))
    w_r[:, :-1] = eps * dx[None, :] * 0.5
    w_l = np.zeros((Ny - 1, Nx))
    w_l[:, 1:] = eps * dx[None, :] * 0.5
    gv = (w_r + w_l) / dy[:, None]

    a = np.concatenate([idx[:, :-1].ravel(), idx[:-1, :].ravel()])
    b = np.concatenate([idx[:, 1:].ravel(), idx[1:, :].ravel()])
    g = np.concatenate([gh.ravel(), gv.ravel()]) * EPS0
    N = Nx * Ny
    rows = np.concatenate([a, b, a, b])
    cols = np.concatenate([a, b, b, a])
    vals = np.concatenate([g, g, -g, -g])
    return sp.csr_matrix((vals, (rows, cols)), shape=(N, N))


def solve_capacitance(xs: CrossSection, settings: MeshSettings, keep_field: bool = False,
                      grid: Optional[Grid] = None) -> FieldSolution:
    """Matrice de capacités de Maxwell des conducteurs SIGNAL de la coupe."""
    if grid is None:
        grid, _, _ = build_grid(xs, settings)
    Ny, Nx = grid.shape
    N = Nx * Ny
    eps = cell_permittivity(xs, grid)
    K = _stiffness(grid, eps)
    lab = conductor_labels(xs, grid).ravel()

    n_sig = xs.n_signals
    if n_sig == 0:
        raise ValueError("Aucun conducteur SIGNAL dans la coupe")

    # nœuds de Dirichlet du domaine : premières et dernières lignes (air lointain)
    boundary = np.zeros((Ny, Nx), dtype=bool)
    boundary[0, :] = True
    boundary[-1, :] = True
    boundary = boundary.ravel()

    # correspondance nœud -> inconnue
    unk = -np.ones(N, dtype=np.int64)
    sig_of = -np.ones(N, dtype=np.int64)
    free = (lab < 0) & ~boundary
    n_free = int(free.sum())
    unk[free] = np.arange(n_free)
    n_unk = n_free
    float_groups = {}
    for k, c in enumerate(xs.conductors):
        nodes = lab == k
        if c.role == FLOATING:
            key = c.float_group if c.float_group >= 0 else ("solo", k)
            if key not in float_groups:
                float_groups[key] = n_unk
                n_unk += 1
            unk[nodes & ~boundary] = float_groups[key]
        elif c.role == SIGNAL:
            sig_of[nodes] = c.signal_index
        # GROUND : reste -1 (potentiel 0)

    rows_p = np.nonzero(unk >= 0)[0]
    P = sp.csr_matrix((np.ones(len(rows_p)), (rows_p, unk[rows_p])), shape=(N, n_unk))
    rows_f = np.nonzero(sig_of >= 0)[0]
    F = sp.csr_matrix((np.ones(len(rows_f)), (rows_f, sig_of[rows_f])), shape=(N, n_sig))
    if any((sig_of == s).sum() == 0 for s in range(n_sig)):
        raise ValueError("Un conducteur SIGNAL n'a aucun nœud de grille")

    KP = K @ P
    A = (P.T @ KP).tocsc()
    B = (P.T @ (K @ F)).toarray()
    lu = spla.splu(A)
    U = lu.solve(-B)
    phi = P @ U + F.toarray()                      # (N, n_sig)
    Q = F.T @ (K @ phi)                            # (n_sig, n_sig)
    C = 0.5 * (Q + Q.T)
    phi_grid = phi.reshape(Ny, Nx, n_sig) if keep_field else None
    return FieldSolution(C=np.asarray(C), grid=grid, phi=phi_grid,
                         eps_cells=eps if keep_field else None, n_nodes=N)
