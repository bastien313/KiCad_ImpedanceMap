"""Installe le plugin dans le dossier des plugins IPC de KiCad 10.

    python tools/install_plugin.py              # copie
    python tools/install_plugin.py --link       # lien symbolique (développement ; Windows : mode dev requis)
    python tools/install_plugin.py --dest DIR   # dossier explicite
    python tools/install_plugin.py --uninstall

Dossiers par défaut (${KICAD_DOCUMENTS_HOME}/10.0/plugins) :
  Windows : %USERPROFILE%\\Documents\\KiCad\\10.0\\plugins
  macOS   : ~/Documents/KiCad/10.0/plugins
  Linux   : ~/.local/share/kicad/10.0/plugins
  Flatpak : ~/.var/app/org.kicad.KiCad/data/kicad/10.0/plugins
KiCad crée ensuite le venv du plugin (--system-site-packages) et y installe requirements.txt
au premier chargement ; relancez KiCad ou « Recharger les plugins » (Préférences > Plugins).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import platform
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAME = "impedance-map"
FILES = ["plugin.json", "requirements.txt", "impedance_map_action.py", "impedance_map_clear.py", "README.md"]
DIRS = ["impedance_map", "icons"]
VERSION = "10.0"


def candidates():
    home = pathlib.Path.home()
    env = os.environ.get("KICAD_DOCUMENTS_HOME")
    out = []
    if env:
        out.append(pathlib.Path(env) / VERSION / "plugins")
    sysname = platform.system()
    if sysname == "Windows":
        docs = pathlib.Path(os.environ.get("USERPROFILE", home)) / "Documents"
        out.append(docs / "KiCad" / VERSION / "plugins")
    elif sysname == "Darwin":
        out.append(home / "Documents" / "KiCad" / VERSION / "plugins")
    else:
        flatpak = home / ".var" / "app" / "org.kicad.KiCad" / "data" / "kicad"
        if flatpak.exists():
            out.append(flatpak / VERSION / "plugins")
        out.append(home / ".local" / "share" / "kicad" / VERSION / "plugins")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest")
    ap.add_argument("--link", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()
    base = pathlib.Path(a.dest) if a.dest else candidates()[0]
    target = base / NAME
    if a.uninstall:
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        print("Désinstallé :", target)
        return 0
    base.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            target.unlink()
        else:
            shutil.rmtree(target)
    if a.link:
        os.symlink(ROOT, target, target_is_directory=True)
        print("Lien créé :", target, "->", ROOT)
    else:
        target.mkdir()
        for f in FILES:
            shutil.copy2(ROOT / f, target / f)
        for d in DIRS:
            shutil.copytree(ROOT / d, target / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print("Copié dans :", target)
    print("Relancez KiCad (ou Préférences > Plugins > Recharger). Activez aussi « Activer le serveur API ».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
