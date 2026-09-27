"""Cache des résultats de coupe (clé = hash de la géométrie quantifiée + mode du solveur).

Mémoire (dict) + persistance disque optionnelle (JSON dans le dossier cache utilisateur),
pour que les relances sur un board peu modifié soient quasi instantanées.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
from typing import Dict, Optional

from ..solver.lines import LineResult

CACHE_VERSION = 3   # à incrémenter si le solveur ou la construction des coupes change


def default_cache_dir() -> pathlib.Path:
    if os.name == "nt":
        base = pathlib.Path(os.environ.get("LOCALAPPDATA", pathlib.Path.home() / "AppData" / "Local"))
    else:
        base = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache"))
    return base / "impedance_map"


class ResultCache:
    def __init__(self, path: Optional[pathlib.Path] = None, persist: bool = True):
        self.mem: Dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        self.lock = threading.Lock()
        self.path = None
        if persist:
            d = path or default_cache_dir()
            try:
                d.mkdir(parents=True, exist_ok=True)
                self.path = d / f"cuts_v{CACHE_VERSION}.json"
                if self.path.exists():
                    self.mem = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.mem = {}

    @staticmethod
    def key(geom_key: str, mode: str) -> str:
        return f"{mode}:{geom_key}"

    def get(self, key: str) -> Optional[LineResult]:
        with self.lock:
            d = self.mem.get(key)
        if d is None:
            return None
        d = dict(d)
        d["z_self"] = tuple(d.get("z_self") or ())
        return LineResult(**d)

    def put(self, key: str, res: LineResult):
        with self.lock:
            self.mem[key] = res.to_dict()

    def save(self, max_entries: int = 50000):
        if not self.path:
            return
        try:
            items = list(self.mem.items())[-max_entries:]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(items)), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass
