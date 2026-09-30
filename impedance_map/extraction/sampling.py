"""Chaînage des pistes d'un net et échantillonnage le long du tracé.

  * les segments/arcs d'un net sont chaînés par leurs extrémités (tolérance 1 µm),
    y compris d'une couche à l'autre (passage par via) -> chemins (Path) ;
  * un point d'échantillonnage tous les `step` le long de chaque chemin, plus des
    points aux bords des zones de coin (raffinement aux changements de géométrie) ;
  * zones de discontinuité marquées dès l'échantillonnage :
      - coin : changement de direction > `corner_angle` entre deux éléments, sur ±corner_zone ;
      - arc serré : rayon < arc_min_radius_factor × largeur de piste ;
      - changement de couche (via) : ±corner_zone autour du point de transition ;
      - extrémités libres : ±w/2 (embout arrondi) ;
  * les changements de largeur de piste ne marquent rien ici : ils ajoutent seulement des points
    aux bords de ±w_max/2. C'est la largeur du cuivre FINAL mesurée dans les coupes (pistes, pads,
    zones fusionnés) qui décide de l'uniformité (Engine._non_uniform) ;
  * zones de cuivre du même net servant de piste (`zone_bridges`) : une extrémité libre de piste
    dans une zone allongée est prolongée le long de la zone ; deux extrémités libres dans la même
    zone sont reliées. Ces tronçons synthétiques (`Seg.synthetic`) sont échantillonnés comme des
    pistes, la largeur réelle étant mesurée dans chaque coupe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .board_model import Seg

Pt = Tuple[float, float]


@dataclass
class PathElem:
    seg: Seg
    reversed: bool

    def points(self) -> List[Pt]:
        p = self.seg.points()
        return p[::-1] if self.reversed else p

    @property
    def start(self) -> Pt:
        return self.seg.end if self.reversed else self.seg.start

    @property
    def end(self) -> Pt:
        return self.seg.start if self.reversed else self.seg.end


@dataclass
class Path:
    net: str
    elems: List[PathElem]
    path_id: int = 0

    def polyline(self):
        """Points, longueur cumulée, indice d'élément de chaque sous-segment."""
        pts: List[Pt] = []
        owner: List[int] = []
        for k, e in enumerate(self.elems):
            p = e.points()
            if pts and _dist(pts[-1], p[0]) < 1e-9:
                p = p[1:]
            elif pts:
                # saut (ne devrait pas arriver) : on relie quand même
                pass
            for q in p:
                pts.append(q)
                owner.append(k)
        arr = np.array(pts)
        s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(arr, axis=0).T))])
        return arr, s, owner

    @property
    def length(self) -> float:
        return sum(e.seg.length for e in self.elems)


@dataclass
class Sample:
    net: str
    path_id: int
    s: float
    pos: Pt
    tangent: Pt
    layer: str
    width: float
    seg_uid: str
    status: str = "ok"               # ok / discontinuity
    reason: str = ""
    radius: float = math.inf


def _dist(a: Pt, b: Pt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _key(p: Pt, tol: float = 1e-6):
    return (round(p[0] / tol), round(p[1] / tol))


def build_paths(segs: Sequence[Seg], tol: float = 1e-6) -> List[Path]:
    """Chaîne les segments d'un net en chemins (parcours en profondeur depuis les extrémités libres)."""
    if not segs:
        return []
    nodes: Dict[tuple, List[int]] = {}
    for i, s in enumerate(segs):
        nodes.setdefault(_key(s.start, tol), []).append(i)
        nodes.setdefault(_key(s.end, tol), []).append(i)
    used = [False] * len(segs)
    paths: List[Path] = []

    def walk(start_node, first_idx):
        elems = []
        node = start_node
        idx = first_idx
        while idx is not None:
            used[idx] = True
            s = segs[idx]
            rev = _key(s.start, tol) != node
            elems.append(PathElem(s, rev))
            node = _key(s.start if rev else s.end, tol)
            nxt = [j for j in nodes.get(node, []) if not used[j]]
            # continuer tout droit de préférence (angle minimal)
            if len(nxt) > 1:
                d0 = _dir(elems[-1])
                nxt.sort(key=lambda j: -_dot(d0, _dir_from(segs[j], node, tol)))
            idx = nxt[0] if nxt else None
        return elems

    ends = sorted([k for k, v in nodes.items() if len(v) == 1])
    for k in ends:
        i = nodes[k][0]
        if not used[i]:
            paths.append(Path(segs[i].net, walk(k, i)))
    for i in range(len(segs)):
        if not used[i]:
            paths.append(Path(segs[i].net, walk(_key(segs[i].start, tol), i)))
    for n, p in enumerate(paths):
        p.path_id = n
    return paths


def _dir(e: PathElem) -> Pt:
    p = e.points()
    a, b = p[-2], p[-1]
    d = _dist(a, b) or 1.0
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def _dir_from(s: Seg, node, tol) -> Pt:
    p = s.points()
    if _key(s.start, tol) != node:
        p = p[::-1]
    a, b = p[0], p[1]
    d = _dist(a, b) or 1.0
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def _dot(a: Pt, b: Pt) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _free_ends(segs: Sequence[Seg], tol: float = 1e-6) -> List[Tuple[Pt, Seg]]:
    """Extrémités libres (degré 1) du réseau de segments, avec le segment qui y arrive."""
    deg: Dict[tuple, List[Tuple[Pt, Seg]]] = {}
    for s in segs:
        deg.setdefault(_key(s.start, tol), []).append((s.start, s))
        deg.setdefault(_key(s.end, tol), []).append((s.end, s))
    return [v[0] for v in deg.values() if len(v) == 1]


def _arrival_dir(seg: Seg, p: Pt) -> Pt:
    """Direction unitaire de parcours de `seg` quand on arrive à son extrémité p."""
    pts = seg.points()
    if _dist(pts[0], p) < _dist(pts[-1], p):
        pts = pts[::-1]
    a, b = pts[-2], pts[-1]
    d = _dist(a, b) or 1.0
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def zone_bridges(segs: Sequence[Seg], zone_fill_of_layer, min_aspect: float = 1.5,
                 max_entry_angle_deg: float = 30.0) -> List[Seg]:
    """Tronçons synthétiques qui font passer le tracé par les zones de cuivre du même net.

    zone_fill_of_layer(layer) -> géométrie shapely des remplissages de zones du net sur la couche
    (ou None). Pour chaque polygone rempli :
      * deux extrémités libres de piste dedans -> segment droit entre elles (s'il reste dans la zone) ;
      * une seule extrémité libre, zone allongée (rectangle minimal ≥ `min_aspect`) et piste entrant
        à moins de `max_entry_angle_deg` de l'axe -> prolongement le long de l'axe jusqu'au bord de
        la zone (largeur nominale = petit côté du rectangle ; la coupe mesure la largeur réelle).
    Les autres cas (zone compacte, plus de deux extrémités, entrée de biais) sont ignorés : la zone
    reste du cuivre du net vu dans les coupes, sans devenir un tronçon du tracé.
    """
    from shapely.geometry import LineString, Point

    if not segs:
        return []
    net = segs[0].net
    ends = _free_ends(segs)
    out: List[Seg] = []
    for layer in sorted({s.layer for s in segs}):
        fill = zone_fill_of_layer(layer)
        if fill is None or fill.is_empty:
            continue
        polys = list(fill.geoms) if hasattr(fill, "geoms") else [fill]
        for zi, poly in enumerate(polys):
            if poly.geom_type != "Polygon" or poly.area <= 0:
                continue
            inside = poly.buffer(1e-6)
            here = [(p, s) for p, s in ends if s.layer == layer and inside.contains(Point(p))]
            rect = list(poly.minimum_rotated_rectangle.exterior.coords)[:4]
            e1 = (rect[1][0] - rect[0][0], rect[1][1] - rect[0][1])
            e2 = (rect[2][0] - rect[1][0], rect[2][1] - rect[1][1])
            l1, l2 = math.hypot(*e1), math.hypot(*e2)
            w_zone = min(l1, l2)
            uid = f"zone:{net}:{layer}:{zi}"
            if len(here) == 2:
                (p, _), (q, _) = here
                line = LineString([p, q])
                if line.length > 0 and inside.contains(line):
                    out.append(Seg(uid, net, layer, w_zone, p, q, synthetic=True))
                continue
            if len(here) != 1 or w_zone <= 0 or max(l1, l2) < min_aspect * w_zone:
                continue
            p, s = here[0]
            ax = e1 if l1 >= l2 else e2
            la = math.hypot(*ax)
            ax = (ax[0] / la, ax[1] / la)
            d_in = _arrival_dir(s, p)
            c = _dot(ax, d_in)
            if abs(c) < math.cos(math.radians(max_entry_angle_deg)):
                continue
            if c < 0:
                ax = (-ax[0], -ax[1])
            far = (p[0] + ax[0] * (la + w_zone), p[1] + ax[1] * (la + w_zone))
            inter = LineString([p, far]).intersection(inside)
            pieces = list(inter.geoms) if hasattr(inter, "geoms") else [inter]
            piece = next((g for g in pieces if g.geom_type == "LineString" and g.distance(Point(p)) < 2e-6), None)
            if piece is None:
                continue
            q = max(piece.coords, key=lambda xy: _dist(xy, p))
            q = (float(q[0]), float(q[1]))
            if _dist(p, q) > 0.5 * w_zone:
                out.append(Seg(uid, net, layer, w_zone, p, q, synthetic=True))
    return out


@dataclass
class SamplingOptions:
    step: float = 0.5e-3
    corner_angle_deg: float = 5.0
    corner_zone: Optional[float] = None      # défaut : max(w, 1,5·h_ref) fourni par l'appelant
    arc_min_radius_factor: float = 2.0       # arc serré si R < facteur × largeur de la piste
    end_margin: Optional[float] = None       # zone exclue aux extrémités libres ; défaut = w/2 (embout arrondi)


def sample_path(path: Path, opt: SamplingOptions, h_ref_of_layer, via_radius_at=None) -> List[Sample]:
    """Échantillonne un chemin. h_ref_of_layer(layer) -> distance diélectrique à la référence (m).

    via_radius_at(point) -> rayon du via présent en ce point (0 si aucun) : la zone de
    discontinuité d'un changement de couche couvre au moins 2 × ce rayon (antipads).
    """
    pts, s, owner = path.polyline()
    L = float(s[-1])
    if L <= 0:
        return []
    elems = path.elems

    # --- événements géométriques (abscisse, type, demi-zone)
    events: List[Tuple[float, str, float]] = []
    s_elem_start = []
    acc = 0.0
    for e in elems:
        s_elem_start.append(acc)
        acc += e.seg.length
    for k in range(1, len(elems)):
        a, b = elems[k - 1], elems[k]
        sv = s_elem_start[k]
        w = max(a.seg.width, b.seg.width)
        zone = opt.corner_zone or max(w, 1.5 * h_ref_of_layer(b.seg.layer))
        if a.seg.layer != b.seg.layer:
            r = via_radius_at(a.end) if via_radius_at else 0.0
            events.append((sv, "changement de couche (via)", max(zone, 2.0 * r)))
            continue
        ang = math.degrees(math.acos(max(-1.0, min(1.0, _dot(_dir(a), _dir_from_elem_start(b))))))
        if ang > opt.corner_angle_deg:
            events.append((sv, f"coin {ang:.0f}°", zone))
        if abs(a.seg.width - b.seg.width) > 1e-7:
            # points de raffinement seulement (non marquant) : l'embout de la piste large a un rayon w_max/2
            events.append((sv, "", max(0.5 * w, 5e-5)))
    # extrémités libres : arrivée sur pad/via
    w0, w1 = elems[0].seg.width, elems[-1].seg.width
    events.append((0.0, "extrémité (pad/via)", opt.end_margin or w0 / 2))
    events.append((L, "extrémité (pad/via)", opt.end_margin or w1 / 2))

    # --- abscisses d'échantillonnage : pas régulier + bords des zones
    n = max(1, int(round(L / opt.step)))
    ss = list(np.linspace(0.0, L, n + 1)[1:-1]) if n > 1 else [L / 2]
    for sv, why, z in events:
        # bords de zone ; aux extrémités, aussi un point dans la zone (pad terminal -> modèle localisé)
        extra = (sv - z / 2, sv + z / 2) if why.startswith("extrémité") else ()
        for sb in (sv - z * 1.001, sv + z * 1.001) + extra:
            if 0 < sb < L:
                ss.append(sb)
    ss = sorted(set(round(x, 9) for x in ss))

    out: List[Sample] = []
    for sq in ss:
        j = int(np.searchsorted(s, sq, side="right") - 1)
        j = min(max(j, 0), len(pts) - 2)
        t = (sq - s[j]) / max(s[j + 1] - s[j], 1e-15)
        p = pts[j] + t * (pts[j + 1] - pts[j])
        d = pts[j + 1] - pts[j]
        dn = float(np.hypot(*d)) or 1.0
        el = elems[owner[j + 1]]
        seg = el.seg
        smp = Sample(net=path.net, path_id=path.path_id, s=float(sq), pos=(float(p[0]), float(p[1])),
                     tangent=(float(d[0] / dn), float(d[1] / dn)), layer=seg.layer, width=seg.width,
                     seg_uid=seg.uid, radius=seg.radius)
        for sv, why, z in events:
            if why and abs(sq - sv) < z:
                smp.status, smp.reason = "discontinuity", why
                break
        if smp.status == "ok" and seg.is_arc:
            if seg.radius < opt.arc_min_radius_factor * seg.width:
                smp.status, smp.reason = "discontinuity", f"arc R={seg.radius * 1e3:.2f} mm"
        out.append(smp)
    return out


def _dir_from_elem_start(e: PathElem) -> Pt:
    p = e.points()
    a, b = p[0], p[1]
    d = _dist(a, b) or 1.0
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)
