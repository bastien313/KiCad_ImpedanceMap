"""Validation du solveur 2D contre les formules analytiques de référence.

Seuils (cahier des charges) :
  * stripline t=0 vs Cohn (exact)                          < 1 %
  * microstrip vs Hammerstad-Jensen                        < 3 %
  * paire microstrip vs Kirschning-Jansen                  < 3 %
  * coplanaire avec plan (CBCPW) vs Wadell                 < 3 %
Les écarts mesurés sont écrits dans tests/_validation_results.json (repris dans le README).
"""

import json
import pathlib

import pytest

from impedance_map.solver import SolveOptions, solve_cross_section
from impedance_map.solver.canonical import microstrip, stripline
from impedance_map.solver.formulas import (cohn_coupled_stripline, cohn_stripline, hammerstad_jensen,
                                           kirschning_jansen, wadell_cpwg)

MM = 1e-3
RESULTS = {}
OUT = pathlib.Path(__file__).with_name("_validation_results.json")


def _record(case, mode, value, ref, err):
    RESULTS.setdefault(case, {})[mode] = {"solveur": round(value, 3), "reference": round(ref, 3),
                                          "ecart_pct": round(100 * err, 3)}
    OUT.write_text(json.dumps(RESULTS, indent=1, ensure_ascii=False), encoding="utf-8")


MODES = ["fast", "precise"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("w,b,er", [(0.2, 0.5, 4.4), (0.1, 0.8, 4.0), (0.5, 0.4, 3.5), (0.15, 0.3, 4.6)])
def test_stripline_cohn(mode, w, b, er):
    ref = cohn_stripline(w * MM, b * MM, er)
    r, _ = solve_cross_section(stripline(w * MM, b * MM, er), SolveOptions(mode=mode))
    err = r.z0 / ref - 1
    _record(f"Stripline Cohn w={w} b={b} εr={er}", mode, r.z0, ref, err)
    assert abs(err) < 0.01
    assert r.eps_eff == pytest.approx(er, rel=1e-6)   # milieu homogène


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("w,s,b,er", [(0.2, 0.2, 0.6, 4.4), (0.1, 0.15, 0.4, 3.8)])
def test_coupled_stripline_cohn(mode, w, s, b, er):
    ze, zo = cohn_coupled_stripline(w * MM, s * MM, b * MM, er)
    r, _ = solve_cross_section(stripline(w * MM, b * MM, er, s=s * MM), SolveOptions(mode=mode))
    _record(f"Paire stripline Cohn Zodd w={w} s={s} b={b}", mode, r.zodd, zo, r.zodd / zo - 1)
    _record(f"Paire stripline Cohn Zeven w={w} s={s} b={b}", mode, r.zeven, ze, r.zeven / ze - 1)
    assert abs(r.zodd / zo - 1) < 0.01
    assert abs(r.zeven / ze - 1) < 0.01
    assert r.zdiff == pytest.approx(2 * r.zodd, rel=1e-9)
    assert r.zcomm == pytest.approx(r.zeven / 2, rel=1e-9)
    assert r.z_self[0] == pytest.approx(r.z_self[1], rel=1e-3)   # symétrie


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("w,h,er", [(0.3, 0.2, 4.4), (2.9, 1.53, 4.6), (0.1, 0.1, 4.1), (0.2, 0.0764, 3.91)])
def test_microstrip_hammerstad_jensen(mode, w, h, er):
    ref, ee = hammerstad_jensen(w * MM, h * MM, er)
    r, _ = solve_cross_section(microstrip(w * MM, h * MM, er), SolveOptions(mode=mode))
    err = r.z0 / ref - 1
    _record(f"Microstrip H-J w={w} h={h} εr={er}", mode, r.z0, ref, err)
    assert abs(err) < 0.03
    assert abs(r.eps_eff / ee - 1) < 0.03


@pytest.mark.parametrize("w,h,er,t", [(0.3, 0.2, 4.4, 0.035), (0.35, 0.2104, 4.4, 0.035)])
def test_microstrip_thickness_hammerstad_jensen(w, h, er, t):
    ref, _ = hammerstad_jensen(w * MM, h * MM, er, t * MM)
    r, _ = solve_cross_section(microstrip(w * MM, h * MM, er, t=t * MM), SolveOptions(mode="fast"))
    err = r.z0 / ref - 1
    _record(f"Microstrip épaisse H-J w={w} h={h} t={t}", "fast", r.z0, ref, err)
    assert abs(err) < 0.03


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("w,s,h,er", [(0.3, 0.2, 0.2, 4.4), (0.15, 0.15, 0.1, 4.1), (1.0, 0.5, 0.5, 10.0),
                                      (0.2, 1.0, 0.2, 4.4)])
def test_coupled_microstrip_kirschning_jansen(mode, w, s, h, er):
    ze, zo, eee, eeo = kirschning_jansen(w * MM, s * MM, h * MM, er)
    r, _ = solve_cross_section(microstrip(w * MM, h * MM, er, s=s * MM), SolveOptions(mode=mode))
    _record(f"Paire microstrip K-J Zodd w={w} s={s} h={h} εr={er}", mode, r.zodd, zo, r.zodd / zo - 1)
    _record(f"Paire microstrip K-J Zeven w={w} s={s} h={h} εr={er}", mode, r.zeven, ze, r.zeven / ze - 1)
    assert abs(r.zodd / zo - 1) < 0.03
    assert abs(r.zeven / ze - 1) < 0.03
    assert abs(r.eps_eff_odd / eeo - 1) < 0.03
    assert abs(r.eps_eff_even / eee - 1) < 0.03


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("w,g,h,er", [(0.3, 0.15, 0.2, 4.4), (0.5, 0.2, 1.5, 4.6), (0.2, 0.1, 0.1, 4.1)])
def test_cpwg_wadell(mode, w, g, h, er):
    ref, ee = wadell_cpwg(w * MM, g * MM, h * MM, er)
    r, _ = solve_cross_section(microstrip(w * MM, h * MM, er, coplanar_gap=g * MM), SolveOptions(mode=mode))
    err = r.z0 / ref - 1
    _record(f"Coplanaire+plan Wadell w={w} g={g} h={h}", mode, r.z0, ref, err)
    assert abs(err) < 0.03
