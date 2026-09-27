"""Phase 3 : plan d'overlay, application/effacement sur un faux board kipy, rapport HTML."""

import pathlib
import uuid

import pytest

from impedance_map.analysis import AnalysisOptions, Engine
from impedance_map.extraction.file_reader import read_kicad_pcb
from impedance_map.extraction.targets import build_targets
from impedance_map.viz.overlay_plan import OverlayOptions, plan_overlay
from impedance_map.viz.report import build_report

DEMO = pathlib.Path(__file__).resolve().parents[1] / "examples" / "demo_impedance.kicad_pcb"


@pytest.fixture(scope="module")
def ctx():
    bm = read_kicad_pcb(DEMO)
    tg = build_targets(bm, nets=["MS_50", "MS_SLOT", "USB_P"])
    eng = Engine(bm, bm.stackup, AnalysisOptions(workers=1, persist_cache=False))
    return bm, eng, eng.run(tg)


def test_plan_layers_and_labels(ctx):
    bm, eng, run = ctx
    opt = OverlayOptions()
    plan = plan_overlay(run, opt)
    layers = {s.layer for s in plan.segs}
    assert opt.layer_ok in layers and opt.layer_annot in layers
    assert plan.counts.get("ok", 0) > 0
    assert any(t.text.startswith("Zdiff") for t in plan.texts)
    assert any(t.text.endswith("Ω") and not t.text.startswith("Zdiff") for t in plan.texts)
    assert any(t.text == "perte réf." for t in plan.texts)           # fente sous MS_SLOT
    assert plan.circles                                                # marqueurs de discontinuité
    # les tronçons suivent la piste : tous proches d'un échantillon
    assert all(90e-3 < s.a[0] < 190e-3 for s in plan.segs)


def test_plan_classification_with_tight_target(ctx):
    bm, eng, run = ctx
    for tr in run.targets:                     # cible irréaliste -> tout « trop bas »
        tr.target = dict(tr.target, z_target=200.0)
    plan = plan_overlay(run, OverlayOptions())
    assert plan.counts.get("low", 0) > 0 and plan.counts.get("ok", 0) == 0
    for tr in run.targets:
        tr.target = dict(tr.target, z_target=90.0 if tr.target["kind"] == "pair" else 50.0)


class FakeBoard:
    """Imite la surface kipy utilisée par overlay_kicad (création, groupes, suppression)."""

    def __init__(self, enabled=("User.1", "User.2", "User.3", "User.4")):
        from impedance_map.extraction.kipy_reader import layer_enum
        self.enabled = [layer_enum(l) for l in enabled]
        self.items = {}
        self.groups = []
        self.commits = []
        self.open_commit = None

    def get_enabled_layers(self):
        return self.enabled

    def begin_commit(self):
        self.open_commit = object()
        self.new_in_commit = set()
        self.groups_in_commit = []
        return self.open_commit

    def push_commit(self, c, msg=""):
        assert c is self.open_commit
        # comportement observé sur KiCad 10.0.6 : un groupe dont les membres sont créés dans le
        # même commit est abandonné sans erreur
        for g in self.groups_in_commit:
            if any(k.value in self.new_in_commit for k in g._proto.items):
                self.groups.remove(g)
        self.commits.append(msg)
        self.open_commit = None

    def drop_commit(self, c):
        self.open_commit = None

    def create_items(self, items):
        out = []
        for it in items:
            it._proto.id.value = str(uuid.uuid4())
            if type(it).__name__ == "Group":
                self.groups.append(it)
                self.groups_in_commit.append(it)
            else:
                self.items[it._proto.id.value] = it
                self.new_in_commit.add(it._proto.id.value)
            out.append(it)
        return out

    def get_groups(self):
        return list(self.groups)

    def get_items_by_id(self, ids):
        return [self.items[i.value] for i in ids if i.value in self.items]

    def remove_items_by_id(self, ids):
        vals = {i.value for i in ids}
        for v in vals:
            self.items.pop(v, None)
        self.groups = [g for g in self.groups if g.id.value not in vals]


def test_apply_and_clear_on_fake_board(ctx):
    pytest.importorskip("kipy")
    from impedance_map.viz.overlay_kicad import apply_overlay, clear_overlay, find_overlay
    bm, eng, run = ctx
    fb = FakeBoard()
    unrelated = "existing-item"
    fb.items[unrelated] = object()
    plan = plan_overlay(run)
    r = apply_overlay(fb, plan)
    assert r["n_items"] == plan.n_items
    assert len(fb.commits) == 2 and "overlay" in fb.commits[0]          # objets : UN commit ; groupe : un 2e
    assert r["group"] is not None
    groups, ids = find_overlay(fb)
    assert len(groups) == 1 and groups[0].name.startswith("ImpedanceMap")
    assert len(ids) == plan.n_items
    # refus de la confirmation : rien n'est supprimé
    assert clear_overlay(fb, lambda n, g: False) == 0
    assert len(fb.items) == plan.n_items + 1
    asked = {}

    def confirm(n, g):
        asked["n"], asked["g"] = n, g
        return True

    assert clear_overlay(fb, confirm) == plan.n_items
    assert asked == {"n": plan.n_items, "g": 1}
    assert list(fb.items) == [unrelated]                                # seul l'objet étranger reste
    assert not fb.groups


def test_layer_fallback_when_user_layers_disabled(ctx):
    pytest.importorskip("kipy")
    from impedance_map.viz.overlay_kicad import resolve_layers
    fb = FakeBoard(enabled=("User.1", "Eco1.User", "Eco2.User", "Cmts.User"))
    mapping, notes = resolve_layers(fb, OverlayOptions())
    assert len(set(mapping.values())) == 4 and len(notes) == 3


def test_report_html(ctx, tmp_path):
    bm, eng, run = ctx
    p = tmp_path / "r.html"
    doc = build_report(run, eng, bm, path=p)
    assert p.exists() and doc.count("data:image/png;base64,") >= 2 * len(run.targets)
    assert "Hypothèses et limites" in doc and "MS_SLOT" in doc and "Zdiff" in doc
    # autonome : aucune ressource externe
    assert "<script src" not in doc and "<link" not in doc and "src='http" not in doc
    assert "bm-svg" in doc and 'class="zs"' in doc            # carte interactive intégrée


class NoGroupBoard(FakeBoard):
    def create_items(self, items):
        if any(type(i).__name__ == "Group" for i in items):
            raise RuntimeError("groupes non supportés")
        return super().create_items(items)


def test_group_failure_falls_back_to_id_file(ctx, tmp_path):
    pytest.importorskip("kipy")
    from impedance_map.viz.overlay_kicad import apply_overlay, clear_overlay
    bm, eng, run = ctx
    fb = NoGroupBoard()
    fb.items["foreign"] = object()
    store = tmp_path / "ids.json"
    plan = plan_overlay(run)
    r = apply_overlay(fb, plan, id_store=store)
    assert r["group"] is None and r["n_items"] == plan.n_items and store.exists()
    assert len(fb.commits) == 1                                          # le commit du groupe est abandonné
    assert clear_overlay(fb, lambda n, g: n == plan.n_items and g == 0, id_store=store) == plan.n_items
    assert list(fb.items) == ["foreign"] and not store.exists()
