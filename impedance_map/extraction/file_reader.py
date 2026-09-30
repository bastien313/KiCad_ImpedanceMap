"""Lecture hors ligne d'un fichier .kicad_pcb (KiCad 8/9/10) vers un BoardModel.

Sert aux tests, à la CLI et au board d'exemple. Couvre : pistes, arcs, vias, pads
(rect, roundrect, circle, oval, trapezoid ≈ rect, custom ≈ ancre), zones remplies
(filled_polygon), contour Edge.Cuts, netclasses (fichier .kicad_pro voisin).

Rotation KiCad (y vers le bas, angle positif = sens trigo à l'écran) :
    monde = pos + (x·cosθ + y·sinθ, −x·sinθ + y·cosθ)
"""

from __future__ import annotations

import fnmatch
import json
import math
import pathlib
import re
from typing import Dict, List, Optional

import shapely
from shapely.affinity import rotate as sh_rotate, translate as sh_translate
from shapely.geometry import LineString, Point, Polygon, box

from . import sexpr as sx
from .board_model import BoardModel, NetClassInfo, PadObj, Seg, ViaObj, ZoneObj, arc_points
from ..stackup.sources import from_kicad_pcb_tree

MM = 1e-3


def _rot(x, y, deg):
    t = math.radians(deg)
    return x * math.cos(t) + y * math.sin(t), -x * math.sin(t) + y * math.cos(t)


def _net_of(node, net_table: Dict[str, str]) -> str:
    n = sx.child(node, "net")
    if n is None or len(n) < 2:
        return ""
    v = n[1]
    if len(n) >= 3:          # (net 3 "GND") dans une définition
        return n[2]
    if re.fullmatch(r"\d+", v) and v in net_table:
        return net_table[v]
    return v


def _copper_order(tree, stackup) -> List[str]:
    if stackup is not None:
        return stackup.copper_names
    names = [L[1] for L in sx.child(tree, "layers")[1:] if isinstance(L, list) and str(L[1]).endswith(".Cu")]
    inner = sorted([n for n in names if n.startswith("In")], key=lambda s: int(re.findall(r"\d+", s)[0]))
    return ["F.Cu"] + inner + ["B.Cu"]


def _expand_layers(layers: List[str], copper: List[str]) -> List[str]:
    out = []
    for l in layers:
        if l in ("*.Cu", "F&B.Cu"):
            out.extend(copper if l == "*.Cu" else [copper[0], copper[-1]])
        elif l in copper:
            out.append(l)
    return list(dict.fromkeys(out))


def _pad_shape(pad, abs_angle) -> Optional[object]:
    kind = pad[3] if len(pad) > 3 else "rect"
    size = sx.child(pad, "size")
    w, h = (float(size[1]) * MM, float(size[2]) * MM) if size else (0.0, 0.0)
    if kind == "circle":
        g = Point(0, 0).buffer(w / 2, quad_segs=12)
    elif kind == "oval":
        if abs(w - h) < 1e-12:
            g = Point(0, 0).buffer(w / 2, quad_segs=12)
        elif w > h:
            g = LineString([(-(w - h) / 2, 0), ((w - h) / 2, 0)]).buffer(h / 2, quad_segs=12)
        else:
            g = LineString([(0, -(h - w) / 2), (0, (h - w) / 2)]).buffer(w / 2, quad_segs=12)
    elif kind == "roundrect":
        r = sx.fvalue(pad, "roundrect_rratio", default=0.25) * min(w, h)
        g = box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r).buffer(r, quad_segs=6) if r > 0 else \
            box(-w / 2, -h / 2, w / 2, h / 2)
    else:  # rect, trapezoid, custom (ancre)
        g = box(-w / 2, -h / 2, w / 2, h / 2)
    if kind == "custom":
        prims = sx.child(pad, "primitives")
        extra = []
        for gp in (sx.children(prims, "gr_poly") if prims else []):
            pts = [(float(p[1]) * MM, float(p[2]) * MM) for p in sx.children(sx.child(gp, "pts"), "xy")]
            if len(pts) >= 3:
                width = sx.fvalue(gp, "width") * MM
                poly = Polygon(pts)
                extra.append(poly.buffer(width / 2) if width > 0 else poly)
        if extra:
            g = shapely.union_all([g] + extra)
    # rotation (sens KiCad : y vers le bas -> rotation shapely de -angle)
    if abs_angle:
        g = sh_rotate(g, -abs_angle, origin=(0, 0))
    return g


def _poly_from_pts(pts_node) -> Optional[Polygon]:
    pts = []
    for c in pts_node[1:]:
        if sx.head(c) == "xy":
            pts.append((float(c[1]) * MM, float(c[2]) * MM))
        elif sx.head(c) == "arc":
            a = sx.xy(c, "start")
            m = sx.xy(c, "mid")
            b = sx.xy(c, "end")
            arc = arc_points((a[0] * MM, a[1] * MM), (m[0] * MM, m[1] * MM), (b[0] * MM, b[1] * MM))
            pts.extend(arc)
    if len(pts) < 3:
        return None
    p = Polygon(pts)
    if not p.is_valid:
        p = p.buffer(0)
    return p


def _read_project_netclasses(pcb_path: pathlib.Path, nets: List[str]):
    pro = pcb_path.with_suffix(".kicad_pro")
    classes: Dict[str, NetClassInfo] = {}
    assign: Dict[str, str] = {}
    if not pro.exists():
        return classes, assign
    try:
        d = json.loads(pro.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return classes, assign
    ns = d.get("net_settings", {})
    for c in ns.get("classes", []):
        def g(k):
            v = c.get(k)
            return float(v) * MM if isinstance(v, (int, float)) else None
        classes[c["name"]] = NetClassInfo(c["name"], g("track_width"), g("diff_pair_width"), g("diff_pair_gap"))
    explicit = ns.get("netclass_assignments") or {}
    patterns = ns.get("netclass_patterns") or []
    for n in nets:
        if n in explicit:
            v = explicit[n]
            assign[n] = v[0] if isinstance(v, list) else v
            continue
        for p in patterns:
            pat = p.get("pattern", "")
            try:
                ok = fnmatch.fnmatchcase(n, pat) or (pat.startswith("/") and False)
                if not ok and any(ch in pat for ch in "^$[]().+"):
                    ok = re.fullmatch(pat, n) is not None
            except re.error:
                ok = False
            if ok:
                assign[n] = p.get("netclass", "Default")
                break
        else:
            assign[n] = "Default"
    return classes, assign


def read_kicad_pcb(path) -> BoardModel:
    path = pathlib.Path(path)
    tree = sx.parse(path.read_text(encoding="utf-8"))
    try:
        stackup = from_kicad_pcb_tree(tree)
    except ValueError as e:
        stackup = None
        warn_stack = str(e)
    else:
        warn_stack = None
    copper = _copper_order(tree, stackup)
    net_table = {}
    for n in sx.children(tree, "net"):
        if len(n) >= 3:
            net_table[n[1]] = n[2]

    bm = BoardModel(name=path.name, stackup=stackup, copper_names=copper, source=f"file:{path}")
    if warn_stack:
        bm.warnings.append(warn_stack)

    for node in tree[1:]:
        h = sx.head(node)
        if h == "segment" or h == "arc":
            layer = sx.value(node, "layer")
            if layer not in copper:
                continue
            a = sx.xy(node, "start")
            b = sx.xy(node, "end")
            mid = sx.xy(node, "mid") if h == "arc" else None
            bm.segs.append(Seg(uid=sx.value(node, "uuid", default="") or "", net=_net_of(node, net_table),
                               layer=layer, width=sx.fvalue(node, "width") * MM,
                               start=(a[0] * MM, a[1] * MM), end=(b[0] * MM, b[1] * MM),
                               mid=(mid[0] * MM, mid[1] * MM) if mid else None))
        elif h == "via":
            at = sx.xy(node, "at")
            lay = sx.child(node, "layers")
            span = [copper[0], copper[-1]] if lay is None else [x for x in lay[1:]]
            i0, i1 = copper.index(span[0]), copper.index(span[-1])
            i0, i1 = min(i0, i1), max(i0, i1)
            bm.vias.append(ViaObj(uid=sx.value(node, "uuid", default="") or "", net=_net_of(node, net_table),
                                  pos=(at[0] * MM, at[1] * MM), diameter=sx.fvalue(node, "size") * MM,
                                  drill=sx.fvalue(node, "drill") * MM, layers=copper[i0:i1 + 1]))
        elif h == "footprint":
            at = sx.child(node, "at")
            fx, fy = float(at[1]) * MM, float(at[2]) * MM
            frot = float(at[3]) if len(at) > 3 else 0.0
            ref = ""
            side = "B" if str(sx.value(node, "layer", default="")).startswith("B.") else "F"
            for p in sx.children(node, "property"):
                if len(p) > 2 and p[1] == "Reference":
                    ref = p[2]
            for fp_text in sx.children(node, "fp_text"):
                if len(fp_text) > 2 and fp_text[1] == "reference":
                    ref = fp_text[2]
            for pad in sx.children(node, "pad"):
                pat = sx.child(pad, "at")
                px, py = float(pat[1]) * MM, float(pat[2]) * MM
                prot = float(pat[3]) if len(pat) > 3 else 0.0
                dx, dy = _rot(px, py, frot)
                pos = (fx + dx, fy + dy)
                lay = sx.child(pad, "layers")
                layers = _expand_layers(list(lay[1:]) if lay else [], copper)
                if not layers:
                    continue
                shape = _pad_shape(pad, prot)
                if shape is None:
                    continue
                shape = sh_translate(shape, pos[0], pos[1])
                drill_n = sx.child(pad, "drill")
                drill = 0.0
                if drill_n is not None:
                    nums = [v for v in drill_n[1:] if isinstance(v, str) and re.fullmatch(r"[\d.]+", v)]
                    drill = float(nums[0]) * MM if nums else 0.0
                bm.pads.append(PadObj(uid=sx.value(pad, "uuid", default="") or "", net=_net_of(pad, net_table),
                                      pos=pos, shapes={l: shape for l in layers}, drill=drill,
                                      ref=f"{ref}.{pad[1]}", side=side))
        elif h == "zone":
            if sx.child(node, "keepout") is not None:
                continue
            lay = sx.child(node, "layers")
            layers = list(lay[1:]) if lay is not None else [sx.value(node, "layer")]
            layers = _expand_layers(layers, copper)
            if not layers:
                continue
            filled: Dict[str, list] = {}
            for fp in sx.children(node, "filled_polygon"):
                l = sx.value(fp, "layer")
                poly = _poly_from_pts(sx.child(fp, "pts"))
                if poly is not None and l in layers:
                    filled.setdefault(l, []).append(poly)
            merged = {l: shapely.union_all(v) for l, v in filled.items()}
            bm.zones.append(ZoneObj(uid=sx.value(node, "uuid", default="") or "", net=_net_of(node, net_table),
                                    name=sx.value(node, "name", default="") or "", layers=layers,
                                    filled=merged, is_filled=bool(merged)))
        elif h in ("gr_line", "gr_rect", "gr_arc", "gr_poly", "gr_circle") and sx.value(node, "layer") == "Edge.Cuts":
            pass  # contour : non requis pour l'analyse

    classes, assign = _read_project_netclasses(path, bm.nets)
    bm.netclasses = classes
    bm.netclass_of = assign
    bm.netclass_all = {n: [c] for n, c in assign.items()}
    return bm
