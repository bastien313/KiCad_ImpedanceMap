"""Parseur S-expression minimal pour les fichiers KiCad (.kicad_pcb).

Représentation : une liste Python par parenthèse ; atomes = str (les chaînes entre
guillemets sont renvoyées sans guillemets). Aucune conversion numérique automatique.
Utilisé uniquement en lecture (analyse hors ligne, tests, repli si l'API ne fournit
pas un champ).
"""

from __future__ import annotations

import re
from typing import Iterator, List, Optional, Union

Node = Union[str, List["Node"]]

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text: str) -> List[Node]:
    stack: List[List[Node]] = [[]]
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"S-expression invalide à la position {pos}")
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            done = stack.pop()
            stack[-1].append(done)
        elif m.group(3) is not None:
            stack[-1].append(m.group(3).replace('\\"', '"').replace("\\\\", "\\"))
        elif m.group(4) is not None:
            stack[-1].append(m.group(4))
    if len(stack) != 1:
        raise ValueError("Parenthèses non équilibrées")
    return stack[0][0] if len(stack[0]) == 1 else stack[0]


def head(node: Node) -> Optional[str]:
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else None


def children(node: Node, name: str) -> Iterator[list]:
    if isinstance(node, list):
        for c in node:
            if head(c) == name:
                yield c


def child(node: Node, name: str) -> Optional[list]:
    return next(children(node, name), None)


def value(node: Node, name: str, idx: int = 1, default=None):
    c = child(node, name)
    if c is None or len(c) <= idx:
        return default
    return c[idx]


def fvalue(node: Node, name: str, idx: int = 1, default: float = 0.0) -> float:
    v = value(node, name, idx)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def xy(node: Node, name: str = "xy"):
    c = child(node, name) if head(node) != name else node
    if c is None:
        return None
    return float(c[1]), float(c[2])
