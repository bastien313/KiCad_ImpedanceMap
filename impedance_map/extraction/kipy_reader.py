"""Lecture du board ouvert dans KiCad (API IPC, kipy) vers un BoardModel.

Points d'attention (voir aussi la section « Dépannage » du README) :
  * unités kipy : nanomètres -> convertis en mètres ici ;
  * zones : on lit `filled_polygons` (remplissage réel), jamais le contour ; une zone
    cuivre sans remplissage est signalée (BoardModel.unfilled_zones) ;
  * pads : forme exacte par couche via GetPadShapeAsPolygon (commande groupée) ;
  * netclasses : `get_netclass_for_nets` (objets Net, pas des chaînes) ;
  * AS_BUSY : si un outil/dialogue est actif dans KiCad, l'énumération échoue ->
    `wait_idle` retente puis lève une erreur explicite.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

import shapely
from shapely.geometry import Polygon

from .board_model import BoardModel, NetClassInfo, PadObj, Seg, ViaObj, ZoneObj, arc_points

NM = 1e-9


def layer_name(layer_enum: int) -> str:
    from kipy.board_types import BoardLayer
    n = BoardLayer.Name(layer_enum)
    return n[3:].replace("_", ".") if n.startswith("BL_") else n


def layer_enum(name: str) -> int:
    from kipy.board_types import BoardLayer
    return BoardLayer.Value("BL_" + name.replace(".", "_"))


def connect(timeout_ms: int = 20000):
    """Connexion kipy (socket/token fournis par KiCad via KICAD_API_SOCKET/KICAD_API_TOKEN)."""
    import kipy
    return kipy.KiCad(timeout_ms=timeout_ms)


def wait_idle(board, tries: int = 10, delay: float = 1.0):
    """Attend que KiCad accepte l'énumération d'items (AS_BUSY si outil/dialogue actif)."""
    from kipy.errors import ApiError
    last = None
    for _ in range(tries):
        try:
            board.get_tracks()
            return
        except ApiError as e:
            last = e
            if "busy" not in str(e).lower():
                raise
            time.sleep(delay)
    raise RuntimeError("KiCad est occupé (AS_BUSY) : appuyez sur Échap, fermez les dialogues ouverts "
                       "et cliquez dans le canevas, puis relancez.") from last


def _poly_to_shapely(pwh) -> Optional[Polygon]:
    def ring(polyline):
        pts = []
        for node in polyline.nodes:
            if node.has_point:
                p = node.point
                pts.append((p.x * NM, p.y * NM))
            elif node.has_arc:
                a = node.arc
                arc = arc_points((a.start.x * NM, a.start.y * NM), (a.mid.x * NM, a.mid.y * NM),
                                 (a.end.x * NM, a.end.y * NM))
                pts.extend(arc)
        return pts

    outer = ring(pwh.outline)
    if len(outer) < 3:
        return None
    holes = [h for h in (ring(x) for x in pwh.holes) if len(h) >= 3]
    p = Polygon(outer, holes)
    if not p.is_valid:
        p = p.buffer(0)
    return p


def _pad_polygons(board, pads, layer: int) -> Dict[str, object]:
    """{kiid pad: shapely} pour une couche (commande groupée, correspondance par identifiant)."""
    from kipy.proto.board import board_commands_pb2
    from kipy.geometry import PolygonWithHoles
    if not pads:
        return {}
    cmd = board_commands_pb2.GetPadShapeAsPolygon()
    cmd.board.CopyFrom(board.document)
    cmd.layer = layer
    cmd.pads.extend([p.id for p in pads])
    resp = board._kicad.send(cmd, board_commands_pb2.PadShapeAsPolygonResponse)
    out = {}
    for kiid, poly in zip(resp.pads, resp.polygons):
        g = _poly_to_shapely(PolygonWithHoles(poly))
        if g is not None:
            out[kiid.value] = g
    return out


def read_board(board, stackup=None, progress=None) -> BoardModel:
    """Construit le BoardModel du board ouvert. `stackup` : Stackup du board (sinon lu via l'API)."""
    from kipy.board_types import ArcTrack

    wait_idle(board)
    if stackup is None:
        from ..stackup.sources import from_kipy
        stackup = from_kipy(board)
    copper = stackup.copper_names
    bm = BoardModel(name=board.name, stackup=stackup, copper_names=copper, source="kipy")

    def step(msg):
        if progress:
            progress(msg)

    step("Lecture des pistes")
    for t in board.get_tracks():
        lname = layer_name(t.layer)
        if lname not in copper:
            continue
        net = t.net.name if t.net else ""
        if isinstance(t, ArcTrack):
            bm.segs.append(Seg(uid=t.id.value, net=net, layer=lname, width=t.width * NM,
                               start=(t.start.x * NM, t.start.y * NM), end=(t.end.x * NM, t.end.y * NM),
                               mid=(t.mid.x * NM, t.mid.y * NM)))
        else:
            bm.segs.append(Seg(uid=t.id.value, net=net, layer=lname, width=t.width * NM,
                               start=(t.start.x * NM, t.start.y * NM), end=(t.end.x * NM, t.end.y * NM)))

    step("Lecture des vias")
    for v in board.get_vias():
        try:
            d = v.padstack.drill
            i0, i1 = copper.index(layer_name(d.start_layer)), copper.index(layer_name(d.end_layer))
        except (ValueError, AttributeError):
            i0, i1 = 0, len(copper) - 1
        i0, i1 = min(i0, i1), max(i0, i1)
        bm.vias.append(ViaObj(uid=v.id.value, net=v.net.name if v.net else "",
                              pos=(v.position.x * NM, v.position.y * NM), diameter=v.diameter * NM,
                              drill=v.drill_diameter * NM, layers=copper[i0:i1 + 1]))

    step("Lecture des pads")
    pads = list(board.get_pads())
    shapes: Dict[str, Dict[str, object]] = {}
    for lname in copper:
        le = layer_enum(lname)
        on_layer = [p for p in pads if le in list(p.padstack.layers) or _is_all_copper(p)]
        for kiid, g in _pad_polygons(board, on_layer, le).items():
            shapes.setdefault(kiid, {})[lname] = g
    for p in pads:
        drill = 0.0
        try:
            dd = p.padstack.drill.diameter
            drill = max(dd.x, dd.y) * NM
        except AttributeError:
            pass
        bm.pads.append(PadObj(uid=p.id.value, net=p.net.name if p.net else "",
                              pos=(p.position.x * NM, p.position.y * NM), shapes=shapes.get(p.id.value, {}),
                              drill=drill, ref=str(p.number)))

    step("Lecture des zones (remplissages)")
    for z in board.get_zones():
        if z.is_rule_area():
            continue
        layers = [layer_name(l) for l in z.layers if layer_name(l) in copper]
        filled = {}
        for le, polys in z.filled_polygons.items():
            ln = layer_name(le)
            if ln not in copper:
                continue
            geoms = [g for g in (_poly_to_shapely(p) for p in polys) if g is not None]
            if geoms:
                filled[ln] = shapely.union_all(geoms)
        bm.zones.append(ZoneObj(uid=z.id.value, net=z.net.name if z.net else "", name=z.name, layers=layers,
                                filled=filled, is_filled=bool(filled) and bool(z.filled)))

    step("Lecture des netclasses")
    try:
        def g(v):
            return v * NM if v else None
        for nc in board.get_project().get_net_classes():
            bm.netclasses[nc.name] = NetClassInfo(nc.name, g(nc.track_width), g(nc.diff_pair_track_width),
                                                  g(nc.diff_pair_gap))
        nets = board.get_nets()
        for net_name, nc in board.get_netclass_for_nets(nets).items():
            # KiCad renvoie une classe composite « Effective for net: ... » quand plusieurs
            # netclasses s'appliquent : les vraies classes sont dans `constituents` (priorité décroissante).
            members = list(nc.constituents or []) or [nc.name]
            bm.netclass_all[net_name] = members
            bm.netclass_of[net_name] = members[0]
    except Exception as e:  # netclasses optionnelles pour l'analyse
        bm.warnings.append(f"Netclasses indisponibles via l'API : {e}")
    return bm


def _is_all_copper(pad) -> bool:
    try:
        return pad.padstack.drill.diameter.x > 0
    except AttributeError:
        return False
