"""Intégrité du signal : paramètres S, perte de désadaptation et TDR contre des cas analytiques."""

import math

import numpy as np
import pytest

from impedance_map.analysis.signal import PRESETS, SignalSpec, analyze_target, sparams, tdr

C = 299_792_458.0


def test_matched_line_no_reflection():
    f = np.linspace(0, 10e9, 50)
    s11, s21 = sparams([0.05], [50.0], [3.2], 50.0, f)
    assert np.max(np.abs(s11)) < 1e-12
    assert np.allclose(np.abs(s21), 1.0)


def test_quarter_wave_transformer_and_energy():
    z1, z0, e = 70.0, 50.0, 4.0
    f0 = 1e9
    L = C / math.sqrt(e) / f0 / 4
    s11, s21 = sparams([L], [z1], [e], z0, np.array([f0, 2 * f0]))
    g = (z1 ** 2 - z0 ** 2) / (z1 ** 2 + z0 ** 2)      # quart d'onde : Zin = Z1²/Z0
    assert abs(s11[0]) == pytest.approx(abs(g), rel=1e-9)
    assert abs(s11[1]) < 1e-9                           # demi-onde : transparent
    assert np.allclose(np.abs(s11) ** 2 + np.abs(s21) ** 2, 1.0)   # sans pertes


def test_tdr_recovers_section_impedance():
    # 50 Ω | 100 mm à 64 Ω | ... vu avec un front rapide : plateau à 64 Ω puis retour à 50 Ω
    x, z, rho = tdr([0.02, 0.10, 0.02], [50.0, 64.0, 50.0], [3.3] * 3, 50.0, 30e-12)
    mid = z[(x > 0.05) & (x < 0.09)]
    assert mid.mean() == pytest.approx(64.0, rel=0.02)
    assert np.abs(z[x < 0.015] - 50).max() < 0.5
    assert np.max(np.abs(rho)) == pytest.approx((64 - 50) / (64 + 50), rel=0.05)


def test_slow_edge_hides_short_mismatch():
    x, z, rho = tdr([0.05, 0.002, 0.05], [90.0, 60.0, 90.0], [3.0] * 3, 90.0, 500e-12)
    assert np.max(np.abs(rho)) < 0.05          # 2 mm à 60 Ω invisible pour 500 ps


class _S:
    def __init__(self, s, z, e, ok=True, status=None):
        self.net, self.path_id, self.s = "N", 0, s
        self.valid = ok
        self.status = status or ("ok" if ok else "discontinuity")
        self.result = {"zdiff": z, "eps_eff_odd": e, "z0": z, "eps_eff": e}


class _TR:
    def __init__(self, samples, kind="pair", zt=90.0):
        self.samples = samples
        self.target = {"kind": kind, "z_target": zt}


def test_analyze_target_uniform_mismatch():
    s = np.arange(1, 200) * 0.5e-3                       # ligne de ~100 mm à 64 Ω, cible 90 Ω
    tr = _TR([_S(x, 64.0, 3.0) for x in s])
    out = analyze_target(tr, PRESETS["USB 2.0 High-Speed (480 Mb/s)"])
    g = (64 - 90) / (64 + 90)
    assert out["gamma_static_pct"] == pytest.approx(100 * abs(g), rel=1e-6)
    assert out["length_mm"] == pytest.approx(100.0, rel=0.01)
    # deux interfaces : |S11| max = 2|Γ|/(1+Γ²) -> perte de désadaptation max dans la bande
    gmax = 2 * abs(g) / (1 + g * g)
    assert out["worst_mismatch_loss_db_in_band"] <= -10 * math.log10(1 - gmax ** 2) + 1e-6
    assert 0 < out["mismatch_loss_db_at_fkey"] < 1.0
    assert out["tdr_z_min"] == pytest.approx(64.0, rel=0.05)
    assert out["tdr_peak_reflection_pct"] == pytest.approx(100 * abs(g), rel=0.1)


def test_rf_mode_and_interpolation():
    s = np.arange(1, 100) * 0.5e-3
    samples = [_S(x, 50.0, 3.3, ok=not (20e-3 < x < 25e-3),
                  status="no_reference" if 22e-3 < x < 23e-3 else None) for x in s]
    out = analyze_target(_TR(samples, "single", 50.0), SignalSpec("RF", "rf", freq=2.4e9))
    assert out["f_key"] == 2.4e9 and "tdr_z" not in out
    assert out["return_loss_db_at_fkey"] > 60          # ligne adaptée (zone interpolée à 50 Ω)
    assert 8 < out["interpolated_pct"] < 10          # 4,5 mm interpolés sur 50 mm
    assert out["no_reference_mm"] == pytest.approx(0.5, abs=0.01)   # 1 point sans référence (22,5 mm) = 0,5 mm


def test_quality_bands_and_ml_equivalents():
    from impedance_map.analysis.signal import quality, rl_to_ml
    assert [quality(x) for x in (25, 17, 12, 6)] == ["excellent", "bon", "acceptable", "mauvais"]
    assert rl_to_ml(20) == pytest.approx(0.0436, abs=1e-4)
    assert rl_to_ml(10) == pytest.approx(0.458, abs=1e-3)


def test_section_contributions_single_defect():
    """Une seule section désadaptée (70 Ω sur 5 mm dans une ligne 50 Ω) : seule, elle produit toute la
    réflexion, et la corriger annule la perte ; les tronçons adaptés ne contribuent pas."""
    s = np.arange(1, 100) * 0.5e-3
    tr = _TR([_S(x, 70.0 if 20e-3 < x < 25e-3 else 50.0, 3.0) for x in s], "single", 50.0)
    out = analyze_target(tr, SignalSpec("RF", "rf", freq=2.4e9))
    secs = out["sections"]
    assert secs[0]["kind"] == "high" and secs[0]["z_mean"] == pytest.approx(70.0)
    assert secs[0]["ml_alone_db"] == pytest.approx(out["mismatch_loss_db_at_fkey"], rel=1e-9)
    assert secs[0]["gain_if_fixed_db"] == pytest.approx(out["mismatch_loss_db_at_fkey"], rel=1e-6)
    assert secs[0]["rl_if_fixed_db"] > 100
    assert all(x["ml_alone_db"] < 1e-9 for x in secs[1:])
    assert out["section_curves"] and len(out["section_curves"][0]["ml"]) == len(out["curve_f_hz"])
