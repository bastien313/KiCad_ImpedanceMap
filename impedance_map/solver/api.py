"""API haut niveau : résolution d'une coupe avec convergence de grille automatique.

Stratégie (voir docs/SOLVER.md) :
  La méthode nodale converge au 1er ordre environ à cause des singularités de
  champ aux arêtes des conducteurs (rapport d'erreur observé ≈ 1,55 quand la
  grille est raffinée d'un facteur 1,5). On résout donc la coupe sur plusieurs
  niveaux de grille emboîtés et on extrapole (Richardson) les matrices C et C0
  élément par élément, avant d'en déduire L, Z, εeff.

  * mode "fast"    : niveaux 2 et 3, rapport de convergence supposé 1,55.
  * mode "precise" : niveaux 4, 5, 6 ; rapport estimé sur les 3 niveaux (Aitken),
                     borné à [1,3 ; 2,5]. Si l'estimation est instable, repli sur 1,55.

  error_estimate = |Z_extrapolé − Z_grille_fine| / Z (majorant prudent de l'erreur de
  discrétisation résiduelle, en pratique 4 à 12 x l'erreur réelle mesurée sur les cas
  analytiques).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .fdm import FieldSolution, solve_capacitance
from .geometry import CrossSection
from .lines import LineResult, line_parameters
from .mesh import MeshSettings

BASE_MESH = MeshSettings(n_res=10.0, growth=1.5, n_layer=3)
REFINE = 1.5
DEFAULT_RATIO = 1.55


@dataclass
class SolveOptions:
    """Options du solveur.

    mode        : "fast" ou "precise".
    levels      : niveaux de grille explicites (remplace le mode).
    extrapolate : extrapolation de Richardson (recommandé).
    """
    mode: str = "fast"
    levels: Optional[Tuple[int, ...]] = None
    extrapolate: bool = True

    def resolved_levels(self) -> Tuple[int, ...]:
        if self.levels:
            return tuple(self.levels)
        return (4, 5, 6) if self.mode == "precise" else (2, 3)


def _capacitances(xs: CrossSection, level: int, keep_field: bool):
    settings = BASE_MESH.refined(level, REFINE)
    sol = solve_capacitance(xs, settings, keep_field=keep_field)
    sol0 = solve_capacitance(xs.with_vacuum(), settings, keep_field=False, grid=sol.grid)
    return sol.C, sol0.C, sol


def _observed_ratio(v0, v1, v2) -> Optional[float]:
    d1, d2 = v1 - v0, v2 - v1
    if abs(d2) < 1e-14 * max(abs(v2), 1e-30) or d1 * d2 <= 0:
        return None
    r = d1 / d2
    if not (1.3 <= r <= 2.5):
        return None
    return r


def solve_cross_section(xs: CrossSection, options: Optional[SolveOptions] = None,
                        keep_field: bool = False) -> Tuple[LineResult, Optional[FieldSolution]]:
    """Résout la coupe. Retourne (LineResult, FieldSolution de la grille la plus fine si keep_field)."""
    options = options or SolveOptions()
    levels = options.resolved_levels()
    Cs: List[np.ndarray] = []
    C0s: List[np.ndarray] = []
    sol = None
    n_nodes = 0
    i = 0
    while i < len(levels):
        lv = levels[i]
        try:
            C, C0, sol = _capacitances(xs, lv, keep_field and i == len(levels) - 1)
        except MemoryError:
            # maillage trop grand (fenêtre large, beaucoup de cuivre) : on garde les niveaux déjà
            # calculés s'il y en a au moins deux, sinon on décale toute la série d'un niveau vers le bas
            if len(Cs) >= 2:
                levels = levels[:i]
                if keep_field:
                    sol = _capacitances(xs, levels[-1], True)[2]
                break
            if levels[0] == 0:
                raise
            levels = tuple(max(0, x - 1) for x in levels)
            levels = tuple(sorted(set(levels)))
            Cs, C0s, i = [], [], 0
            continue
        Cs.append(C)
        C0s.append(C0)
        n_nodes = sol.n_nodes
        i += 1

    fine = line_parameters(Cs[-1], C0s[-1])
    if not options.extrapolate or len(levels) < 2:
        fine.n_nodes = n_nodes
        return fine, sol

    ratio = DEFAULT_RATIO
    if len(levels) >= 3:
        # rapport estimé sur la trace de C (quantité scalaire robuste)
        r_c = _observed_ratio(*[np.trace(c) for c in Cs[-3:]])
        r_c0 = _observed_ratio(*[np.trace(c) for c in C0s[-3:]])
        rs = [r for r in (r_c, r_c0) if r is not None]
        if rs:
            ratio = float(np.mean(rs))
    Cx = Cs[-1] + (Cs[-1] - Cs[-2]) / (ratio - 1.0)
    C0x = C0s[-1] + (C0s[-1] - C0s[-2]) / (ratio - 1.0)
    res = line_parameters(Cx, C0x)
    res.error_estimate = abs(res.z_main - fine.z_main) / abs(res.z_main)
    res.n_nodes = n_nodes
    return res, sol


def convergence_study(xs: CrossSection, levels=range(0, 7)):
    """Tableau (niveau, nœuds, Z principal non extrapolé) pour la documentation / les tests."""
    rows = []
    for lv in levels:
        C, C0, sol = _capacitances(xs, lv, False)
        r = line_parameters(C, C0)
        rows.append((lv, sol.n_nodes, r.z_main))
    return rows
