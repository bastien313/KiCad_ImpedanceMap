"""Pistes réelles : arcs de routage, changements de largeur, clôtures de vias, zones du même net.

Cas remontés sur un board utilisateur (ligne coplanaire 50 Ω routée en arcs, rétrécie aux pads,
bordée de vias GND et prolongée par une zone de cuivre du net) : presque tout le tracé était
marqué « discontinuité ». Board synthétique, sans KiCad.
"""

import math

import numpy as np
import pytest
from shapely.geometry import box

from impedance_map.analysis import AnalysisOptions, Engine
from impedance_map.extraction.board_model import BoardModel, PadObj, Seg, ViaObj, ZoneObj
from impedance_map.extraction.crosssection import CutOptions, build_cut
from impedance_map.extraction.sampling import SamplingOptions, build_paths, sample_path, zone_bridges
from impedance_map.extraction.targets import build_targets
from impedance_map.solver import SolveOptions, solve_cross_section
from impedance_map.solver import api as solver_api
from impedance_map.stackup import jlcpcb_stackup

MM = 1e-3
W = 0.35 * MM
R_ARC = 1.2 * MM                      # 3,4 w : arc de routage ordinaire


def _board(with_zone=True):
    st = jlcpcb_stackup("JLC04161H-7628")
    names = st.copper_names
    c = (15 * MM, 10 * MM + R_ARC)     # centre de l'arc (y vers le bas)
    a45 = math.radians(45)
    segs = [
        Seg("t1", "SIG", "F.Cu", W, (5 * MM, 10 * MM), (15 * MM, 10 * MM)),
        Seg("a1", "SIG", "F.Cu", W, (15 * MM, 10 * MM), (c[0] + R_ARC, c[1]),        # quart de cercle
            mid=(c[0] + R_ARC * math.sin(a45), c[1] - R_ARC * math.cos(a45))),
        Seg("t2", "SIG", "F.Cu", W, (c[0] + R_ARC, c[1]), (c[0] + R_ARC, 16 * MM)),
    ]
    zones = [ZoneObj("z1", "GND", "plan", ["In1.Cu"], {"In1.Cu": box(0, 0, 40 * MM, 25 * MM)})]
    x = c[0] + R_ARC
    if with_zone:
        # zone du net qui prolonge la piste (plus large : 0,6 mm) de y = 15,5 à 19 mm
        zones.append(ZoneObj("z2", "SIG", "", ["F.Cu"], {"F.Cu": box(x - 0.3 * MM, 15.5 * MM, x + 0.3 * MM, 19 * MM)}))
    vias = [ViaObj("v1", "GND", (10 * MM, 10.9 * MM), 0.6 * MM, 0.3 * MM, list(names))]
    return BoardModel("synth", st, names, segs=segs, vias=vias, zones=zones)


@pytest.fixture(scope="module")
def run():
    bm = _board()
    eng = Engine(bm, bm.stackup, AnalysisOptions(workers=1, persist_cache=False))
    return bm, eng.run(build_targets(bm, nets=["SIG"], z_single=50))


def test_routing_arc_is_computed(run):
    _, r = run
    arc = [s for s in r.targets[0].samples if s.x > 15.1 * MM and s.y < 11.1 * MM]
    assert arc and all(s.status == "ok" for s in arc), [(s.status, s.reason) for s in arc]


def test_tight_arc_still_flagged():
    seg = Seg("a", "N", "F.Cu", W, (0.0, 0.0), (2 * 0.5 * MM, 0.0), mid=(0.5 * MM, -0.5 * MM))   # R = 1,4 w
    smp = sample_path(build_paths([seg])[0], SamplingOptions(step=0.1 * MM, end_margin=1e-5), lambda L: 1e-4)
    assert any(s.reason.startswith("arc R=") for s in smp)


def _step_board(extra_pads=()):
    """Microstrip F.Cu : 0,3 mm de x = 5 à 10 mm puis 0,9 mm jusqu'à 15 mm, plan GND sur In1."""
    st = jlcpcb_stackup("JLC04161H-7628")
    segs = [Seg("n", "SIG", "F.Cu", 0.3 * MM, (5 * MM, 10 * MM), (10 * MM, 10 * MM)),
            Seg("w", "SIG", "F.Cu", 0.9 * MM, (10 * MM, 10 * MM), (15 * MM, 10 * MM))]
    zones = [ZoneObj("z1", "GND", "plan", ["In1.Cu"], {"In1.Cu": box(0, 0, 20 * MM, 20 * MM)})]
    return BoardModel("step", st, st.copper_names, segs=segs, zones=zones, pads=list(extra_pads))


def _run(bm):
    return Engine(bm, bm.stackup, AnalysisOptions(workers=1, persist_cache=False)).run(
        build_targets(bm, nets=["SIG"], z_single=50)).targets[0]


def test_width_step_decided_by_final_copper():
    tr = _run(_step_board())
    bad = [s.x for s in tr.samples if s.reason == "jonction / changement de largeur"]
    assert bad and all(9.3 * MM < x < 10.6 * MM for x in bad)
    ok = [s for s in tr.samples if s.valid]
    assert any(s.x < 9.3 * MM and s.width == pytest.approx(0.3 * MM, rel=1e-3) for s in ok)
    assert any(s.x > 10.6 * MM and s.width == pytest.approx(0.9 * MM, rel=1e-3) for s in ok)


def test_pad_flush_with_line_is_computed_wider_pad_is_not():
    # pad de 0,9 × 0,9 mm posé sur la partie large : même cuivre que la ligne -> calculé
    flush = PadObj("p1", "SIG", (12.5 * MM, 10 * MM), {"F.Cu": box(12.05 * MM, 9.55 * MM, 12.95 * MM, 10.45 * MM)})
    tr = _run(_step_board([flush]))
    on = [s for s in tr.samples if abs(s.x - 12.5 * MM) < 0.4 * MM]
    assert on and all(s.valid for s in on)
    # pad de 1,6 mm de large : dépasse de la ligne -> discontinuité (modèle localisé)
    wide = PadObj("p2", "SIG", (12.5 * MM, 10 * MM), {"F.Cu": box(12.1 * MM, 9.2 * MM, 12.9 * MM, 10.8 * MM)})
    tr = _run(_step_board([wide]))
    on = [s for s in tr.samples if abs(s.x - 12.5 * MM) < 0.3 * MM]
    assert on and all(s.status == "discontinuity" for s in on)
    assert any(s.reason == "pad / jonction" for s in on)


def test_other_net_via_is_not_a_discontinuity(run):
    bm, r = run
    near = [s for s in r.targets[0].samples if abs(s.x - 10 * MM) < 0.2 * MM]
    assert near and all(s.status == "ok" for s in near)
    assert any("vias d'autres nets" in w for w in r.targets[0].warnings)
    # le même via sur le net du signal reste une discontinuité
    bm2 = _board()
    bm2.vias[0].net = "SIG"
    xs, info = build_cut(bm2, bm2.stackup, (10 * MM, 10 * MM), (0.0, 1.0), 2 * MM, [("SIG", 0.0, W)], "F.Cu")
    assert info.status == "discontinuity" and info.reason == "via du même net"


def test_zone_bridge_extends_track(run):
    bm, r = run
    ext = zone_bridges(bm.segs_of("SIG"), lambda L: bm.net_zone_fill("SIG", L))
    assert len(ext) == 1 and ext[0].synthetic
    assert ext[0].start[1] == pytest.approx(16 * MM) and ext[0].end[1] == pytest.approx(19 * MM, abs=2e-6)
    far = [s for s in r.targets[0].samples if s.y > 16.6 * MM and s.status == "ok"]
    assert far, "le tronçon de zone doit être calculé"
    assert all(s.width == pytest.approx(0.6 * MM, rel=0.02) for s in far)
    # piste plus large -> Z plus basse que sur la piste nominale
    z_track = np.median([s.z for s in r.targets[0].samples if s.status == "ok" and s.y < 10.1 * MM])
    assert max(s.z for s in far) < z_track


def test_track_buried_in_zone_uses_copper_width(run):
    _, r = run
    buried = [s for s in r.targets[0].samples if 15.55 * MM < s.y < 16.0 * MM and s.status == "ok"]
    assert buried and all(s.width == pytest.approx(0.6 * MM, rel=0.02) for s in buried)


def test_compact_zone_is_not_a_bridge():
    bm = _board(with_zone=False)
    x = 15 * MM + R_ARC
    bm.zones.append(ZoneObj("z3", "SIG", "", ["F.Cu"], {"F.Cu": box(x - 0.4 * MM, 15.6 * MM, x + 0.4 * MM, 16.4 * MM)}))
    assert zone_bridges(bm.segs_of("SIG"), lambda L: bm.net_zone_fill("SIG", L)) == []


def test_free_width_cut():
    bm = _board()
    x = 15 * MM + R_ARC
    _, info = build_cut(bm, bm.stackup, (x, 17 * MM), (1.0, 0.0), 3 * MM, [("SIG", 0.0, W)], "F.Cu")
    assert info.status == "discontinuity"          # sans free_width : « pad / jonction »
    _, info = build_cut(bm, bm.stackup, (x, 17 * MM), (1.0, 0.0), 3 * MM, [("SIG", 0.0, W)], "F.Cu",
                        CutOptions(free_width=True))
    assert info.status == "ok" and info.signal_widths[0] == pytest.approx(0.6 * MM, rel=1e-3)


def test_solver_falls_back_when_finest_grid_too_large(monkeypatch):
    bm = _board()
    xs, _ = build_cut(bm, bm.stackup, (8 * MM, 10 * MM), (0.0, 1.0), 2 * MM, [("SIG", 0.0, W)], "F.Cu")
    ref, _ = solve_cross_section(xs, SolveOptions(levels=(4, 5)))
    real = solver_api._capacitances

    def limited(xs_, level, keep_field):
        if level >= 6:
            raise MemoryError("trop grand")
        return real(xs_, level, keep_field)

    monkeypatch.setattr(solver_api, "_capacitances", limited)
    res, _ = solve_cross_section(xs, SolveOptions(mode="precise"))
    assert res.z0 == pytest.approx(ref.z0, rel=1e-9)


def test_map_shows_computed_copper(run):
    """La carte dessine le cuivre final et des bandes à la largeur mesurée dans chaque coupe."""
    from impedance_map.viz.board_map import board_svg

    bm, r = run
    svg = board_svg(r, bm)
    assert 'class="cu" data-L="F.Cu"' in svg and 'class="cu" data-L="In1.Cu"' in svg   # cuivre final par couche
    assert 'data-L="In1.Cu" style="display:none"' in svg                               # plan masqué par défaut
    assert 'stroke-width="0.600"' in svg and 'stroke-width="0.350"' in svg             # zone 0,6 / piste 0,35
    assert 'data-c="' in svg and 'id="bm-cut"' in svg                                  # ligne de coupe au survol
    runs, i, smp = 0, 0, r.targets[0].samples
    while i < len(smp):                                 # un repère par tronçon non calculé consécutif
        if not smp[i].valid:
            runs += 1
            while i + 1 < len(smp) and not smp[i + 1].valid:
                i += 1
        i += 1
    assert svg.count('class="mk ') == runs
