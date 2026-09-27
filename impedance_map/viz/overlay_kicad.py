"""Application de l'overlay dans l'éditeur PCB (kipy) et effacement sélectif.

  * tous les objets sont créés dans UN commit nommé (un seul Ctrl+Z annule tout l'overlay) ;
  * ils sont réunis dans UN groupe nommé « ImpedanceMap <date> » (identifiable), créé dans un
    second commit : KiCad 10.0.6 abandonne silencieusement un groupe créé dans le même commit
    que ses nouveaux membres ;
  * « Effacer l'overlay » ne supprime QUE les membres des groupes dont le nom commence
    par le préfixe, après décompte et confirmation (callback), puis les groupes eux-mêmes ;
  * filet de sécurité : les identifiants créés sont aussi enregistrés dans un fichier JSON
    (dossier impedance_map/ du projet). Si la création du groupe échoue (version de KiCad),
    l'overlay est quand même créé et reste effaçable via ce fichier.
"""

from __future__ import annotations

import datetime
import json
import pathlib
from typing import Callable, Dict, List, Optional, Tuple

from .overlay_plan import OverlayOptions, OverlayPlan

NM_PER_M = 1e9
FALLBACK_LAYERS = [f"User.{i}" for i in range(1, 10)] + ["Eco1.User", "Eco2.User", "Cmts.User", "Dwgs.User"]


def _nm(v: float) -> int:
    return int(round(v * NM_PER_M))


def _v(p):
    from kipy.geometry import Vector2
    return Vector2.from_xy(_nm(p[0]), _nm(p[1]))


def resolve_layers(board, opt: OverlayOptions) -> Tuple[Dict[str, int], List[str]]:
    """Associe les couches demandées à des couches ACTIVES du board (repli si désactivée)."""
    from ..extraction.kipy_reader import layer_enum, layer_name
    enabled = {layer_name(l) for l in board.get_enabled_layers()}
    wanted = [opt.layer_ok, opt.layer_high, opt.layer_low, opt.layer_annot]
    notes = []
    mapping: Dict[str, int] = {}
    used = set()
    for name in wanted:
        if name in mapping:
            continue
        chosen = name if name in enabled else None
        if chosen is None:
            for alt in FALLBACK_LAYERS:
                if alt in enabled and alt not in used and alt not in wanted:
                    chosen = alt
                    break
            notes.append(f"Couche {name} désactivée dans le board : remplacée par {chosen or 'aucune'}")
        if chosen is None:
            raise RuntimeError(f"Aucune couche utilisateur disponible pour remplacer {name} "
                               "(activez des couches User dans Configuration du circuit > Couches).")
        used.add(chosen)
        mapping[name] = layer_enum(chosen)
    return mapping, notes


def apply_overlay(board, plan: OverlayPlan, opt: Optional[OverlayOptions] = None,
                  label: str = "", id_store=None) -> Dict[str, object]:
    """Crée les objets du plan dans le board ouvert. Retourne un résumé."""
    from kipy.board_types import BoardCircle, BoardSegment, BoardText, Group

    opt = opt or OverlayOptions()
    lmap, notes = resolve_layers(board, opt)
    items = []
    for s in plan.segs:
        g = BoardSegment()
        g.start, g.end = _v(s.a), _v(s.b)
        g.layer = lmap[s.layer]
        g.attributes.stroke.width = _nm(s.width)
        items.append(g)
    for c in plan.circles:
        g = BoardCircle()
        g.center = _v(c.c)
        g.radius_point = _v((c.c[0] + c.r, c.c[1]))
        g.layer = lmap[c.layer]
        g.attributes.stroke.width = _nm(c.width)
        items.append(g)
    for t in plan.texts:
        g = BoardText()
        g.value = t.text
        g.position = _v(t.pos)
        g.layer = lmap[t.layer]
        attrs = g.attributes
        from kipy.geometry import Vector2
        attrs.size = Vector2.from_xy(_nm(t.size), _nm(t.size))
        attrs.stroke_width = _nm(t.size * 0.12)
        g.attributes = attrs
        items.append(g)

    name = f"{opt.group_prefix} {datetime.datetime.now():%Y-%m-%d %H:%M:%S}" + (f" {label}" if label else "")
    # 1) objets : UN commit (un seul Ctrl+Z pour tout l'overlay)
    commit = board.begin_commit()
    try:
        created = board.create_items(items) if items else []
        board.push_commit(commit, f"Impedance Map : overlay ({len(created)} objets)")
    except Exception:
        board.drop_commit(commit)
        raise
    # 2) groupe : commit SÉPARÉ. Vérifié sur KiCad 10.0.6 : un groupe créé dans le même commit
    #    que ses membres (nouveaux) est accepté (ISC_OK) puis silencieusement abandonné.
    grouped = False
    if created:
        commit = board.begin_commit()
        try:
            grp = Group()
            grp._proto.name = name
            grp.items = created
            board.create_items([grp])
            board.push_commit(commit, "Impedance Map : groupe de l'overlay")
            grouped = any(g.name == name for g in board.get_groups())
        except Exception as e:  # noqa: BLE001 — le groupe est un confort, pas une nécessité
            board.drop_commit(commit)
            notes.append(f"Groupe non créé ({e}).")
        if not grouped:
            notes.append("Groupe absent après création : objets identifiés par le fichier d'identifiants.")
    if id_store is not None:
        _store_ids(id_store, name, [c.id.value for c in created])
    return {"group": name if grouped else None, "n_items": len(created), "notes": notes}


def _store_ids(path, name: str, ids: List[str]):
    path = pathlib.Path(path)
    try:
        d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        d = {}
    d[name] = ids
    try:
        path.write_text(json.dumps(d, indent=1), encoding="utf-8")
    except OSError:
        pass


def _stored_ids(path) -> List[str]:
    if path is None or not pathlib.Path(path).exists():
        return []
    try:
        d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [i for ids in d.values() for i in ids]


def find_overlay(board, prefix: str = "ImpedanceMap", id_store=None):
    """(groupes, identifiants KIID des objets) des overlays existants.

    Membres des groupes « prefix… » + identifiants du fichier `id_store` encore présents
    dans le board (vérifiés par get_items_by_id : jamais d'identifiant périmé supprimé).
    """
    from kipy.proto.common.types import KIID
    groups = [g for g in board.get_groups() if g.name.startswith(prefix)]
    ids: Dict[str, object] = {}
    for g in groups:
        for k in g._proto.items:
            ids[k.value] = k
    extra = [v for v in _stored_ids(id_store) if v not in ids]
    if extra:
        wanted = []
        for v in extra:
            k = KIID()
            k.value = v
            wanted.append(k)
        try:
            for it in board.get_items_by_id(wanted):
                ids[it.id.value] = it.id
        except Exception:  # noqa: BLE001 — KiCad 10.0.6 lève « none of the requested IDs were found »
            pass           # quand AUCUN identifiant n'existe (au lieu d'une liste vide) ; API absente : idem
    return groups, list(ids.values())


def clear_overlay(board, confirm: Callable[[int, int], bool], prefix: str = "ImpedanceMap", id_store=None) -> int:
    """Supprime les objets d'overlay après confirmation. Retourne le nombre supprimé."""
    groups, ids = find_overlay(board, prefix, id_store)
    if not groups and not ids:
        _drop_store(id_store)          # entrées périmées (objets déjà supprimés, Ctrl+Z…)
        return 0
    if not confirm(len(ids), len(groups)):
        return 0
    commit = board.begin_commit()
    try:
        if ids:
            board.remove_items_by_id(ids)
        if groups:
            board.remove_items_by_id([g.id for g in groups])
        board.push_commit(commit, f"Impedance Map : effacement de l'overlay ({len(ids)} objets)")
    except Exception:
        board.drop_commit(commit)
        raise
    _drop_store(id_store)
    return len(ids)


def _drop_store(id_store):
    if id_store is not None and pathlib.Path(id_store).exists():
        try:
            pathlib.Path(id_store).unlink()
        except OSError:
            pass
