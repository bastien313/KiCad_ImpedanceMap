"""Sources de stackup : board KiCad (API kipy ou fichier), JSON perso, préréglages JLCPCB."""

from __future__ import annotations

import json
import pathlib
from typing import List, Optional

from .model import COPPER, CORE, DIELECTRIC, MASK, PREPREG, Stackup, StackupLayer

DATA = pathlib.Path(__file__).with_name("data")
NM = 1e-9
MM = 1e-3


# ---------------------------------------------------------------------- préréglages JLCPCB
_PRESETS_CACHE: Optional[dict] = None


def _presets_raw() -> dict:
    global _PRESETS_CACHE
    if _PRESETS_CACHE is None:
        with open(DATA / "jlcpcb_stackups.json", "r", encoding="utf-8") as f:
            _PRESETS_CACHE = json.load(f)
    return _PRESETS_CACHE


def jlcpcb_presets(n_copper: Optional[int] = None) -> List[str]:
    """Noms des préréglages JLCPCB (filtrés par nombre de couches cuivre)."""
    out = []
    for name, d in _presets_raw()["presets"].items():
        n = sum(1 for l in d["layers"] if l["kind"] == COPPER)
        if n_copper is None or n == n_copper:
            out.append(name)
    return out


def jlcpcb_metadata() -> dict:
    raw = _presets_raw()
    return {k: v for k, v in raw.items() if k.startswith("_")}


def jlcpcb_stackup(name: str, copper_names: Optional[List[str]] = None) -> Stackup:
    d = _presets_raw()["presets"].get(name)
    if d is None:
        raise KeyError(f"Préréglage JLCPCB inconnu : {name}")
    st = Stackup.from_dict(d)
    st.source = "jlcpcb:" + name
    if copper_names:
        st = st.renamed_for(copper_names)
    else:
        from .model import default_copper_names
        st = st.renamed_for(default_copper_names(st.n_copper))
    return st


# ---------------------------------------------------------------------- kipy (API IPC)
def from_kipy(board) -> Stackup:
    """Construit le Stackup depuis `board.get_stackup()` (kipy ≥ 0.8 pour εr/masque détaillés).

    Si un champ manque (kipy ancien ou KiCad < 10.0.6), lever AttributeError : l'appelant
    peut alors se replier sur `from_kicad_pcb_file`.
    """
    from kipy.board_types import BoardLayer
    from kipy.proto.board import board_pb2

    st = board.get_stackup()
    layers: List[StackupLayer] = []
    n_diel = 0
    for L in st.layers:
        if not L.enabled:
            continue
        t = L.type
        if t == board_pb2.BSLT_COPPER:
            name = L.user_name or BoardLayer.Name(L.layer).replace("BL_", "").replace("_", ".")
            layers.append(StackupLayer(COPPER, _std_copper_name(L.layer, name), L.thickness * NM))
        elif t == board_pb2.BSLT_DIELECTRIC:
            d = L.dielectric                 # AttributeError si kipy trop ancien
            if d is None or not d.layers:
                raise AttributeError("détails diélectriques indisponibles")
            dtype = {board_pb2.BSDT_CORE: CORE, board_pb2.BSDT_PREPREG: PREPREG}.get(d.type, "")
            for sub in d.layers:
                n_diel += 1
                layers.append(StackupLayer(DIELECTRIC, f"{dtype or 'diel'} {n_diel}", sub.thickness * NM,
                                           er=sub.epsilon_r or 1.0, loss_tangent=sub.loss_tangent,
                                           material=sub.material_name, dielectric_type=dtype))
        elif t == board_pb2.BSLT_SOLDERMASK:
            sm = L.soldermask
            if sm is None:
                raise AttributeError("détails masque indisponibles")
            name = "F.Mask" if L.layer == BoardLayer.BL_F_Mask else "B.Mask"
            thick = (sm.thickness or L.thickness) * NM
            layers.append(StackupLayer(MASK, name, thick, er=sm.epsilon_r or 1.0,
                                       loss_tangent=sm.loss_tangent, material=sm.material_name))
    stack = Stackup(layers=layers, name=f"Stackup du board ({board.name})", source="board")
    errs = stack.validate()
    if errs:
        raise ValueError("Stackup du board invalide : " + " ; ".join(errs))
    return stack


def _std_copper_name(layer_enum: int, fallback: str) -> str:
    from kipy.board_types import BoardLayer
    n = BoardLayer.Name(layer_enum)          # BL_F_Cu, BL_In1_Cu, BL_B_Cu
    if n.startswith("BL_"):
        return n[3:].replace("_", ".")
    return fallback


# ---------------------------------------------------------------------- fichier .kicad_pcb
def from_kicad_pcb_tree(tree) -> Stackup:
    """Stackup depuis l'arbre S-expression d'un .kicad_pcb (section (setup (stackup ...)))."""
    from ..extraction import sexpr as sx

    setup = sx.child(tree, "setup")
    stk = sx.child(setup, "stackup") if setup is not None else None
    if stk is None:
        return _default_from_layers(tree)
    layers: List[StackupLayer] = []
    n_diel = 0
    for L in sx.children(stk, "layer"):
        name = L[1]
        typ = sx.value(L, "type", default="")
        thick = sx.fvalue(L, "thickness") * MM
        if typ == "copper":
            layers.append(StackupLayer(COPPER, name, thick))
        elif typ in ("core", "prepreg"):
            # sous-couches éventuelles : (addsublayer ...) non gérées finement -> εr principal
            n_diel += 1
            layers.append(StackupLayer(DIELECTRIC, f"{typ} {n_diel}", thick,
                                       er=sx.fvalue(L, "epsilon_r", default=4.5),
                                       loss_tangent=sx.fvalue(L, "loss_tangent", default=0.0),
                                       material=sx.value(L, "material", default="") or "",
                                       dielectric_type=typ))
        elif typ in ("Top Solder Mask", "Bottom Solder Mask"):
            layers.append(StackupLayer(MASK, "F.Mask" if typ.startswith("Top") else "B.Mask",
                                       thick if thick > 0 else 0.01 * MM,
                                       er=sx.fvalue(L, "epsilon_r", default=3.3)))
    st = Stackup(layers=layers, name="Stackup du fichier", source="board")
    errs = st.validate()
    if errs:
        raise ValueError("Stackup du fichier invalide : " + " ; ".join(errs))
    return st


def _default_from_layers(tree) -> Stackup:
    """Board sans section stackup : stackup KiCad par défaut (1,6 mm, FR4 εr 4,5)."""
    from ..extraction import sexpr as sx
    from .model import default_copper_names

    layers_node = sx.child(tree, "layers")
    n = sum(1 for L in layers_node[1:] if isinstance(L, list) and len(L) > 1 and str(L[1]).endswith(".Cu"))
    n = max(n, 2)
    cu = 0.035 * MM
    diel_t = (1.6 * MM - n * cu) / (n - 1)
    layers = [StackupLayer(MASK, "F.Mask", 0.01 * MM, er=3.3)]
    for i, name in enumerate(default_copper_names(n)):
        layers.append(StackupLayer(COPPER, name, cu))
        if i < n - 1:
            layers.append(StackupLayer(DIELECTRIC, f"diel {i + 1}", diel_t, er=4.5,
                                       dielectric_type=CORE if i % 2 else PREPREG, material="FR4"))
    layers.append(StackupLayer(MASK, "B.Mask", 0.01 * MM, er=3.3))
    return Stackup(layers=layers, name="Stackup KiCad par défaut", source="board",
                   notes="Aucune section stackup dans le fichier : valeurs KiCad par défaut.")


def load_stackup(spec: str, copper_names: Optional[List[str]] = None, board_stackup: Optional[Stackup] = None) -> Stackup:
    """Résout une spécification de stackup :

      "board"                -> board_stackup (obligatoire)
      "jlcpcb:<nom>"         -> préréglage JLCPCB (renommé selon copper_names)
      "<chemin>.json"        -> stackup perso
    """
    if spec in ("", "board"):
        if board_stackup is None:
            raise ValueError("Stackup du board indisponible")
        return board_stackup
    if spec.startswith("jlcpcb:"):
        return jlcpcb_stackup(spec.split(":", 1)[1], copper_names)
    st = Stackup.from_json(spec)
    if copper_names and st.copper_names != copper_names:
        st = st.renamed_for(copper_names)
    return st
