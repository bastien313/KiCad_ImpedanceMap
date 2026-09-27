"""Description géométrique d'une coupe transverse 2D (entrée du solveur).

Repère : x horizontal le long de la coupe, y vertical vers le haut (face F.Cu en haut).
Unités : mètres.

Une coupe contient :
  * des tranches diélectriques horizontales (`Slab`) couvrant l'épaisseur du substrat,
    y compris les niveaux de cuivre internes (remplis par la résine du prepreg) ;
  * des conducteurs (`Conductor`), trapèzes à bases horizontales (rectangle si
    x0t == x0b et x1t == x1b), d'épaisseur éventuellement nulle ;
  * un masque de soudure conforme optionnel sur chaque face externe (`MaskSpec`) ;
  * de l'air partout ailleurs.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

SIGNAL = "signal"      # conducteur actif (potentiel imposé, une colonne de la matrice C)
GROUND = "ground"      # conducteur au repos, 0 V (référence, lignes voisines)
FLOATING = "floating"  # cuivre sans net : potentiel inconnu, charge nette nulle


@dataclass
class Slab:
    """Tranche diélectrique homogène entre y0 et y1 (y0 < y1)."""
    y0: float
    y1: float
    er: float
    name: str = ""

    @property
    def thickness(self) -> float:
        return self.y1 - self.y0


@dataclass
class Conductor:
    """Conducteur trapézoïdal à bases horizontales.

    (x0b, x1b) : abscisses à y0 (base inférieure) ; (x0t, x1t) : abscisses à y1.
    Si y1 == y0, conducteur d'épaisseur nulle (segment).
    role : SIGNAL / GROUND / FLOATING.
    signal_index : indice du conducteur actif (0, 1) si role == SIGNAL.
    float_group : les conducteurs flottants de même groupe sont un seul corps
                  (même îlot coupé plusieurs fois).
    """
    x0b: float
    x1b: float
    y0: float
    y1: float
    role: str = GROUND
    signal_index: int = -1
    x0t: Optional[float] = None
    x1t: Optional[float] = None
    name: str = ""
    net: str = ""
    layer: str = ""
    float_group: int = -1

    def __post_init__(self):
        if self.x0t is None:
            self.x0t = self.x0b
        if self.x1t is None:
            self.x1t = self.x1b
        if self.y1 < self.y0:
            raise ValueError("Conductor: y1 < y0")
        if self.x1b < self.x0b or self.x1t < self.x0t:
            raise ValueError("Conductor: x1 < x0")

    @property
    def xmin(self) -> float:
        return min(self.x0b, self.x0t)

    @property
    def xmax(self) -> float:
        return max(self.x1b, self.x1t)

    @property
    def width(self) -> float:
        return max(self.x1b - self.x0b, self.x1t - self.x0t)

    @property
    def is_rect(self) -> bool:
        return self.x0b == self.x0t and self.x1b == self.x1t

    def x_bounds_at(self, y):
        """Abscisses gauche/droite à la hauteur y (interpolation linéaire)."""
        if self.y1 == self.y0:
            return self.xmin, self.xmax
        t = (y - self.y0) / (self.y1 - self.y0)
        return (self.x0b + t * (self.x0t - self.x0b), self.x1b + t * (self.x1t - self.x1b))


@dataclass
class MaskSpec:
    """Masque de soudure conforme sur une face externe.

    side : "top" (le masque s'étend vers +y depuis y_surface) ou "bottom".
    c1 : épaisseur au-dessus du substrat nu.
    c2 : épaisseur au-dessus (et sur les flancs) d'un conducteur posé sur la surface.
    """
    side: str
    y_surface: float
    c1: float
    c2: float
    er: float


@dataclass
class CrossSection:
    """Coupe transverse complète (entrée du solveur)."""
    slabs: List[Slab]
    conductors: List[Conductor]
    x_min: float
    x_max: float
    masks: List[MaskSpec] = field(default_factory=list)
    air_above: Optional[float] = None   # hauteur d'air au-dessus du haut du substrat/masque
    air_below: Optional[float] = None   # hauteur d'air sous le bas du substrat/masque

    # ------------------------------------------------------------------ utilitaires
    @property
    def signals(self) -> List[Conductor]:
        s = [c for c in self.conductors if c.role == SIGNAL]
        return sorted(s, key=lambda c: c.signal_index)

    @property
    def n_signals(self) -> int:
        return len({c.signal_index for c in self.conductors if c.role == SIGNAL})

    def substrate_bounds(self) -> Tuple[float, float]:
        if not self.slabs:
            ys = [c.y0 for c in self.conductors] + [c.y1 for c in self.conductors]
            return min(ys), max(ys)
        return min(s.y0 for s in self.slabs), max(s.y1 for s in self.slabs)

    def content_bounds_y(self) -> Tuple[float, float]:
        y0, y1 = self.substrate_bounds()
        for c in self.conductors:
            y0 = min(y0, c.y0)
            y1 = max(y1, c.y1)
        for m in self.masks:
            if m.side == "top":
                y1 = max(y1, m.y_surface + m.c1)
                for c in self.conductors:
                    if abs(c.y0 - m.y_surface) < 1e-12:
                        y1 = max(y1, c.y1 + m.c2)
            else:
                y0 = min(y0, m.y_surface - m.c1)
                for c in self.conductors:
                    if abs(c.y1 - m.y_surface) < 1e-12:
                        y0 = min(y0, c.y0 - m.c2)
        return y0, y1

    def feature_size(self) -> float:
        """Plus petite dimension caractéristique autour des conducteurs actifs (m)."""
        sig = self.signals or self.conductors
        sizes = [c.width for c in sig if c.width > 0]
        # épaisseurs des diélectriques adjacents aux signaux
        for c in sig:
            for s in self.slabs:
                if s.thickness > 0 and (abs(s.y1 - c.y0) < 1e-12 or abs(s.y0 - c.y1) < 1e-12
                                        or (s.y0 <= c.y0 <= s.y1) or (s.y0 <= c.y1 <= s.y1)):
                    sizes.append(s.thickness)
        # écartement horizontal entre un signal et ses voisins sur la même hauteur
        for c in sig:
            for o in self.conductors:
                if o is c:
                    continue
                if o.y1 < c.y0 or o.y0 > c.y1:
                    continue
                gap = max(o.xmin - c.xmax, c.xmin - o.xmax)
                if gap > 0:
                    sizes.append(gap)
        return min(sizes) if sizes else (self.x_max - self.x_min) / 50.0

    def quantized_key(self, quantum: float = 1e-7) -> str:
        """Empreinte (hash) de la géométrie quantifiée, pour le cache.

        Toute la géométrie est ramenée à un repère centré sur le 1er signal puis
        arrondie à `quantum` (défaut 0,1 µm). Deux coupes identiques à l'arrondi
        près ont la même clé.
        """
        sig = self.signals
        xref = 0.5 * (sig[0].xmin + sig[0].xmax) if sig else 0.5 * (self.x_min + self.x_max)

        def q(v):
            return int(round((v - xref) / quantum))

        def qy(v):
            return int(round(v / quantum))

        def qe(v):
            return int(round(v * 1e4))

        h = hashlib.sha1()
        h.update(struct.pack("<qq", q(self.x_min), q(self.x_max)))
        for s in sorted(self.slabs, key=lambda s: s.y0):
            h.update(struct.pack("<qqq", qy(s.y0), qy(s.y1), qe(s.er)))
        conds = sorted(self.conductors, key=lambda c: (c.y0, c.x0b, c.x1b, c.role))
        for c in conds:
            h.update(struct.pack("<qqqqqqq", q(c.x0b), q(c.x1b), q(c.x0t), q(c.x1t),
                                 qy(c.y0), qy(c.y1), c.signal_index))
            h.update(c.role.encode())
            h.update(struct.pack("<q", c.float_group))
        for m in sorted(self.masks, key=lambda m: m.side):
            h.update(m.side.encode())
            h.update(struct.pack("<qqqq", qy(m.y_surface), qy(m.c1), qy(m.c2), qe(m.er)))
        h.update(struct.pack("<qq", qy(self.air_above or 0), qy(self.air_below or 0)))
        return h.hexdigest()

    def with_vacuum(self) -> "CrossSection":
        """Même géométrie avec tous les εr = 1 (pour C0)."""
        return replace(
            self,
            slabs=[replace(s, er=1.0) for s in self.slabs],
            masks=[replace(m, er=1.0) for m in self.masks],
        )
