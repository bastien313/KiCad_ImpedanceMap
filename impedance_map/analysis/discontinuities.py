"""Discontinuités du chemin principal : détection géométrique et paramètres des modèles localisés.

Utilisé pour l'intégrité du signal (analysis/signal.py). Pour chaque cible, le chemin le plus
long est le « chemin principal » ; on y repère :

  * VIA (changement de couche) : fût = ligne coaxiale Z_via = 60/√εr · ln(D2/d) sur la hauteur
    utilisée entre les deux couches (d = perçage, D2 = diamètre d'antipad MESURÉ sur les remplissages
    des plans traversés) ; parties du fût au-delà des couches utilisées = STUBS DE VIA (lignes ouvertes
    en dérivation, résonance quart d'onde f = c / (4 l √εr)). Sans via de retour d'un net de plan à
    moins de `return_via_distance`, le courant de retour fait un détour : on ajoute l'excédent
    d'inductance L_Johnson − L_coax, avec L_Johnson = 0,2·h·(ln(4h/d) + 1) nH (h, d en mm).
  * COIN : capacité en excès ΔC = C′ × (A_cuivre − A_droit)/w, où A_cuivre est la surface de cuivre
    réelle (tronçons arrondis KiCad) dans un disque autour du sommet et A_droit celle d'une piste
    droite de même largeur dans le même disque.
  * FENTE (points « perte de référence ») : inductance série L = 0,2·D·ln(D/W) nH (Johnson, D, W en
    mm), D = étendue de la fente perpendiculairement à la piste, W = largeur de la fente le long de la
    piste, toutes deux mesurées par lancer de rayons sur le cuivre réel du plan. Si le plan s'arrête
    (bord de plan) au lieu de former une fente, D est plafonnée et le résultat signalé.
  * STUB DE PISTE : autre chemin du même net dont une extrémité est sur le chemin principal (point
    de test, ESD, branche en T) -> ligne ouverte en dérivation au point de jonction, construite avec
    les impédances réellement calculées sur la branche (analysis/signal.py).

Les pads traversés par le chemin principal sont traités à part (coupes 2D « cuivre du même net
fusionné », voir Engine) : ils deviennent des tronçons courts de Z et εeff locaux.

Hypothèse commune : éléments courts devant la longueur du front (modèle quasi-statique localisé).
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

C_LIGHT = 299_792_458.0
RAY_MAX = 30e-3


def _dir(a, b):
    d = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    return ((b[0] - a[0]) / d, (b[1] - a[1]) / d)


def main_path_id(paths) -> Optional[int]:
    if not paths:
        return None
    return max(paths, key=lambda p: p.length).path_id


def _project(pts: np.ndarray, s: np.ndarray, p) -> Tuple[float, float]:
    """(abscisse curviligne, distance) du projeté de p sur la polyligne."""
    best = (0.0, float("inf"))
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        d = b - a
        L2 = float(d @ d)
        t = 0.0 if L2 <= 0 else max(0.0, min(1.0, float((np.array(p) - a) @ d) / L2))
        q = a + t * d
        dist = float(np.hypot(*(np.array(p) - q)))
        if dist < best[1]:
            best = (float(s[i] + t * math.sqrt(L2)), dist)
    return best


def _eps_between(stackup, y0, y1) -> float:
    lay = stackup.vertical_layout()
    num = den = 0.0
    for a, b, er, _, _ in lay["dielectrics"]:
        o = max(0.0, min(b, y1) - max(a, y0))
        num += o * er
        den += o
    return num / den if den > 0 else 4.0


def _plane_parts(bm, layer, exclude_nets):
    lc = bm.layer_copper(layer)
    return [(p, n) for p, n in zip(lc.parts, lc.nets) if n and n not in exclude_nets]


class _Barrel:
    """Fût métallisé vu par le modèle : via, ou pad traversant (broche de connecteur, composant THT)."""

    def __init__(self, pos, diameter, drill, layers, label, pad=None):
        self.pos, self.diameter, self.drill, self.layers, self.label, self.pad = pos, diameter, drill, layers, label, pad


def find_barrel(bm, stackup, net, point) -> Optional[_Barrel]:
    for v in bm.vias:
        if v.net == net and abs(v.pos[0] - point[0]) < 5e-6 and abs(v.pos[1] - point[1]) < 5e-6:
            return _Barrel(v.pos, v.diameter, v.drill, v.layers, "via")
    p = bm.through_pad_at(point, net)
    if p is None:
        return None
    ext = [g.bounds for g in p.shapes.values() if g is not None and not g.is_empty]
    dia = max((min(b[2] - b[0], b[3] - b[1]) for b in ext), default=2 * p.drill)
    return _Barrel(p.pos, max(dia, p.drill * 1.1), p.drill, list(stackup.copper_names),
                   f"broche {p.ref}" if p.ref else "pad traversant", pad=p)


def via_element(bm, stackup, net, point, layer_a, layer_b, s_v, return_distance, barrel=None) -> Optional[dict]:
    """Modèle du fût entre layer_a et layer_b (via ou broche traversante) ; layer_a == layer_b : fût
    traversé sur place (seulement des stubs)."""
    via = barrel or find_barrel(bm, stackup, net, point)
    lay = stackup.vertical_layout()["copper"]
    yc = {L: 0.5 * (a + b) for L, (a, b) in lay.items()}
    if via is None or layer_a not in yc or layer_b not in yc:
        return None
    ya, yb = yc[layer_a], yc[layer_b]
    span = [L for L in via.layers if L in yc] or [layer_a, layer_b]
    ytop, ybot = max(yc[L] for L in span), min(yc[L] for L in span)
    h_used = abs(ya - yb)
    stub_top = max(0.0, ytop - max(ya, yb))
    stub_bot = max(0.0, min(ya, yb) - ybot)
    er = _eps_between(stackup, ybot, ytop)
    d = via.drill if via.drill > 0 else 0.6 * via.diameter
    # antipad : distance au cuivre d'un autre net sur les couches traversées (hors couches utilisées)
    dmin = None
    P = Point(via.pos)
    for L in span:
        for part, n in _plane_parts(bm, L, {net}):
            dist = part.distance(P)
            if via.diameter / 2 < dist < 3e-3 and (dmin is None or dist < dmin):
                dmin = dist
    note = ""
    if dmin is None:
        D2 = 3 * via.diameter
        note = "aucun plan proche du via : antipad supposé = 3 × diamètre du pad"
    else:
        D2 = 2 * dmin
    D2 = max(D2, 1.2 * d)
    z_via = 60.0 / math.sqrt(er) * math.log(D2 / d)
    plane_nets = {z.net for z in bm.zones if z.net and z.is_filled} - {net}
    ret = any(u.net in plane_nets and math.hypot(u.pos[0] - via.pos[0], u.pos[1] - via.pos[1]) <= return_distance
              for u in bm.vias)
    L_coax = z_via * h_used * math.sqrt(er) / C_LIGHT
    h_mm, d_mm = h_used * 1e3, d * 1e3
    L_j = 0.2e-9 * h_mm * (math.log(4 * h_mm / d_mm) + 1) if h_mm > 0 else 0.0
    extra_L = 0.0 if ret else max(0.0, L_j - L_coax)
    going_down = ya > yb
    entry_stub, exit_stub = (stub_top, stub_bot) if going_down else (stub_bot, stub_top)
    stubs = [l for l in (stub_top, stub_bot) if l > 1e-6]
    return {
        "type": "via", "s": s_v, "pos": list(via.pos), "layers": f"{layer_a}→{layer_b}", "what": via.label,
        "z_via": z_via, "eps": er, "h_used": h_used, "entry_stub": entry_stub, "exit_stub": exit_stub,
        "extra_L": extra_L, "drill": d, "D2": D2, "return_via": ret, "note": note,
        "L_total_nH": (L_coax + extra_L) * 1e9, "C_barrel_pF": h_used * math.sqrt(er) / C_LIGHT / z_via * 1e12,
        "stub_mm": [l * 1e3 for l in stubs],
        "stub_resonance_GHz": [C_LIGHT / (4 * l * math.sqrt(er)) / 1e9 for l in stubs],
    }


def corner_element(ea, eb, s_v) -> Optional[dict]:
    """Excédent de longueur équivalente au coin (m) ; ΔC = C′ × extra_len (C′ appliqué ensuite)."""
    pa, pb = ea.points(), eb.points()
    V = pa[-1]
    w = max(ea.seg.width, eb.seg.width)
    la = LineString(pa).length if len(pa) > 1 else 0.0
    lb = LineString(pb).length if len(pb) > 1 else 0.0
    R = min(3 * w, 0.45 * la, 0.45 * lb)
    if R <= w / 2:
        return None
    disk = Point(V).buffer(R, quad_segs=32)
    copper = unary_union([ea.seg.geometry(), eb.seg.geometry()]).intersection(disk).area
    d = _dir(pa[-2], pa[-1])
    straight = LineString([(V[0] - d[0] * 2 * R, V[1] - d[1] * 2 * R), (V[0] + d[0] * 2 * R, V[1] + d[1] * 2 * R)])
    ref = straight.buffer(w / 2, quad_segs=8).intersection(disk).area
    d2 = _dir(pb[0], pb[1])
    ang = math.degrees(math.acos(max(-1.0, min(1.0, d[0] * d2[0] + d[1] * d2[1]))))
    return {"type": "corner", "s": s_v, "pos": list(V), "angle": ang, "extra_len": (copper - ref) / w,
            "width": w}


def _ray(parts_geom, p, u, rmax=RAY_MAX) -> Tuple[float, bool]:
    ray = LineString([p, (p[0] + u[0] * rmax, p[1] + u[1] * rmax)])
    hit = ray.intersection(parts_geom)
    if hit.is_empty:
        return rmax, False
    return Point(p).distance(hit), True


def slot_element(bm, stackup, nets, samples, layer) -> Optional[dict]:
    """Fente sous un groupe d'échantillons « perte de référence » consécutifs."""
    mid = samples[len(samples) // 2]
    p = (mid.x, mid.y)
    t = (mid.tx, mid.ty)
    n = (-t[1], t[0])
    names = stackup.copper_names
    lay = stackup.vertical_layout()["copper"]
    y = 0.5 * sum(lay[layer])
    cands = sorted([L for L in names if L != layer], key=lambda L: abs(0.5 * sum(lay[L]) - y))
    P = Point(p)
    for L in cands[:2]:
        parts = [g for g, _ in _plane_parts(bm, L, set(nets))]
        if not parts:
            continue
        near = [g for g in parts if g.distance(P) < 5e-3]
        if not near or any(g.contains(P) for g in near):
            continue
        geom = unary_union(near)
        d1, h1 = _ray(geom, p, n)
        d2, h2 = _ray(geom, p, (-n[0], -n[1]))
        w1, _ = _ray(geom, p, t, 10e-3)
        w2, _ = _ray(geom, p, (-t[0], -t[1]), 10e-3)
        D, W = d1 + d2, max(w1 + w2, 0.05e-3)
        if D <= W:
            D = 1.5 * W
        L_slot = 0.2e-9 * (D * 1e3) * math.log(D / W)
        return {"type": "slot", "s": mid.s, "pos": list(p), "layer": L, "D_mm": D * 1e3, "W_mm": W * 1e3,
                "L": L_slot, "L_nH": L_slot * 1e9, "edge": not (h1 and h2),
                "note": ("bord de plan (le plan s'arrête d'un côté) : inductance plafonnée, effet réel plus fort"
                         if not (h1 and h2) else "")}
    return None


def _dedupe_barrels(elems: List[dict]) -> List[dict]:
    """Un seul élément par fût : plusieurs segments peuvent se rejoindre au centre d'une même broche.
    On garde la transition qui traverse le plus de hauteur (changement de couche > fût traversé sur place),
    puis la broche terminale."""
    best: dict = {}
    order = []
    for e in elems:
        if e["type"] != "via":
            order.append(e)
            continue
        key = (round(e["pos"][0] * 1e5), round(e["pos"][1] * 1e5))
        cur = best.get(key)
        if cur is None:
            best[key] = e
            order.append(e)
        elif (e["h_used"], bool(e.get("terminal"))) > (cur["h_used"], bool(cur.get("terminal"))):
            order[order.index(cur)] = e
            best[key] = e
    return order


def pin_elements(bm, stackup, net, main, return_distance) -> List[dict]:
    """Broches traversantes aux extrémités du chemin principal (connecteur, composant THT).

    Composant sur la face opposée à la piste : le fût traverse la carte EN SÉRIE jusqu'au composant.
    Même face (ou face inconnue) : le fût est un stub jusqu'à l'autre face. La partie de la broche hors
    de la carte et le contact du connecteur ne sont pas modélisés.
    """
    out = []
    names = stackup.copper_names
    ends = [(main.elems[0].start, main.elems[0].seg.layer, 0.0),
            (main.elems[-1].end, main.elems[-1].seg.layer, main.length)]
    for pt, layer, s_v in ends:
        b = find_barrel(bm, stackup, net, pt)
        if b is None or b.pad is None:
            continue
        side = {"F": names[0], "B": names[-1]}.get(b.pad.side, layer)
        el = via_element(bm, stackup, net, pt, layer, side, s_v, return_distance, barrel=b)
        if el:
            el["terminal"] = True
            if s_v == 0.0:            # entrée : le fût est parcouru du composant vers la piste
                el["entry_stub"], el["exit_stub"] = el["exit_stub"], el["entry_stub"]
            out.append(el)
    return out


def stub_elements(paths, main) -> Tuple[List[dict], List[str]]:
    out, notes = [], []
    if main is None:
        return out, notes
    mpts, ms, _ = main.polyline()
    for p in paths:
        if p.path_id == main.path_id:
            continue
        pts, s, _ = p.polyline()
        sj0, d0 = _project(mpts, ms, pts[0])
        sj1, d1 = _project(mpts, ms, pts[-1])
        on0, on1 = d0 < 5e-6, d1 < 5e-6
        if on0 and on1:
            notes.append(f"branche {p.path_id} reliée au chemin principal par ses deux extrémités (boucle) : ignorée")
            continue
        if not (on0 or on1):
            notes.append(f"branche {p.path_id} non reliée directement au chemin principal : ignorée")
            continue
        out.append({"type": "stub", "s": sj0 if on0 else sj1, "path_id": p.path_id, "reverse": not on0,
                    "length_mm": float(s[-1]) * 1e3, "pos": list(pts[0] if on0 else pts[-1])})
    return out, notes


def build_elements(bm, stackup, target, paths, samples, return_distance: float,
                   corner_angle_deg: float = 5.0) -> Tuple[Optional[int], List[dict], List[str]]:
    """Éléments localisés du chemin principal d'une cible (géométrie seulement)."""
    net = target.nets[0]
    mid = main_path_id(paths)
    if mid is None:
        return None, [], []
    main = next(p for p in paths if p.path_id == mid)
    elems: List[dict] = []
    notes: List[str] = []
    acc = 0.0
    elems += pin_elements(bm, stackup, net, main, return_distance)
    for k, e in enumerate(main.elems):
        if k > 0:
            a = main.elems[k - 1]
            if a.seg.layer != e.seg.layer:
                v = via_element(bm, stackup, net, a.end, a.seg.layer, e.seg.layer, acc, return_distance)
                if v:
                    elems.append(v)
                else:
                    notes.append(f"changement de couche en s = {acc * 1e3:.1f} mm sans via trouvé (non modélisé)")
            elif bm.through_pad_at(a.end, net) is not None:
                # pad traversant sur le trajet sans changement de couche : le fût est un stub
                v = via_element(bm, stackup, net, a.end, a.seg.layer, a.seg.layer, acc, return_distance)
                if v:
                    elems.append(v)
            else:
                d1 = _dir(a.points()[-2], a.points()[-1])
                d2 = _dir(e.points()[0], e.points()[1])
                ang = math.degrees(math.acos(max(-1.0, min(1.0, d1[0] * d2[0] + d1[1] * d2[1]))))
                if ang > corner_angle_deg:
                    c = corner_element(a, e, acc)
                    if c:
                        elems.append(c)
        acc += e.seg.length
    # fentes : groupes consécutifs de pertes de référence sur le chemin principal
    ms = sorted([s for s in samples if s.path_id == mid and s.net == net], key=lambda s: s.s)
    group: List = []
    for s in ms + [None]:
        if s is not None and s.status == "no_reference":
            group.append(s)
            continue
        if group:
            el = slot_element(bm, stackup, target.nets, group, group[0].layer)
            if el:
                el["length_mm"] = (group[-1].s - group[0].s) * 1e3
                elems.append(el)
            else:
                notes.append(f"perte de référence en s = {group[0].s * 1e3:.1f} mm : plan fendu non identifié")
            group = []
    elems = _dedupe_barrels(elems)
    st, n2 = stub_elements(paths, main)
    elems += st
    notes += n2
    elems.sort(key=lambda d: d["s"])
    return mid, elems, notes


def _barrel_elements(bm, stackup, net, main, return_distance) -> List[dict]:
    """Vias, broches traversées et broches terminales d'un chemin (sans coins, fentes ni stubs)."""
    out = pin_elements(bm, stackup, net, main, return_distance)
    acc = 0.0
    for k, e in enumerate(main.elems):
        if k > 0:
            a = main.elems[k - 1]
            if a.seg.layer != e.seg.layer or bm.through_pad_at(a.end, net) is not None:
                v = via_element(bm, stackup, net, a.end, a.seg.layer, e.seg.layer if a.seg.layer != e.seg.layer
                                else a.seg.layer, acc, return_distance)
                if v:
                    out.append(v)
        acc += e.seg.length
    return _dedupe_barrels(out)


def pair_elements(bm, stackup, target, p_paths, n_paths, samples, return_distance: float,
                  corner_angle_deg: float = 5.0, match_distance: float = 3e-3):
    """Éléments d'une paire : ceux du brin P (build_elements) + vias / broches du brin N.

    Un fût du brin P et un fût du brin N à moins de `match_distance` avec la même transition de couches
    forment un élément SYMÉTRIQUE (modèle différentiel habituel). Sinon l'élément est ASYMÉTRIQUE
    (`strand` = "P" ou "N") : un seul brin le voit, ce qui convertit une partie du mode différentiel en
    mode commun ; signal.py l'approxime en demi-effet différentiel.
    """
    mid, elems, notes = build_elements(bm, stackup, target, p_paths, samples, return_distance, corner_angle_deg)
    if mid is None:
        return mid, elems, notes
    n_main = next((p for p in n_paths if p.path_id == main_path_id(n_paths)), None)
    if n_main is None:
        return mid, elems, notes
    p_main = next(p for p in p_paths if p.path_id == mid)
    ppts, ps, _ = p_main.polyline()
    n_els = _barrel_elements(bm, stackup, target.nets[1], n_main, return_distance)
    p_vias = [e for e in elems if e["type"] == "via"]
    for e in p_vias:
        e["strand"] = "P"
    for ne in n_els:
        ne["strand"] = "N"
        ne["s"] = _project(ppts, ps, ne["pos"])[0]        # abscisse ramenée sur le brin P
        mate = next((pe for pe in p_vias if pe["strand"] == "P" and pe["layers"] == ne["layers"] and
                     math.hypot(pe["pos"][0] - ne["pos"][0], pe["pos"][1] - ne["pos"][1]) < match_distance), None)
        if mate is not None:
            mate["strand"] = "PN"
            mate["what"] = f"{mate.get('what', 'via')} + {ne.get('what', 'via')}"
        else:
            elems.append(ne)
    for e in elems:
        if e["type"] == "via" and e["strand"] != "PN":
            notes.append(f"{e.get('what', 'via')} ({e['layers']}) sur le seul brin {e['strand']} en s = "
                         f"{e['s'] * 1e3:.1f} mm : élément asymétrique (conversion de mode non calculée)")
    elems.sort(key=lambda d: d["s"])
    return mid, elems, notes
