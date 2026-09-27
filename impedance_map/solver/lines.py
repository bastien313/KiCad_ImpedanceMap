"""Paramètres de ligne à partir des matrices de capacités C (diélectriques réels) et C0 (vide).

L = μ0 ε0 C0⁻¹ (hypothèse quasi-TEM, conducteurs parfaits, milieu non magnétique).

Ligne simple : Z0 = √(L/C) = 1 / (c √(C·C0)), εeff = C / C0.

Paire couplée (analyse modale) :
  M = L·C ; M = T Λ T⁻¹ ; √M = T Λ^½ T⁻¹
  matrice d'impédance caractéristique  Zc = (√M)⁻¹ · L   (V = Zc·I pour une onde progressive)
  Zdiff = Zc11 + Zc22 − 2 Zc12        (I1 = −I2)
  Zcomm = (Zc11 + Zc22 + 2 Zc12) / 4   (I1 = I2, les deux brins en parallèle)
  Zodd  = Zdiff / 2 ; Zeven = 2 Zcomm
  εeff de chaque mode = c² λ_i
  coefficient de couplage k = (Zeven − Zodd) / (Zeven + Zodd)
Pour une paire symétrique on retrouve exactement Zodd = Z11 − Z12, Zeven = Z11 + Z12.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np

C0_LIGHT = 299_792_458.0
MU0 = 1.25663706212e-6
EPS0 = 8.8541878128e-12


@dataclass
class LineResult:
    kind: str                         # "single" ou "pair"
    z0: float = float("nan")          # ligne simple
    eps_eff: float = float("nan")
    zodd: float = float("nan")
    zeven: float = float("nan")
    zdiff: float = float("nan")
    zcomm: float = float("nan")
    z_self: tuple = ()                # Zc11, Zc22 (Z0 de chaque brin dans la paire)
    eps_eff_odd: float = float("nan")
    eps_eff_even: float = float("nan")
    k_coupling: float = float("nan")
    C: Optional[list] = None          # F/m
    L: Optional[list] = None          # H/m
    delay_ps_per_mm: float = float("nan")
    error_estimate: float = float("nan")   # écart relatif estimé (convergence de grille)
    n_nodes: int = 0

    @property
    def z_main(self) -> float:
        """Valeur principale affichée : Z0 (simple) ou Zdiff (paire)."""
        return self.z0 if self.kind == "single" else self.zdiff

    def to_dict(self):
        return asdict(self)


def line_parameters(C: np.ndarray, C0: np.ndarray) -> LineResult:
    C = np.atleast_2d(np.asarray(C, dtype=float))
    C0 = np.atleast_2d(np.asarray(C0, dtype=float))
    n = C.shape[0]
    L = MU0 * EPS0 * np.linalg.inv(C0)
    if n == 1:
        c, c0 = C[0, 0], C0[0, 0]
        z0 = 1.0 / (C0_LIGHT * np.sqrt(c * c0))
        eeff = c / c0
        return LineResult(kind="single", z0=float(z0), eps_eff=float(eeff),
                          C=C.tolist(), L=L.tolist(),
                          delay_ps_per_mm=float(np.sqrt(eeff) / C0_LIGHT * 1e-3 * 1e12))
    if n != 2:
        raise ValueError("line_parameters : 1 ou 2 conducteurs actifs seulement")

    M = L @ C
    lam, T = np.linalg.eig(M)
    lam = np.real(lam)
    T = np.real(T)
    sqrtM = T @ np.diag(np.sqrt(lam)) @ np.linalg.inv(T)
    Zc = np.linalg.solve(sqrtM, L)
    Zc = 0.5 * (Zc + Zc.T)
    z11, z22, z12 = Zc[0, 0], Zc[1, 1], Zc[0, 1]
    zdiff = z11 + z22 - 2.0 * z12
    zcomm = (z11 + z22 + 2.0 * z12) / 4.0
    zodd, zeven = zdiff / 2.0, 2.0 * zcomm
    # identification des modes : vecteur propre de signes opposés = mode impair
    eeff = C0_LIGHT ** 2 * lam
    odd_idx = int(np.argmin([T[0, i] * T[1, i] / (np.linalg.norm(T[:, i]) ** 2) for i in range(2)]))
    even_idx = 1 - odd_idx
    return LineResult(
        kind="pair", zodd=float(zodd), zeven=float(zeven), zdiff=float(zdiff), zcomm=float(zcomm),
        z_self=(float(z11), float(z22)), eps_eff_odd=float(eeff[odd_idx]),
        eps_eff_even=float(eeff[even_idx]), k_coupling=float((zeven - zodd) / (zeven + zodd)),
        C=C.tolist(), L=L.tolist(),
        delay_ps_per_mm=float(np.sqrt(eeff[odd_idx]) / C0_LIGHT * 1e-3 * 1e12),
    )
