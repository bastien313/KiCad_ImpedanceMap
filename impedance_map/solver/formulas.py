"""Formules analytiques de référence (validation du solveur et estimations rapides).

Toutes les formules sont quasi-statiques, conducteurs d'épaisseur nulle sauf mention.

  * cohn_stripline            stripline centrée, t = 0 (exacte, transformation conforme)
  * cohn_coupled_stripline    paire stripline couplée par les bords, centrée, t = 0 (exacte)
  * hammerstad_jensen         microstrip, t = 0 (précision ~0,2 %, 0,01 ≤ w/h ≤ 100)
  * kirschning_jansen         paire microstrip couplée, t = 0, statique
                              (≈1 %, 0,1 ≤ w/h ≤ 10, 0,1 ≤ s/h ≤ 10, 1 ≤ εr ≤ 18)
  * wadell_cpwg               coplanaire avec plan de masse (CBCPW), t = 0, masses latérales infinies

Références :
  S. B. Cohn, "Characteristic impedance of the shielded-strip transmission line", IRE MTT 1954 ;
  S. B. Cohn, "Shielded coupled-strip transmission line", IRE MTT 1955 ;
  E. Hammerstad, Ø. Jensen, "Accurate models for microstrip computer-aided design", IEEE MTT-S 1980 ;
  M. Kirschning, R. H. Jansen, "Accurate wide-range design equations for the frequency-dependent
  characteristic of parallel coupled microstrip lines", IEEE MTT 1984 (+ correction 1985) ;
  B. C. Wadell, "Transmission Line Design Handbook", Artech House 1991, §3.4.
"""

from __future__ import annotations

import math

from scipy.special import ellipk, ellipkm1

C0_LIGHT = 299_792_458.0
ETA0 = 376.730313668


def _k_ratio(k: float) -> float:
    """K(k)/K(k') avec k module (pas paramètre), stable près de 0 et 1."""
    m = k * k
    return float(ellipk(m) / ellipkm1(m))


def cohn_stripline(w: float, b: float, er: float) -> float:
    """Z0 d'une stripline centrée entre deux plans espacés de b, t = 0."""
    k = 1.0 / math.cosh(math.pi * w / (2 * b))
    return ETA0 / (4 * math.sqrt(er)) * _k_ratio(k)


def cohn_coupled_stripline(w: float, s: float, b: float, er: float):
    """(Z0even, Z0odd) d'une paire stripline centrée, couplée par les bords, t = 0."""
    a = math.tanh(math.pi * w / (2 * b))
    ke = a * math.tanh(math.pi * (w + s) / (2 * b))
    ko = a / math.tanh(math.pi * (w + s) / (2 * b))
    ze = ETA0 / (4 * math.sqrt(er)) / _k_ratio(ke)
    zo = ETA0 / (4 * math.sqrt(er)) / _k_ratio(ko)
    return ze, zo


def _hj_air(u: float) -> float:
    f = 6 + (2 * math.pi - 6) * math.exp(-((30.666 / u) ** 0.7528))
    return ETA0 / (2 * math.pi) * math.log(f / u + math.sqrt(1 + 4 / u ** 2))


def _hj_eeff(u: float, er: float) -> float:
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


def hammerstad_jensen(w: float, h: float, er: float, t: float = 0.0):
    """(Z0, εeff) d'une microstrip ; t > 0 active la correction d'épaisseur de H&J."""
    u = w / h
    if t <= 0:
        ee = _hj_eeff(u, er)
        return _hj_air(u) / math.sqrt(ee), ee
    tn = t / h
    coth2 = 1.0 / math.tanh(math.sqrt(6.517 * u)) ** 2
    du1 = tn / math.pi * math.log(1 + 4 * math.e / (tn * coth2))
    dur = 0.5 * (1 + 1 / math.cosh(math.sqrt(er - 1))) * du1
    u1, ur = u + du1, u + dur
    ee = _hj_eeff(ur, er) * (_hj_air(u1) / _hj_air(ur)) ** 2
    return _hj_air(ur) / math.sqrt(_hj_eeff(ur, er)), ee


def kirschning_jansen(w: float, s: float, h: float, er: float):
    """(Z0even, Z0odd, εeff_even, εeff_odd) d'une paire microstrip couplée, t = 0, statique."""
    u, g = w / h, s / h
    z0, ee = hammerstad_jensen(w, h, er)
    # permittivités effectives des modes
    v = u * (20 + g * g) / (10 + g * g) + g * math.exp(-g)
    ae = 1 + math.log((v ** 4 + (v / 52) ** 2) / (v ** 4 + 0.432)) / 49 + math.log(1 + (v / 18.1) ** 3) / 18.7
    be = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    ee_e = (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / v) ** (-ae * be)
    ao = 0.7287 * (ee - (er + 1) / 2) * (1 - math.exp(-0.179 * u))
    bo = 0.747 * er / (0.15 + er)
    co = bo - (bo - 0.207) * math.exp(-0.414 * u)
    do = 0.593 + 0.694 * math.exp(-0.562 * u)
    ee_o = ((er + 1) / 2 + ao - ee) * math.exp(-co * g ** do) + ee
    # impédances
    q1 = 0.8695 * u ** 0.194
    q2 = 1 + 0.7519 * g + 0.189 * g ** 2.31
    q3 = 0.1975 + (16.6 + (8.4 / g) ** 6) ** (-0.387) + math.log(g ** 10 / (1 + (g / 3.4) ** 10)) / 241
    q4 = 2 * q1 / q2 / (math.exp(-g) * u ** q3 + (2 - math.exp(-g)) * u ** (-q3))
    q5 = 1.794 + 1.14 * math.log(1 + 0.638 / (g + 0.517 * g ** 2.43))
    q6 = 0.2305 + math.log(g ** 10 / (1 + (g / 5.8) ** 10)) / 281.3 + math.log(1 + 0.598 * g ** 1.154) / 5.1
    q7 = (10 + 190 * g * g) / (1 + 82.3 * g ** 3)
    q8 = math.exp(-6.5 - 0.95 * math.log(g) - (g / 0.15) ** 5)
    q9 = math.log(q7) * (q8 + 1 / 16.5)
    q10 = (q2 * q4 - q5 * math.exp(math.log(u) * q6 * u ** (-q9))) / q2
    ze = z0 * math.sqrt(ee / ee_e) / (1 - z0 * math.sqrt(ee) * q4 / ETA0)
    zo = z0 * math.sqrt(ee / ee_o) / (1 - z0 * math.sqrt(ee) * q10 / ETA0)
    return ze, zo, ee_e, ee_o


def wadell_cpwg(w: float, s: float, h: float, er: float):
    """(Z0, εeff) d'un coplanaire avec plan de masse inférieur (CBCPW), t = 0."""
    k1 = w / (w + 2 * s)
    k3 = math.tanh(math.pi * w / (4 * h)) / math.tanh(math.pi * (w + 2 * s) / (4 * h))
    r1 = _k_ratio(k1)      # K(k1)/K(k1')
    r3 = _k_ratio(k3)      # K(k3)/K(k3')
    q = (1 / r1) * r3      # K(k1')/K(k1) · K(k3)/K(k3')
    ee = (1 + er * q) / (1 + q)
    z0 = 60 * math.pi / math.sqrt(ee) / (r1 + r3)
    return z0, ee
