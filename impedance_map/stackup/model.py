"""Modèle d'empilement (stackup) indépendant de KiCad.

Conventions :
  * couches listées du HAUT (F.Mask) vers le BAS (B.Mask) ;
  * épaisseurs en mètres ;
  * l'épaisseur d'un diélectrique est la distance entre les faces des cuivres qui
    l'encadrent (convention KiCad et JLCPCB : épaisseur carte = somme de toutes les couches) ;
  * masque : c1 = épaisseur sur substrat nu, c2 = épaisseur au-dessus d'une piste
    (KiCad ne donne qu'une épaisseur : c1 = c2 = épaisseur).

Repère vertical pour le solveur : y = 0 à la face inférieure du cuivre B.Cu, y croissant
vers F.Cu (voir `Stackup.vertical_layout`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

COPPER = "copper"
DIELECTRIC = "dielectric"
MASK = "mask"

CORE = "core"
PREPREG = "prepreg"


@dataclass
class StackupLayer:
    kind: str                      # COPPER / DIELECTRIC / MASK
    name: str                      # "F.Cu", "In1.Cu", "B.Mask", "diel 1"...
    thickness: float               # m
    er: float = 1.0
    loss_tangent: float = 0.0
    material: str = ""
    dielectric_type: str = ""      # CORE / PREPREG / "" (inconnu)
    mask_c2: Optional[float] = None  # MASK : épaisseur au-dessus d'une piste (défaut = thickness)

    def to_dict(self):
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, "")}


@dataclass
class Stackup:
    layers: List[StackupLayer]
    name: str = "stackup"
    source: str = "custom"         # "board", "jlcpcb:<id>", "custom:<fichier>"
    notes: str = ""

    # ------------------------------------------------------------------ requêtes
    @property
    def copper_layers(self) -> List[StackupLayer]:
        return [l for l in self.layers if l.kind == COPPER]

    @property
    def copper_names(self) -> List[str]:
        return [l.name for l in self.copper_layers]

    @property
    def n_copper(self) -> int:
        return len(self.copper_layers)

    @property
    def total_thickness(self) -> float:
        return sum(l.thickness for l in self.layers if l.kind != MASK)

    def mask(self, side: str) -> Optional[StackupLayer]:
        masks = [l for l in self.layers if l.kind == MASK]
        if not masks:
            return None
        if side == "top":
            return self.layers[0] if self.layers[0].kind == MASK else None
        return self.layers[-1] if self.layers[-1].kind == MASK else None

    def validate(self) -> List[str]:
        """Liste des problèmes (vide si OK)."""
        errs = []
        core = [l for l in self.layers if l.kind != MASK]
        if not core or core[0].kind != COPPER or core[-1].kind != COPPER:
            errs.append("Le stackup doit commencer et finir par une couche cuivre (masques exclus).")
        for a, b in zip(core, core[1:]):
            if a.kind == COPPER and b.kind == COPPER:
                errs.append(f"Deux cuivres adjacents sans diélectrique : {a.name} / {b.name}")
        for l in self.layers:
            if l.thickness < 0:
                errs.append(f"Épaisseur négative : {l.name}")
            if l.kind in (DIELECTRIC, MASK) and l.er < 1.0:
                errs.append(f"εr < 1 : {l.name}")
            if l.kind == DIELECTRIC and l.thickness <= 0:
                errs.append(f"Diélectrique d'épaisseur nulle : {l.name}")
        names = self.copper_names
        if len(set(names)) != len(names):
            errs.append("Noms de couches cuivre dupliqués")
        return errs

    # ------------------------------------------------------------------ géométrie
    def vertical_layout(self) -> Dict[str, object]:
        """Positions verticales (m), y = 0 sous B.Cu.

        Retourne {
          "copper": {nom: (y_bas, y_haut)},
          "dielectrics": [(y0, y1, er, nom, type)],
          "copper_fill_er": {nom: εr du matériau qui remplit le niveau cuivre entre les pistes},
          "top_surface": y du haut du substrat (= bas de F.Cu),
          "bottom_surface": y du bas du substrat (= haut de B.Cu),
        }
        """
        core = [l for l in self.layers if l.kind != MASK]
        y = 0.0
        copper: Dict[str, Tuple[float, float]] = {}
        diel: List[Tuple[float, float, float, str, str]] = []
        for l in reversed(core):          # cumul depuis le bas : y exact à 0 sous B.Cu
            y1 = y + l.thickness
            if l.kind == COPPER:
                copper[l.name] = (y, y1)
            else:
                diel.append((y, y1, l.er, l.name, l.dielectric_type))
            y = y1
        diel.reverse()
        copper = dict(reversed(list(copper.items())))
        fill: Dict[str, float] = {}
        for i, l in enumerate(core):
            if l.kind != COPPER or i == 0 or i == len(core) - 1:
                continue
            above, below = core[i - 1], core[i + 1]
            neigh = [n for n in (above, below) if n.kind == DIELECTRIC]
            pre = [n for n in neigh if n.dielectric_type == PREPREG]
            src = pre or neigh
            fill[l.name] = sum(n.er for n in src) / len(src) if src else 1.0
        names = self.copper_names
        return {
            "copper": copper,
            "dielectrics": diel,
            "copper_fill_er": fill,
            "top_surface": copper[names[0]][0],
            "bottom_surface": copper[names[-1]][1],
        }

    def nearest_plane_distance(self, layer: str) -> float:
        """Distance diélectrique minimale entre `layer` et un cuivre voisin (m)."""
        lay = self.vertical_layout()["copper"]
        y0, y1 = lay[layer]
        d = []
        for n, (a, b) in lay.items():
            if n == layer:
                continue
            d.append(a - y1 if a >= y1 else y0 - b)
        return min(d) if d else self.total_thickness

    # ------------------------------------------------------------------ adaptation
    def renamed_for(self, board_copper_names: List[str]) -> "Stackup":
        """Renomme les cuivres d'un préréglage selon les noms du board (même nombre de couches)."""
        if len(board_copper_names) != self.n_copper:
            raise ValueError(f"Le stackup '{self.name}' a {self.n_copper} couches cuivre, "
                             f"le board en a {len(board_copper_names)}")
        it = iter(board_copper_names)
        layers = []
        for l in self.layers:
            d = StackupLayer(**asdict(l))
            if l.kind == COPPER:
                d.name = next(it)
            layers.append(d)
        return Stackup(layers=layers, name=self.name, source=self.source, notes=self.notes)

    # ------------------------------------------------------------------ JSON
    def to_dict(self):
        return {"name": self.name, "source": self.source, "notes": self.notes,
                "units": "mm",
                "layers": [_layer_to_mm(l) for l in self.layers]}

    def to_json(self, path=None) -> str:
        s = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(s)
        return s

    @staticmethod
    def from_dict(d) -> "Stackup":
        scale = 1e-3 if d.get("units", "mm") == "mm" else 1.0
        layers = []
        for ld in d["layers"]:
            c2 = ld.get("mask_c2")
            layers.append(StackupLayer(
                kind=ld["kind"], name=ld["name"], thickness=float(ld["thickness"]) * scale,
                er=float(ld.get("er", 1.0)), loss_tangent=float(ld.get("loss_tangent", 0.0)),
                material=ld.get("material", ""), dielectric_type=ld.get("dielectric_type", ""),
                mask_c2=float(c2) * scale if c2 is not None else None))
        st = Stackup(layers=layers, name=d.get("name", "stackup"), source=d.get("source", "custom"),
                     notes=d.get("notes", ""))
        errs = st.validate()
        if errs:
            raise ValueError("Stackup invalide : " + " ; ".join(errs))
        return st

    @staticmethod
    def from_json(path) -> "Stackup":
        with open(path, "r", encoding="utf-8") as f:
            st = Stackup.from_dict(json.load(f))
        if st.source == "custom":
            st.source = f"custom:{path}"
        return st

    def describe(self) -> str:
        rows = []
        for l in self.layers:
            if l.kind == COPPER:
                rows.append(f"{l.name:<10} cuivre      {l.thickness * 1e3:7.4f} mm")
            elif l.kind == MASK:
                rows.append(f"{l.name:<10} masque      {l.thickness * 1e3:7.4f} mm  εr={l.er:.2f}")
            else:
                rows.append(f"{l.name:<10} {l.dielectric_type or 'diél.':<11} {l.thickness * 1e3:7.4f} mm  "
                            f"εr={l.er:.2f}  {l.material}")
        return f"{self.name} ({self.source}), {self.n_copper} couches, {self.total_thickness * 1e3:.3f} mm\n" + \
            "\n".join(rows)


def _layer_to_mm(l: StackupLayer):
    d = {"kind": l.kind, "name": l.name, "thickness": round(l.thickness * 1e3, 6)}
    if l.kind != COPPER:
        d["er"] = l.er
    if l.loss_tangent:
        d["loss_tangent"] = l.loss_tangent
    if l.material:
        d["material"] = l.material
    if l.dielectric_type:
        d["dielectric_type"] = l.dielectric_type
    if l.mask_c2 is not None:
        d["mask_c2"] = round(l.mask_c2 * 1e3, 6)
    return d


def default_copper_names(n: int) -> List[str]:
    return ["F.Cu"] + [f"In{i}.Cu" for i in range(1, n - 1)] + ["B.Cu"]
