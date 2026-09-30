"""Construction de la coupe transverse 2D en un point d'échantillonnage.

Entrées : BoardModel, Stackup, point central, direction de coupe (unitaire,
perpendiculaire à la piste), demi-largeur de fenêtre, cibles (net, abscisse attendue).

Règles physiques (documentées dans docs/PHYSICS.md) :
  * tout le cuivre de toutes les couches coupé par la ligne est pris en compte
    (pistes voisines, pads, remplissages de zones) ;
  * nets autres que les cibles -> conducteurs au repos, 0 V (GROUND) ;
  * cuivre sans net -> conducteur FLOTTANT (charge nette nulle), un corps par îlot ;
  * cuivre du même net que la cible mais distinct de la piste : s'il est à moins de
    `self_zone` de la piste -> discontinuité (coin, méandre, pad, via du même net) ;
    plus loin -> traité comme conducteur au repos (0 V) ;
  * vias / pads traversants DU MÊME NET coupés par la ligne près de la cible -> discontinuité ;
    ceux des autres nets (clôture de vias d'une ligne coplanaire, vias de couture) ne le sont pas :
    leurs pastilles sont dans le cuivre des couches, leur fût est ignoré (comme dans les
    calculateurs CPWG usuels ; un mur continu à sa place abaisserait Z de quelques %) et compté
    dans `CutInfo.ignored_vias` pour avertir l'utilisateur ;
  * `free_width` (piste dans une zone du même net, tronçon déduit d'une zone) : la largeur du
    signal est celle du cuivre coupé, pas celle de la piste nominale ;
  * référence : un conducteur GROUND sur une autre couche couvrant la piste ±w, ou à
    défaut des masses coplanaires des deux côtés à moins de 3w -> sinon « perte de
    référence » (valeur calculée mais signalée) ;
  * troncature exacte : un conducteur GROUND couvrant toute la fenêtre isole
    électriquement ce qui est au-delà (bords de Neumann) -> on coupe le domaine sur
    ce plan (accélère le calcul et augmente le taux de cache).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..solver.geometry import Conductor, CrossSection, MaskSpec, Slab, FLOATING, GROUND, SIGNAL
from ..stackup.model import CORE, DIELECTRIC, Stackup
from .board_model import BoardModel, CutInterval

Pt = Tuple[float, float]


@dataclass
class CutOptions:
    quantum: float = 1e-6              # quantification des abscisses (m) pour le cache
    etch_factor: float = 0.0           # 0 = rectangle ; sinon trapèze (t / retrait latéral)
    self_zone: Optional[float] = None  # défaut : 2·w
    via_zone: Optional[float] = None   # défaut : max(1,5·w, h_ref)
    width_tol: float = 0.25            # écart de largeur toléré avant « pad/jonction »
    truncate_shielded: bool = True
    merge_same_net: bool = False       # modèle localisé (pads) : le cuivre du même net proche de la
    #                                    piste est au potentiel du signal au lieu d'être à 0 V
    free_width: bool = False           # largeur du signal = cuivre coupé (zone du net), sans test


@dataclass
class CutInfo:
    status: str = "ok"                 # ok / discontinuity / no_reference / error
    reason: str = ""
    references: List[str] = field(default_factory=list)
    signal_widths: List[float] = field(default_factory=list)
    gap: float = float("nan")
    warnings: List[str] = field(default_factory=list)
    ignored_vias: int = 0              # fûts de vias d'autres nets proches de la piste, ignorés


def _covers(iv: CutInterval, a: float, b: float, tol: float = 1e-9) -> bool:
    return iv.u0 <= a + tol and iv.u1 >= b - tol


def build_cut(bm: BoardModel, stackup: Stackup, center: Pt, direction: Pt, half: float,
              targets: Sequence[Tuple[str, float, float]], layer: str,
              opt: Optional[CutOptions] = None) -> Tuple[Optional[CrossSection], CutInfo]:
    """targets : [(net, u_attendu, largeur_piste[, tolérance_de_recherche])] (1 ou 2 éléments,
    ordre = indices de signal). Tolérance par défaut : w/4 autour de u_attendu."""
    opt = opt or CutOptions()
    info = CutInfo()
    lay = stackup.vertical_layout()
    ycu: Dict[str, Tuple[float, float]] = lay["copper"]
    if layer not in ycu:
        info.status, info.reason = "error", f"couche {layer} absente du stackup"
        return None, info
    h_ref = stackup.nearest_plane_distance(layer)
    w_max = max(t[2] for t in targets)

    # ------------------------------------------------ intervalles de cuivre par couche
    raw: Dict[str, List[CutInterval]] = {}
    for L in stackup.copper_names:
        raw[L] = bm.layer_copper(L).cut(center, direction, half * 1.02)

    # ------------------------------------------------ identification des signaux
    sig_iv: List[CutInterval] = []
    for tg in targets:
        net, u_exp, w = tg[0], tg[1], tg[2]
        search = tg[3] if len(tg) > 3 else 0.25 * w
        cands = [iv for iv in raw[layer] if iv.net == net and iv.u0 - search <= u_exp <= iv.u1 + search]
        if not cands:
            if len(targets) > 1 and tg is not targets[0]:
                info.status, info.reason = "discontinuity", f"brin {net} non coupé perpendiculairement (virage)"
            else:
                info.status, info.reason = "error", f"piste {net} introuvable dans la coupe"
            return None, info
        iv = min(cands, key=lambda c: abs(c.center - u_exp))
        sig_iv.append(iv)
        info.signal_widths.append(iv.width)
        if opt.free_width:
            continue
        if iv.width > w * (1 + opt.width_tol) + 2e-6:
            info.status, info.reason = "discontinuity", "pad / jonction (cuivre plus large que la piste)"
        elif iv.width < w * (1 - opt.width_tol) - 2e-6:
            info.status, info.reason = "discontinuity", "fin de piste"
    if opt.free_width:
        w_max = max([w_max] + info.signal_widths)
    if len(sig_iv) == 2:
        if sig_iv[0] is sig_iv[1] or sig_iv[0].part == sig_iv[1].part:
            info.status, info.reason = "error", "les deux brins se confondent dans la coupe"
            return None, info
        a, b = sorted(sig_iv, key=lambda c: c.u0)
        info.gap = b.u0 - a.u1
    target_nets = {t[0] for t in targets}
    net_index = {t[0]: k for k, t in enumerate(targets)}

    # centrage de la fenêtre sur les signaux (invariance par translation -> cache)
    u_c = 0.5 * (min(iv.u0 for iv in sig_iv) + max(iv.u1 for iv in sig_iv))
    q = opt.quantum

    def snap(u):
        return round((u - u_c) / q) * q

    lo, hi = -half, half
    self_zone = opt.self_zone if opt.self_zone is not None else 2.0 * w_max
    via_zone = opt.via_zone if opt.via_zone is not None else max(1.5 * w_max, h_ref)
    s_lo = min(iv.u0 for iv in sig_iv)
    s_hi = max(iv.u1 for iv in sig_iv)

    # ------------------------------------------------ vias / pads traversants coupés
    if info.status == "ok":
        for u, r, net, obj in bm.vias_near_line(center, direction, half):
            dist = max(s_lo - (u + r), (u - r) - s_hi, 0.0)
            if dist >= via_zone:
                continue
            if net in target_nets:
                info.status, info.reason = "discontinuity", "via du même net"
                break
            info.ignored_vias += 1

    # ------------------------------------------------ conducteurs
    conds: List[Conductor] = []
    sig_ids = {id(iv): k for k, iv in enumerate(sig_iv)}
    fill_er = lay["copper_fill_er"]
    names = stackup.copper_names
    for L in names:
        y0, y1 = ycu[L]
        base_bottom = _base_at_bottom(stackup, L)
        for iv in raw[L]:
            a, b = max(iv.u0, u_c + lo), min(iv.u1, u_c + hi)
            if b - a < 2 * q:
                continue
            k = sig_ids.get(id(iv))
            if k is not None:
                role, idx = SIGNAL, k
            elif iv.net in target_nets:
                d = max(s_lo - b, a - s_hi, 0.0)
                if d < self_zone and info.status == "ok":
                    info.status = "discontinuity"
                    info.reason = f"cuivre du même net ({L}) à {d * 1e3:.2f} mm"
                if opt.merge_same_net and d < self_zone:
                    role, idx = SIGNAL, net_index[iv.net]
                else:
                    role, idx = GROUND, -1
            elif iv.net == "":
                role, idx = FLOATING, -1
            else:
                role, idx = GROUND, -1
            x0, x1 = snap(a), snap(b)
            if x1 - x0 < q:
                continue
            t = y1 - y0
            if opt.etch_factor > 0 and t > 0 and role == SIGNAL:
                dd = min(t / opt.etch_factor, 0.45 * (x1 - x0))
                if base_bottom:
                    c = Conductor(x0, x1, y0, y1, role, idx, x0t=x0 + dd, x1t=x1 - dd)
                else:
                    c = Conductor(x0 + dd, x1 - dd, y0, y1, role, idx, x0t=x0, x1t=x1)
            else:
                c = Conductor(x0, x1, y0, y1, role, idx)
            c.net, c.layer = iv.net, L
            c.float_group = iv.part if role == FLOATING else -1
            conds.append(c)

    # ------------------------------------------------ diélectriques
    slabs = [Slab(d[0], d[1], d[2], d[3]) for d in lay["dielectrics"]]
    for L, er in fill_er.items():
        y0, y1 = ycu[L]
        if y1 > y0:
            slabs.append(Slab(y0, y1, er, f"résine {L}"))
    masks = []
    mt, mb = stackup.mask("top"), stackup.mask("bottom")
    if mt is not None and mt.thickness > 0:
        masks.append(MaskSpec("top", lay["top_surface"], mt.thickness,
                              mt.mask_c2 if mt.mask_c2 is not None else mt.thickness, mt.er))
    if mb is not None and mb.thickness > 0:
        masks.append(MaskSpec("bottom", lay["bottom_surface"], mb.thickness,
                              mb.mask_c2 if mb.mask_c2 is not None else mb.thickness, mb.er))
    xs = CrossSection(slabs=slabs, conductors=conds, x_min=lo, x_max=hi, masks=masks)

    # ------------------------------------------------ références
    li = names.index(layer)
    sig_c = [c for c in conds if c.role == SIGNAL]
    if not sig_c:
        info.status, info.reason = "error", "signal hors fenêtre"
        return None, info
    refs = []
    partial = []
    for direction_idx in (range(li - 1, -1, -1), range(li + 1, len(names))):
        for j in direction_idx:
            Lj = names[j]
            on = [c for c in conds if c.layer == Lj and c.role == GROUND]
            if not on:
                continue
            need = [(c.xmin - c.width, c.xmax + c.width) for c in sig_c]
            if all(any(g.xmin <= a + 1e-9 and g.xmax >= b - 1e-9 for g in on) for a, b in need):
                refs.append(Lj)
                break
            # cuivre présent mais ne couvrant pas la piste : fente / bord de plan
            if any(g.xmax > min(c.xmin for c in sig_c) and g.xmin < max(c.xmax for c in sig_c) for g in on):
                partial.append(Lj)
            # on continue à chercher plus loin (le plan suivant peut servir de référence)
    if not refs:
        same = [c for c in conds if c.layer == layer and c.role == GROUND]
        left = [g for g in same if g.xmax <= min(c.xmin for c in sig_c) + 1e-9 and
                min(c.xmin for c in sig_c) - g.xmax < 3 * w_max]
        right = [g for g in same if g.xmin >= max(c.xmax for c in sig_c) - 1e-9 and
                 g.xmin - max(c.xmax for c in sig_c) < 3 * w_max]
        if left and right:
            refs.append(f"{layer} (coplanaire)")
    info.references = refs
    if not refs:
        if info.status == "ok":
            info.status = "no_reference"
            info.reason = "aucun plan de référence sous/sur la piste" + \
                          (f" (fente ou bord de plan sur {', '.join(partial)})" if partial else "")
    elif partial and info.status == "ok":
        info.warnings.append(f"plan incomplet sur {', '.join(partial)} (référence prise sur {refs[0]})")

    if opt.truncate_shielded:
        xs = _truncate_behind_full_planes(xs, layer, names, ycu)
    return xs, info


def _base_at_bottom(stackup: Stackup, layer: str) -> bool:
    """Côté de la base (large) d'une piste gravée : côté substrat/core."""
    names = stackup.copper_names
    if layer == names[0]:
        return True
    if layer == names[-1]:
        return False
    core = [l for l in stackup.layers if l.kind != "mask"]
    i = next(k for k, l in enumerate(core) if l.name == layer)
    below = core[i + 1] if i + 1 < len(core) else None
    above = core[i - 1] if i > 0 else None
    if below is not None and below.kind == DIELECTRIC and below.dielectric_type == CORE:
        return True
    if above is not None and above.kind == DIELECTRIC and above.dielectric_type == CORE:
        return False
    return True


def _truncate_behind_full_planes(xs: CrossSection, layer: str, names: List[str], ycu) -> CrossSection:
    """Coupe le domaine sur le premier plan GROUND couvrant toute la fenêtre de chaque côté."""
    sig = [c for c in xs.conductors if c.role == SIGNAL]
    y_sig_lo = min(c.y0 for c in sig)
    y_sig_hi = max(c.y1 for c in sig)

    def full(c):
        return c.role == GROUND and c.xmin <= xs.x_min + 1e-12 and c.xmax >= xs.x_max - 1e-12

    fulls = [c for c in xs.conductors if full(c)]
    above = [c for c in fulls if c.y0 >= y_sig_hi - 1e-12]
    below = [c for c in fulls if c.y1 <= y_sig_lo + 1e-12]
    y_top = min((c.y1 for c in above), default=None)
    y_bot = max((c.y0 for c in below), default=None)
    if y_top is None and y_bot is None:
        return xs
    keep = []
    for c in xs.conductors:
        if y_top is not None and c.y0 > y_top + 1e-12:
            continue
        if y_bot is not None and c.y1 < y_bot - 1e-12:
            continue
        keep.append(c)
    slabs = []
    for s in xs.slabs:
        a, b = s.y0, s.y1
        if y_top is not None:
            b = min(b, y_top)
        if y_bot is not None:
            a = max(a, y_bot)
        if b > a:
            slabs.append(Slab(a, b, s.er, s.name))
    masks = [m for m in xs.masks if not ((m.side == "top" and y_top is not None) or
                                         (m.side == "bottom" and y_bot is not None))]
    out = CrossSection(slabs=slabs, conductors=keep, x_min=xs.x_min, x_max=xs.x_max, masks=masks,
                       air_above=0.0 if y_top is not None else xs.air_above,
                       air_below=0.0 if y_bot is not None else xs.air_below)
    return out
