import pytest

from impedance_map.stackup import (Stackup, jlcpcb_metadata, jlcpcb_presets, jlcpcb_stackup, load_stackup)

MM = 1e-3


def test_jlcpcb_presets_available():
    four = jlcpcb_presets(4)
    six = jlcpcb_presets(6)
    two = jlcpcb_presets(2)
    assert "JLC04161H-7628" in four and "JLC04161H-3313" in four
    assert "JLC06161H-2116" in six
    assert len(two) >= 5
    meta = jlcpcb_metadata()["_parameters"]
    assert meta["prepreg_dk"]["7628"] == 4.4 and meta["core_dk"] == 4.6


@pytest.mark.parametrize("name", jlcpcb_presets())
def test_every_preset_valid(name):
    st = jlcpcb_stackup(name)
    assert st.validate() == []
    assert 1.4e-3 < st.total_thickness < 2.1e-3 or "2L" in name
    assert st.copper_names[0] == "F.Cu" and st.copper_names[-1] == "B.Cu"
    assert st.mask("top").er == pytest.approx(3.8)


def test_jlc_7628_values():
    st = jlcpcb_stackup("JLC04161H-7628")
    lay = st.vertical_layout()
    f0, f1 = lay["copper"]["F.Cu"]
    i0, i1 = lay["copper"]["In1.Cu"]
    assert f1 - f0 == pytest.approx(0.035 * MM)
    assert f0 - i1 == pytest.approx(0.2104 * MM)          # prepreg 7628
    assert st.nearest_plane_distance("F.Cu") == pytest.approx(0.2104 * MM)
    assert lay["copper"]["B.Cu"][0] == 0.0
    assert lay["copper_fill_er"]["In1.Cu"] == pytest.approx(4.4)   # résine du prepreg
    assert st.mask("top").mask_c2 == pytest.approx(0.6 * 25.4e-6)


def test_json_roundtrip(tmp_path):
    st = jlcpcb_stackup("JLC06161H-3313")
    p = tmp_path / "s.json"
    st.to_json(p)
    st2 = Stackup.from_json(p)
    assert [(l.kind, l.name, round(l.thickness, 12), l.er) for l in st.layers] == \
           [(l.kind, l.name, round(l.thickness, 12), l.er) for l in st2.layers]
    assert load_stackup(str(p)).n_copper == 6


def test_renamed_for_and_mismatch():
    st = jlcpcb_stackup("JLC04161H-7628", ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
    assert st.copper_names == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    with pytest.raises(ValueError):
        st.renamed_for(["F.Cu", "B.Cu"])


def test_invalid_stackup_rejected():
    d = {"units": "mm", "layers": [{"kind": "copper", "name": "F.Cu", "thickness": 0.035},
                                   {"kind": "copper", "name": "B.Cu", "thickness": 0.035}]}
    with pytest.raises(ValueError):
        Stackup.from_dict(d)
