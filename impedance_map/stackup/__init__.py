"""Stackup : modèle, préréglages JLCPCB, lecture depuis KiCad, JSON perso."""

from .model import COPPER, CORE, DIELECTRIC, MASK, PREPREG, Stackup, StackupLayer, default_copper_names
from .sources import (from_kicad_pcb_tree, from_kipy, jlcpcb_metadata, jlcpcb_presets, jlcpcb_stackup,
                      load_stackup)

__all__ = ["COPPER", "CORE", "DIELECTRIC", "MASK", "PREPREG", "Stackup", "StackupLayer",
           "default_copper_names", "from_kicad_pcb_tree", "from_kipy", "jlcpcb_metadata",
           "jlcpcb_presets", "jlcpcb_stackup", "load_stackup"]
