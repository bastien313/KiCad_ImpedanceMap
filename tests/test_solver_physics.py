"""Tests de cohérence physique : convergence, sensibilité au domaine, flottants, masque, trapèze."""

import pytest

from impedance_map.solver import (Conductor, CrossSection, FLOATING, GROUND, SIGNAL, Slab,
                                  SolveOptions, solve_cross_section)
from impedance_map.solver.api import convergence_study
from impedance_map.solver.canonical import microstrip, stripline
from impedance_map.solver.formulas import cohn_stripline, hammerstad_jensen

MM = 1e-3


def test_grid_convergence_monotonic():
    """Z non extrapolé converge de façon monotone vers la valeur exacte, erreur décroissante."""
    ref = cohn_stripline(0.2 * MM, 0.5 * MM, 4.4)
    rows = convergence_study(stripline(0.2 * MM, 0.5 * MM, 4.4), levels=range(0, 6))
    errs = [abs(z / ref - 1) for _, _, z in rows]
    assert all(e2 < e1 for e1, e2 in zip(errs, errs[1:]))
    # ordre ~1 : rapport d'erreur entre 1,3 et 2,2 pour un raffinement de 1,5
    ratios = [e1 / e2 for e1, e2 in zip(errs, errs[1:])]
    assert all(1.3 < q < 2.2 for q in ratios[1:])


def test_precise_better_than_fast_error_estimate():
    xs = microstrip(0.3 * MM, 0.2 * MM, 4.4)
    rf, _ = solve_cross_section(xs, SolveOptions(mode="fast"))
    rp, _ = solve_cross_section(xs, SolveOptions(mode="precise"))
    assert rp.error_estimate < rf.error_estimate
    ref, _ = hammerstad_jensen(0.3 * MM, 0.2 * MM, 4.4)
    # l'estimation d'erreur est un majorant prudent
    assert abs(rf.z0 / ref - 1) < max(rf.error_estimate, 0.005)


@pytest.mark.parametrize("factor,tol", [(0.5, 0.01), (2.0, 0.003)])
def test_window_width_sensitivity(factor, tol):
    """Microstrip : doubler la fenêtre par défaut (≥ 20 w, 30 h) change Z de < 0,3 % ; la diviser par 2, < 1 %."""
    w, h, er = 0.3 * MM, 0.2 * MM, 4.4
    base = microstrip(w, h, er)
    width = base.x_max - base.x_min
    r0, _ = solve_cross_section(base, SolveOptions(mode="fast"))
    r1, _ = solve_cross_section(microstrip(w, h, er, window=width * factor), SolveOptions(mode="fast"))
    assert abs(r1.z0 / r0.z0 - 1) < tol


def test_window_minimum_documented():
    """Fenêtre du cahier des charges max(10 w, 6 h) : écart < 1 % par rapport à une fenêtre très large."""
    w, h, er = 0.3 * MM, 0.2 * MM, 4.4
    wide, _ = solve_cross_section(microstrip(w, h, er, window=60 * w), SolveOptions(mode="fast"))
    small, _ = solve_cross_section(microstrip(w, h, er, window=max(10 * w, 6 * h)), SolveOptions(mode="fast"))
    assert abs(small.z0 / wide.z0 - 1) < 0.01


@pytest.mark.parametrize("air", [10, 40])
def test_air_height_sensitivity(air):
    w, h, er = 0.3 * MM, 0.2 * MM, 4.4
    r0, _ = solve_cross_section(microstrip(w, h, er, air=20 * h), SolveOptions(mode="fast"))
    r1, _ = solve_cross_section(microstrip(w, h, er, air=air * h), SolveOptions(mode="fast"))
    assert abs(r1.z0 / r0.z0 - 1) < 0.005


def test_floating_plane_series_capacitance():
    """Plan flottant pleine largeur (charge nette nulle) entre la piste et la masse.

    Entre deux plans équipotentiels pleine largeur avec bords de Neumann, le champ est
    uniforme : C_pp = ε·W/h1 exactement. Le plan flottant met donc C_pp en série avec la
    capacité piste→plan (cas où le plan est à la masse) : 1/C_f = 1/C_g + 1/C_pp (idem pour C0).
    Vérifie aussi qu'ignorer le plan (microstrip h1+h2) donnerait un résultat très différent."""
    h1, h2, w, er = 0.2 * MM, 0.2 * MM, 0.35 * MM, 4.4
    win = 40 * w

    def build(role):
        return CrossSection(
            slabs=[Slab(0, h1, er), Slab(h1, h1 + h2, er)],
            conductors=[Conductor(-win / 2, win / 2, 0, 0, GROUND),
                        Conductor(-win / 2, win / 2, h1, h1, role, float_group=7),
                        Conductor(-w / 2, w / 2, h1 + h2, h1 + h2, SIGNAL, 0)],
            x_min=-win / 2, x_max=win / 2, air_above=20 * h2, air_below=h1)

    rf, _ = solve_cross_section(build(FLOATING), SolveOptions(mode="fast"))
    rg, _ = solve_cross_section(build(GROUND), SolveOptions(mode="fast"))
    z_thick, _ = hammerstad_jensen(w, h1 + h2, er)
    eps0 = 8.8541878128e-12
    cg = rg.C[0][0]
    c0g = 1.25663706212e-6 * eps0 / rg.L[0][0]
    cpp, c0pp = er * eps0 * win / h1, eps0 * win / h1
    cf, c0f = 1 / (1 / cg + 1 / cpp), 1 / (1 / c0g + 1 / c0pp)
    z_series = 1 / (299_792_458.0 * (cf * c0f) ** 0.5)
    assert rf.z0 > rg.z0
    assert abs(rf.z0 / z_series - 1) < 0.01
    assert abs(rf.z0 / z_thick - 1) > 0.2


def test_floating_neighbor_between_ground_and_absent():
    """Une piste voisine flottante perturbe moins qu'une voisine à la masse."""
    w, h, er = 0.3 * MM, 0.2 * MM, 4.4
    base = microstrip(w, h, er)
    z_alone = solve_cross_section(base, SolveOptions(mode="fast"))[0].z0
    res = {}
    for role in (GROUND, FLOATING):
        xs = microstrip(w, h, er)
        xs.conductors.append(Conductor(w / 2 + 0.15 * MM, w / 2 + 0.15 * MM + w, h, h, role))
        res[role] = solve_cross_section(xs, SolveOptions(mode="fast"))[0].z0
    assert res[GROUND] < res[FLOATING] <= z_alone * 1.001


def test_solder_mask_lowers_impedance():
    w, h, er = 0.35 * MM, 0.2104 * MM, 4.4
    t = 0.035 * MM
    r0, _ = solve_cross_section(microstrip(w, h, er, t=t), SolveOptions(mode="fast"))
    mask = (1.2 * 25.4e-6, 0.6 * 25.4e-6, 3.8)   # JLCPCB : C1 1,2 mil, C2 0,6 mil, εr 3,8
    r1, _ = solve_cross_section(microstrip(w, h, er, t=t, mask=mask), SolveOptions(mode="fast"))
    assert r1.z0 < r0.z0
    assert 0.005 < 1 - r1.z0 / r0.z0 < 0.08
    assert r1.eps_eff > r0.eps_eff


def test_trapezoid_between_bounds():
    w, h, er, t = 0.3 * MM, 0.2 * MM, 4.4, 0.035 * MM
    r_rect, _ = solve_cross_section(microstrip(w, h, er, t=t), SolveOptions(mode="fast"))
    r_trap, _ = solve_cross_section(microstrip(w, h, er, t=t, etch_factor=2.0), SolveOptions(mode="fast"))
    w_top = w - 2 * t / 2.0
    r_narrow, _ = solve_cross_section(microstrip(w_top, h, er, t=t), SolveOptions(mode="fast"))
    assert r_rect.z0 < r_trap.z0 < r_narrow.z0


def test_asymmetric_pair_consistency():
    """Paire asymétrique : Zdiff = Z11 + Z22 − 2 Z12 et Z11 ≠ Z22."""
    xs = microstrip(0.2 * MM, 0.2 * MM, 4.4, s=0.2 * MM)
    xs.conductors[2] = Conductor(0.1 * MM, 0.1 * MM + 0.3 * MM, 0.2 * MM, 0.2 * MM, SIGNAL, 1)
    r, _ = solve_cross_section(xs, SolveOptions(mode="fast"))
    assert r.z_self[0] != pytest.approx(r.z_self[1], rel=1e-2)
    assert 0 < r.k_coupling < 1
    assert r.zdiff < r.z_self[0] + r.z_self[1]


def test_cache_key_translation_invariant():
    a = microstrip(0.3 * MM, 0.2 * MM, 4.4)
    b = microstrip(0.3 * MM, 0.2 * MM, 4.4)
    for c in b.conductors:
        c.x0b += 1.0; c.x1b += 1.0; c.x0t += 1.0; c.x1t += 1.0
    b.x_min += 1.0
    b.x_max += 1.0
    assert a.quantized_key() == b.quantized_key()
    c = microstrip(0.31 * MM, 0.2 * MM, 4.4)
    assert a.quantized_key() != c.quantized_key()
