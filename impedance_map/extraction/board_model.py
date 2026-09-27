"""Modèle de board neutre (indépendant de kipy), géométrie cuivre en shapely.

Repère : coordonnées KiCad (x vers la droite, y vers le BAS), en mètres.
Construit par `kipy_reader.read_board` (KiCad en direct) ou `file_reader.read_kicad_pcb`
(fichier .kicad_pcb, hors ligne). Tout le reste du pipeline (coupes, analyse,
rapport) ne dépend que de ce modèle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from ..stackup.model import Stackup

Pt = Tuple[float, float]
ARC_SEG_ANGLE = math.radians(5.0)   # discrétisation des arcs


def arc_center(a: Pt, m: Pt, b: Pt) -> Optional[Tuple[Pt, float]]:
    ax, ay = a
    bx, by = m
    cx, cy = b
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-24:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def arc_points(a: Pt, m: Pt, b: Pt, max_angle: float = ARC_SEG_ANGLE) -> List[Pt]:
    """Discrétise l'arc passant par a, m, b (dans l'ordre)."""
    cr = arc_center(a, m, b)
    if cr is None:
        return [a, b]
    (cx, cy), r = cr
    t0 = math.atan2(a[1] - cy, a[0] - cx)
    tm = math.atan2(m[1] - cy, m[0] - cx)
    t1 = math.atan2(b[1] - cy, b[0] - cx)
    # sens : celui qui passe par m
    def norm(t):
        return t % (2 * math.pi)
    ccw_span = norm(t1 - t0)
    ccw_mid = norm(tm - t0)
    if ccw_mid <= ccw_span:
        span = ccw_span
    else:
        span = ccw_span - 2 * math.pi
    n = max(2, int(math.ceil(abs(span) / max_angle)))
    return [(cx + r * math.cos(t0 + span * i / n), cy + r * math.sin(t0 + span * i / n)) for i in range(n + 1)]


@dataclass
class Seg:
    """Piste droite ou arc."""
    uid: str
    net: str
    layer: str
    width: float
    start: Pt
    end: Pt
    mid: Optional[Pt] = None          # arc si non None

    @property
    def is_arc(self) -> bool:
        return self.mid is not None

    def points(self) -> List[Pt]:
        if self.mid is None:
            return [self.start, self.end]
        return arc_points(self.start, self.mid, self.end)

    @property
    def length(self) -> float:
        p = np.array(self.points())
        return float(np.sum(np.hypot(*np.diff(p, axis=0).T)))

    @property
    def radius(self) -> float:
        if self.mid is None:
            return math.inf
        cr = arc_center(self.start, self.mid, self.end)
        return cr[1] if cr else math.inf

    def geometry(self):
        pts = self.points()
        if pts[0] == pts[-1] and len(pts) == 2:
            return Point(pts[0]).buffer(self.width / 2, quad_segs=8)
        return LineString(pts).buffer(self.width / 2, quad_segs=8)


@dataclass
class ViaObj:
    uid: str
    net: str
    pos: Pt
    diameter: float
    drill: float
    layers: List[str]                 # couches cuivre traversées (ordre haut -> bas)


@dataclass
class PadObj:
    uid: str
    net: str
    pos: Pt
    shapes: Dict[str, object]         # couche -> géométrie shapely
    drill: float = 0.0
    ref: str = ""

    @property
    def is_through(self) -> bool:
        return self.drill > 0


@dataclass
class ZoneObj:
    uid: str
    net: str
    name: str
    layers: List[str]
    filled: Dict[str, object]         # couche -> MultiPolygon rempli
    is_filled: bool = True


@dataclass
class NetClassInfo:
    name: str
    track_width: Optional[float] = None
    dp_width: Optional[float] = None
    dp_gap: Optional[float] = None


@dataclass
class CutInterval:
    u0: float
    u1: float
    net: str
    layer: str
    part: int                          # identifiant du corps cuivre (îlot)
    kind: str = "copper"

    @property
    def width(self) -> float:
        return self.u1 - self.u0

    @property
    def center(self) -> float:
        return 0.5 * (self.u0 + self.u1)


class LayerCopper:
    """Cuivre d'une couche : union par net, découpée en corps connexes, indexée (STRtree)."""

    def __init__(self, layer: str, geoms_by_net: Dict[str, list], part_offset: int = 0):
        self.layer = layer
        self.parts: List[object] = []
        self.nets: List[str] = []
        for net, geoms in geoms_by_net.items():
            if not geoms:
                continue
            u = shapely.union_all([g for g in geoms if g is not None and not g.is_empty])
            if u.is_empty:
                continue
            polys = list(u.geoms) if hasattr(u, "geoms") else [u]
            for p in polys:
                if p.geom_type == "Polygon" and p.area > 0:
                    self.parts.append(p)
                    self.nets.append(net)
        self.part_offset = part_offset
        self.tree = STRtree(self.parts) if self.parts else None

    def cut(self, p0: Pt, d: Pt, half: float) -> List[CutInterval]:
        """Intervalles de cuivre le long de p0 + u·d, u ∈ [-half, half]."""
        if self.tree is None:
            return []
        a = (p0[0] - d[0] * half, p0[1] - d[1] * half)
        b = (p0[0] + d[0] * half, p0[1] + d[1] * half)
        line = LineString([a, b])
        out = []
        for i in self.tree.query(line):
            inter = self.parts[i].intersection(line)
            if inter.is_empty:
                continue
            pieces = list(inter.geoms) if hasattr(inter, "geoms") else [inter]
            for pc in pieces:
                if pc.geom_type != "LineString" or pc.length <= 0:
                    continue
                us = [(x - p0[0]) * d[0] + (y - p0[1]) * d[1] for x, y in pc.coords]
                out.append(CutInterval(min(us), max(us), self.nets[i], self.layer, self.part_offset + int(i)))
        out.sort(key=lambda c: c.u0)
        return out


@dataclass
class BoardModel:
    name: str
    stackup: Optional[Stackup]
    copper_names: List[str]
    segs: List[Seg] = field(default_factory=list)
    vias: List[ViaObj] = field(default_factory=list)
    pads: List[PadObj] = field(default_factory=list)
    zones: List[ZoneObj] = field(default_factory=list)
    netclass_of: Dict[str, str] = field(default_factory=dict)        # net -> netclass principale
    netclass_all: Dict[str, List[str]] = field(default_factory=dict)  # net -> toutes ses netclasses
    netclasses: Dict[str, NetClassInfo] = field(default_factory=dict)
    source: str = "file"
    warnings: List[str] = field(default_factory=list)
    outline: Optional[object] = None           # contour de carte (shapely), optionnel
    _layer_cache: Dict[str, LayerCopper] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ requêtes
    @property
    def nets(self) -> List[str]:
        s = {x.net for x in self.segs} | {v.net for v in self.vias} | {p.net for p in self.pads} | \
            {z.net for z in self.zones}
        s.discard("")
        return sorted(s)

    def segs_of(self, net: str) -> List[Seg]:
        return [s for s in self.segs if s.net == net]

    def unfilled_zones(self) -> List[ZoneObj]:
        return [z for z in self.zones if not z.is_filled]

    def layer_copper(self, layer: str) -> LayerCopper:
        if layer not in self._layer_cache:
            geoms: Dict[str, list] = {}
            for s in self.segs:
                if s.layer == layer:
                    geoms.setdefault(s.net, []).append(s.geometry())
            for v in self.vias:
                if layer in v.layers:
                    geoms.setdefault(v.net, []).append(Point(v.pos).buffer(v.diameter / 2, quad_segs=8))
            for p in self.pads:
                g = p.shapes.get(layer)
                if g is not None:
                    geoms.setdefault(p.net, []).append(g)
            for z in self.zones:
                g = z.filled.get(layer)
                if g is not None and not g.is_empty:
                    geoms.setdefault(z.net, []).append(g)
            offset = 1_000_000 * (self.copper_names.index(layer) + 1) if layer in self.copper_names else 0
            self._layer_cache[layer] = LayerCopper(layer, geoms, offset)
        return self._layer_cache[layer]

    def vias_near_line(self, p0: Pt, d: Pt, half: float, margin: float = 0.0):
        """Vias et pads traversants coupés par le segment de coupe : [(u, rayon, net, obj)]."""
        out = []
        n = (-d[1], d[0])
        for v in self.vias:
            rx, ry = v.pos[0] - p0[0], v.pos[1] - p0[1]
            u = rx * d[0] + ry * d[1]
            dist = abs(rx * n[0] + ry * n[1])
            if abs(u) <= half and dist <= v.diameter / 2 + margin:
                out.append((u, v.diameter / 2, v.net, v))
        for p in self.pads:
            if not p.is_through:
                continue
            rx, ry = p.pos[0] - p0[0], p.pos[1] - p0[1]
            u = rx * d[0] + ry * d[1]
            dist = abs(rx * n[0] + ry * n[1])
            r = p.drill / 2
            if abs(u) <= half and dist <= r + margin:
                out.append((u, r, p.net, p))
        return out

    def pads_at(self, pt: Pt, layer: str, net: Optional[str] = None, tol: float = 0.0):
        q = Point(pt)
        out = []
        for p in self.pads:
            g = p.shapes.get(layer)
            if g is None or (net is not None and p.net != net):
                continue
            if g.distance(q) <= tol:
                out.append(p)
        return out
