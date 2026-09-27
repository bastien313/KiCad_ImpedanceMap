"""Définition des cibles d'analyse et détection des paires différentielles.

Ordre de détection des paires (cahier des charges) :
  1. netclass : les nets dont la netclass définit des paramètres de paire
     (largeur/gap diff.) sont appariés en priorité ;
  2. suffixes : `_P/_N`, `+/-`, `P/N` (dernier caractère, ex. USB_DP / USB_DN).
Deux nets ne forment une paire que s'ils ont la même base après retrait du suffixe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from .board_model import BoardModel

SUFFIX_RULES = [
    ("_P", "_N"), ("_p", "_n"),
    ("+", "-"),
    ("P", "N"), ("p", "n"),
]


@dataclass
class Target:
    kind: str                       # "single" ou "pair"
    nets: Tuple[str, ...]           # (net,) ou (P, N)
    label: str
    z_target: float
    tol: float                      # fraction (0.10 = ±10 %)
    netclass: str = ""
    source: str = ""                # comment la cible a été définie

    @property
    def z_min(self) -> float:
        return self.z_target * (1 - self.tol)

    @property
    def z_max(self) -> float:
        return self.z_target * (1 + self.tol)


def _split_suffix(net: str):
    for pos, neg in SUFFIX_RULES:
        if net.endswith(pos) and len(net) > len(pos):
            return net[: -len(pos)], "P", (pos, neg)
        if net.endswith(neg) and len(net) > len(neg):
            return net[: -len(neg)], "N", (pos, neg)
    return None


def find_pairs(nets: Iterable[str], bm: Optional[BoardModel] = None) -> List[Tuple[str, str, str]]:
    """[(net_P, net_N, source)] ; source = "netclass" ou "suffixe"."""
    nets = [n for n in nets if n]
    netset = set(nets)
    used = set()
    pairs = []

    def dp_class(n):
        if bm is None:
            return None
        for cname in bm.netclass_all.get(n, [bm.netclass_of.get(n, "")]):
            nc = bm.netclasses.get(cname)
            if nc and cname != "Default" and (nc.dp_width or nc.dp_gap):
                return cname
        return None

    for pass_idx in (0, 1):
        for n in sorted(nets):
            if n in used:
                continue
            sp = _split_suffix(n)
            if not sp:
                continue
            base, pol, (pos, neg) = sp
            if pol != "P":
                continue
            m = base + neg
            if m not in netset or m in used:
                continue
            cp, cn = dp_class(n), dp_class(m)
            if pass_idx == 0 and not (cp and cp == cn):
                continue
            pairs.append((n, m, "netclass" if pass_idx == 0 else "suffixe"))
            used.update((n, m))
    return pairs


_Z_IN_NAME = re.compile(r"(?<!\d)(\d{2,3})\s*(?:R|Ω|ohm|OHM|Ohm)?(?!\d)")


def z_from_netclass(name: str) -> Optional[float]:
    """Impédance suggérée par le nom de netclass (« 90R », « USB-90R », « Z50 ») ou None."""
    if not name or name == "Default":
        return None
    for m in _Z_IN_NAME.finditer(name):
        v = float(m.group(1))
        if 20 <= v <= 200:
            return v
    return None


def pair_label(p: str, n: str) -> str:
    sp = _split_suffix(p)
    base = sp[0] if sp else p
    return f"{base} ({p[len(base):]}/{n[len(base):]})"


def build_targets(bm: BoardModel, *, nets: Sequence[str] = (), netclass: Optional[str] = None,
                  all_pairs: bool = False, z_single: float = 50.0, z_diff: float = 100.0, tol: float = 0.10,
                  pair_detection: bool = True, z_from_class: bool = True) -> List[Target]:
    """Construit les cibles.

    nets        : nets explicites (sélection ou liste) ; les nets appairables deviennent des paires
                  si pair_detection.
    netclass    : tous les nets routés de cette netclass.
    all_pairs   : toutes les paires différentielles du board.
    """
    routed = {s.net for s in bm.segs if s.net}
    chosen: List[str] = [n for n in nets if n in routed]
    if netclass:
        chosen += [n for n in routed if netclass in bm.netclass_all.get(n, [bm.netclass_of.get(n, "")])]
    targets: List[Target] = []
    used = set()
    pool = sorted(set(chosen))
    if pair_detection or all_pairs:
        # candidats : tout le board (all_pairs) ou les nets choisis + leur partenaire éventuel
        candidates = set(routed) if all_pairs else set(pool)
        if not all_pairs:
            for n in list(pool):
                sp = _split_suffix(n)
                if sp:
                    base, pol, (pos, neg) = sp
                    candidates.add(base + (neg if pol == "P" else pos))
        for p, n, src in find_pairs(sorted(candidates & routed), bm):
            if not all_pairs and p not in pool and n not in pool:
                continue
            if not pair_detection and not all_pairs:
                continue
            cls = bm.netclass_of.get(p, "")
            zt = z_diff
            if z_from_class:
                for c in bm.netclass_all.get(p, [cls]):
                    z = z_from_netclass(c)
                    if z:
                        zt = z
                        cls = c
                        break
            targets.append(Target("pair", (p, n), pair_label(p, n), zt, tol, cls, src))
            used.update((p, n))
    for n in pool:
        if n in used:
            continue
        cls = bm.netclass_of.get(n, "")
        zt = z_single
        if z_from_class:
            for c in bm.netclass_all.get(n, [cls]):
                z = z_from_netclass(c)
                if z:
                    zt = z
                    break
        targets.append(Target("single", (n,), n, zt, tol, cls, "net"))
    return targets
