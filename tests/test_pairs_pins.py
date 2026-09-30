"""Paires différentielles et broches traversantes (connecteur) : cas remontés sur un board utilisateur
(USB-C : D− change de couche par une broche du connecteur, D+ plus long de 1,35 mm).
Boards synthétiques, sans KiCad."""

import math

import pytest
from shapely.geometry import Point, box

from impedance_map.analysis import AnalysisOptions, Engine
from impedance_map.analysis.signal import PRESETS, analyze_run
from impedance_map.extraction.board_model import BoardModel, PadObj, Seg, ZoneObj
from impedance_map.extraction.targets import build_targets
from impedance_map.stackup import jlcpcb_stackup
from impedance_map.viz.board_map import board_svg

MM = 1e-3
W, GAP = 0.2 * MM, 0.15 * MM
C = 299_792_458.0


def _pin(ref, net, x, y, st, side="F"):
    ring = Point(x, y).buffer(0.55 * MM)
    return PadObj(ref, net, (x, y), {L: ring for L in st.copper_names}, drill=0.65 * MM, ref=ref, side=side)


def _pair_board(extra_n=1.0 * MM):
    """Paire B.Cu le long de x (y = 10 et 10,35 mm), N plus court de `extra_n` ; plan GND sur In2.
    Le brin N finit sur une broche traversante et continue en F.Cu (changement de couche par la broche)."""
    st = jlcpcb_stackup("JLC04161H-7628")
    yp, yn = 10 * MM, 10 * MM + W + GAP
    x_end = 20 * MM
    segs = [Seg("p", "D_P", "B.Cu", W, (5 * MM, yp), (x_end, yp)),
            Seg("n", "D_N", "B.Cu", W, (5 * MM + extra_n, yn), (x_end, yn)),
            Seg("nf", "D_N", "F.Cu", W, (x_end, yn), (x_end + 2 * MM, yn))]
    pads = [_pin("J1.A7", "D_N", x_end, yn, st), _pin("J1.A6", "D_P", x_end, yp, st)]
    zones = [ZoneObj("g", "GND", "plan", ["In2.Cu"], {"In2.Cu": box(0, 0, 30 * MM, 20 * MM)})]
    return BoardModel("pair", st, st.copper_names, segs=segs, pads=pads, zones=zones)


@pytest.fixture(scope="module")
def run():
    bm = _pair_board()
    r = Engine(bm, bm.stackup, AnalysisOptions(workers=1, persist_cache=False)).run(
        build_targets(bm, nets=["D_P"], z_diff=90))
    analyze_run(r, PRESETS["USB 2.0 High-Speed (480 Mb/s)"])
    return bm, r


def test_skew_from_lengths(run):
    _, r = run
    p = r.targets[0].pair
    # P : 15 mm ; N : 14 mm en B.Cu + 2 mm en F.Cu après la broche = 16 mm
    assert p["len_p_mm"] == pytest.approx(15.0) and p["len_n_mm"] == pytest.approx(16.0)
    assert p["delta_mm"] == pytest.approx(-1.0, abs=1e-6)
    assert p["skew_ps"] == pytest.approx(-1e-3 * math.sqrt(p["eps_eff"]) / C * 1e12, rel=1e-9)
    assert r.targets[0].signal["skew_ps"] == pytest.approx(p["skew_ps"])
    assert r.targets[0].signal["mode_conversion_db_at_fkey"] < -40        # 6 ps à 240 MHz : négligeable


def test_through_pins_become_barrels(run):
    _, r = run
    vias = [e for e in r.targets[0].elements if e["type"] == "via"]
    # D+ finit sur J1.A6 (composant côté F, piste en B) : fût en série ; D− passe B→F par J1.A7 : même
    # transition au même endroit -> un seul élément symétrique
    assert len(vias) == 1
    v = vias[0]
    assert v["strand"] == "PN" and "J1.A6" in v["what"] and "J1.A7" in v["what"]
    assert v["layers"] == "B.Cu→F.Cu" and v["h_used"] > 1.0 * MM and v.get("terminal")


def test_single_strand_pin_is_asymmetric():
    bm = _pair_board()
    bm.pads = [p for p in bm.pads if p.ref != "J1.A6"]          # D+ sans broche : seul D− traverse
    r = Engine(bm, bm.stackup, AnalysisOptions(workers=1, persist_cache=False)).run(
        build_targets(bm, nets=["D_P"], z_diff=90))
    vias = [e for e in r.targets[0].elements if e["type"] == "via"]
    assert len(vias) == 1 and vias[0]["strand"] == "N"
    assert any("asymétrique" in n for n in r.targets[0].element_notes)


def test_map_draws_n_strand_where_it_is(run):
    _, r = run
    svg = board_svg(r, run[0])
    yn = (10 * MM + W + GAP) * 1e3
    # des bandes du brin N à y = 10,35 mm (et non à une position déduite de P par symétrie)
    assert f" {yn:.3f}L" in svg or f" {yn:.3f}\"" in svg
