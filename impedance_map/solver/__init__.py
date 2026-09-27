"""Solveur de champ 2D quasi-statique (différences finies / volumes finis).

Point d'entrée principal :

    from impedance_map.solver import CrossSection, Conductor, Slab, MaskSpec, solve_cross_section
    res = solve_cross_section(xs, mode="fast")
    res.z0, res.zdiff, ...

Toutes les longueurs sont en mètres dans ce sous-paquet.
"""

from .geometry import Conductor, CrossSection, MaskSpec, Slab, SIGNAL, GROUND, FLOATING
from .mesh import MeshSettings
from .fdm import FieldSolution, solve_capacitance
from .lines import LineResult, line_parameters
from .api import solve_cross_section, SolveOptions

__all__ = [
    "Conductor", "CrossSection", "MaskSpec", "Slab", "SIGNAL", "GROUND", "FLOATING",
    "MeshSettings", "FieldSolution", "solve_capacitance", "LineResult", "line_parameters",
    "solve_cross_section", "SolveOptions",
]
