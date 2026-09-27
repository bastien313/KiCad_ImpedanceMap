"""Phase 2 : extraction et coupes sur le board d'exemple (examples/demo_impedance.kicad_pcb).

Le board est généré par examples/make_demo_board.py puis rempli par kicad-cli ; il est
versionné, ces tests n'ont donc pas besoin de KiCad.
"""

import math
import pathlib

import pytest

from impedance_map.analysis import AnalysisOptions, AnalysisRun, Engine
from impedance_map.extraction import sexpr
from impedance_map.extraction.crosssection import build_cut
from impedance_map.extraction.file_reader import read_kicad_pcb
from impedance_map.extraction.sampling import SamplingOptions, build_paths, sample_path
from impedance_map.extraction.targets import build_targets, find_pairs, z_from_netclass
from impedance_map.solver.geometry import FLOATING, GROUND, SIGNAL
from impedance_map.stackup import jlcpcb_stackup
from impedance_map.synthesis import impedance

DEMO = pathlib.Path(__file__).resolve().parents[1] / "examples" / "demo_impedance.kicad_pcb"
MM = 1e-3


@pytest.fixture(scope="module")
def bm():
    return read_kicad_pcb(DEMO)


@pytest.fixture(scope="module")
def run(bm):
    tg = build_targets(bm, nets=["MS_50", "MS_SLOT", "MS_VIA", "SL_50", "USB_P"], z_single=50, z_diff=100)
    eng = Engine(bm, bm.stackup, AnalysisOptions(workers=1, persist_cache=False))
    return eng.run(tg)


def _tr(run, label):
    return next(t for t in run.targets if t.label == label)


def test_sexpr_basic():
    t = sexpr.parse('(a (b "x y") (c 1 2) (c 3 4))')
    assert sexpr.value(t, "b") == "x y"
    assert [c[1] for c in sexpr.children(t, "c")] == ["1", "3"]


def test_read_demo(bm):
    assert bm.copper_names == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    assert not bm.unfilled_zones()
    assert {"MS_50", "USB_P", "USB_N", "SL_50", "GND"} <= set(bm.nets)
    ref = jlcpcb_stackup("JLC04161H-7628")
    assert bm.stackup.nearest_plane_distance("F.Cu") == pytest.approx(ref.nearest_plane_distance("F.Cu"))
    assert bm.netclass_of["USB_P"] == "USB_90R"
    # îlot sans net rempli sur F.Cu
    assert any(z.net == "" and "F.Cu" in z.filled for z in bm.zones)


def test_pairs_and_netclass_z(bm):
    pairs = find_pairs(bm.nets, bm)
    assert ("USB_P", "USB_N", "netclass") in pairs
    assert z_from_netclass("USB_90R") == 90
    assert z_from_netclass("90R") == 90
    assert z_from_netclass("Default") is None
    tg = build_targets(bm, nets=["USB_N"])
    assert len(tg) == 1 and tg[0].kind == "pair" and tg[0].z_target == 90


def test_sampling_corner_marked(bm):
    paths = build_paths(bm.segs_of("MS_50"))
    assert len(paths) == 1
    smp = sample_path(paths[0], SamplingOptions(step=0.5 * MM), bm.stackup.nearest_plane_distance)
    corner = [s for s in smp if s.status == "discontinuity" and "coin" in s.reason]
    assert corner and all(abs(s.pos[0] - 0.140) < 1e-3 for s in corner)
    assert paths[0].length == pytest.approx(34e-3 + math.hypot(10e-3, 10e-3), rel=1e-6)


def test_cut_contains_neighbor_and_floating(bm):
    # MS_50 en x = 121.5 mm : voisine AGGR au-dessus (0,15 mm) et îlot flottant en dessous (0,3 mm)
    xs, info = build_cut(bm, bm.stackup, (0.1215, 0.055), (0.0, 1.0), 2.0 * MM, [("MS_50", 0.0, 0.35 * MM)], "F.Cu")
    assert info.status == "ok", info.reason
    roles = {(c.net, c.role) for c in xs.conductors}
    assert ("MS_50", SIGNAL) in roles and ("AGGR", GROUND) in roles and ("", FLOATING) in roles
    assert "In1.Cu" in info.references
    # troncature : rien au-delà du plan In1 (plein sur la fenêtre)
    assert all(c.layer in ("F.Cu", "In1.Cu") for c in xs.conductors)


def test_cut_over_slot_has_no_reference(bm):
    xs, info = build_cut(bm, bm.stackup, (0.1255, 0.070), (0.0, 1.0), 2.0 * MM, [("MS_SLOT", 0.0, 0.35 * MM)], "F.Cu")
    assert info.status == "no_reference"


def test_pipeline_values(run, bm):
    st = bm.stackup
    ms = _tr(run, "MS_50")
    z_ref = impedance(st, "F.Cu", 0.35 * MM).z0
    far = [s for s in ms.samples if s.valid and s.x > 0.135 and s.y < 0.0551]
    near = [s for s in ms.samples if s.valid and 0.1155 < s.x < 0.1195]
    assert far and near
    assert all(abs(s.z / z_ref - 1) < 0.01 for s in far)          # loin de tout : = calcul canonique
    assert all(s.z < 0.95 * z_ref for s in near)                   # voisine à 0,15 mm : Z baisse
    usb = _tr(run, "USB (_P/_N)")
    zd = impedance(st, "F.Cu", 0.283 * MM, 0.200 * MM).zdiff
    assert usb.stats["z_mean"] == pytest.approx(zd, rel=0.01)
    assert usb.target["z_target"] == 90
    assert 95 < usb.stats["length_mm"] < 110
    slot = _tr(run, "MS_SLOT")
    assert slot.stats["n_no_reference"] >= 1
    assert any(125e-3 <= s.x <= 126e-3 for s in slot.samples if s.status == "no_reference")
    via = _tr(run, "MS_VIA")
    assert any("via de retour" in w for w in via.warnings)
    sl = _tr(run, "SL_50")
    assert sl.stats["z_mean"] < 50                                  # masse coplanaire In2 à 0,3 mm


def test_run_json_roundtrip(run, tmp_path):
    p = tmp_path / "run.json"
    run.to_json(p)
    r2 = AnalysisRun.from_json(p)
    assert [t.label for t in r2.targets] == [t.label for t in run.targets]
    assert r2.targets[0].stats == pytest.approx(run.targets[0].stats)
