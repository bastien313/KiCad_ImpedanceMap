"""Modèles localisés de l'intégrité du signal : primitives (formules exactes) et détection sur le
board examples/demo_discontinuites.kicad_pcb (pad, stub de piste, via + stub de via, fente, paire ESD)."""

import math
import pathlib

import numpy as np
import pytest

from impedance_map.analysis import AnalysisOptions, Engine
from impedance_map.analysis.signal import PRESETS, analyze_run, cascade
from impedance_map.extraction.file_reader import read_kicad_pcb
from impedance_map.extraction.targets import build_targets

DEMO = pathlib.Path(__file__).resolve().parents[1] / "examples" / "demo_discontinuites.kicad_pcb"
C0 = 299_792_458.0
Z0 = 50.0


# ------------------------------------------------------------------ primitives (formules exactes)
def test_shunt_capacitance_exact():
    f = np.array([1e9, 5e9])
    C = 0.5e-12
    s11, s21 = cascade([("C", C)], Z0, f)
    w = 2 * np.pi * f
    assert np.allclose(s11, -1j * w * C * Z0 / (2 + 1j * w * C * Z0))
    assert np.allclose(np.abs(s11) ** 2 + np.abs(s21) ** 2, 1.0)


def test_series_inductance_exact():
    f = np.array([1e9, 5e9])
    L = 3e-9
    s11, _ = cascade([("L", L)], Z0, f)
    w = 2 * np.pi * f
    assert np.allclose(s11, 1j * w * L / (2 * Z0 + 1j * w * L))


def test_open_stub_quarter_wave_and_low_frequency():
    e, l = 3.3, 8e-3
    fres = C0 / (4 * l * math.sqrt(e))
    s11, s21 = cascade([("S", [(Z0, e, l)])], Z0, np.array([fres, fres / 1000]))
    assert abs(s21[0]) < 1e-6 and abs(s11[0]) == pytest.approx(1.0, abs=1e-6)   # court-circuit à f0
    # basse fréquence : stub ouvert ≈ capacité l·√e/(c·Z)
    C = l * math.sqrt(e) / (C0 * Z0)
    ref, _ = cascade([("C", C)], Z0, np.array([fres / 1000]))
    assert abs(s11[1]) == pytest.approx(abs(ref[0]), rel=1e-3)


# ------------------------------------------------------------------ détection sur le board
@pytest.fixture(scope="module")
def run():
    bm = read_kicad_pcb(DEMO)
    r = Engine(bm, bm.stackup, AnalysisOptions(workers=1, persist_cache=False)).run(build_targets(bm, nets=bm.nets))
    analyze_run(r, PRESETS["USB 2.0 High-Speed (480 Mb/s)"])
    return r


def _t(run, label):
    return next(t for t in run.targets if t.label == label)


def test_pad_on_path_modeled(run):
    t = _t(run, "MS_PAD")
    assert t.stats["z_mean"] == pytest.approx(50, abs=1)             # la carte n'affiche pas le pad
    pads = t.signal["pads"]
    assert len(pads) == 1 and "ΔC" in pads[0]["info"]
    assert t.signal["tdr_z_min"] < 49.5                                  # creux capacitif visible
    assert t.signal["tdr_peak_reflection_pct"] > t.signal["line_only"]["tdr_peak_reflection_pct"] + 1


def test_trace_stub_detected_without_double_count(run):
    t = _t(run, "MS_STUB")
    stubs = [e for e in t.elements if e["type"] == "stub"]
    assert len(stubs) == 1 and stubs[0]["length_mm"] == pytest.approx(8.0, abs=0.6)
    assert stubs[0]["s"] == pytest.approx(15e-3, abs=0.3e-3)
    assert not t.signal["pads"]                                          # le T n'est pas compté comme pad
    info = next(e["info"] for e in t.signal["elements"] if e["type"] == "stub")
    f = float(info.split("≈")[-1].split("GHz")[0].replace(",", "."))
    assert 4.5 < f < 5.6                                                 # c / (4 · 8 mm · √εeff)
    assert t.signal["tdr_z_min"] < 46


def test_via_with_stub_and_return_via(run):
    t = _t(run, "SL_VIA")
    v = next(e for e in t.elements if e["type"] == "via")
    assert v["return_via"] and v["extra_L"] == 0.0
    assert v["D2"] == pytest.approx(1.2e-3, rel=0.05)                    # antipad = pad 0,6 + 2 × 0,3 de dégagement
    assert v["z_via"] == pytest.approx(60 / math.sqrt(v["eps"]) * math.log(v["D2"] / v["drill"]), rel=1e-9)
    assert len(v["stub_mm"]) == 1 and 0.2 < v["stub_mm"][0] < 0.3        # In2 -> B.Cu


def test_slot_measured(run):
    t = _t(run, "MS_SLOT8")
    sl = next(e for e in t.elements if e["type"] == "slot")
    assert sl["D_mm"] == pytest.approx(8.0, abs=0.3) and sl["W_mm"] == pytest.approx(1.0, abs=0.2)
    assert sl["L_nH"] == pytest.approx(0.2 * sl["D_mm"] * math.log(sl["D_mm"] / sl["W_mm"]), rel=1e-9)
    assert t.signal["tdr_peak_reflection_pct"] > 5 > t.signal["line_only"]["tdr_peak_reflection_pct"]


def test_pair_pads_differential(run):
    t = _t(run, "ESD (_P/_N)")
    assert t.target["z_target"] == 90
    assert len(t.signal["pads"]) == 1 and "mode impair" in t.signal["pads"][0]["info"]
    assert t.signal["tdr_z_min"] < 90
