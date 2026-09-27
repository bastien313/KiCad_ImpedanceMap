"""Point d'entrée du plugin IPC KiCad « Impedance Map » (déclaré dans plugin.json).

KiCad lance ce script avec l'interpréteur du venv du plugin, le dossier du plugin
comme répertoire courant et les variables KICAD_API_SOCKET / KICAD_API_TOKEN.

Usage hors KiCad (test de l'interface sur un fichier) :
    python impedance_map_action.py chemin/vers/board.kicad_pcb [--tk]
"""

import multiprocessing
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main():
    from impedance_map.ui import run_plugin
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    return run_plugin(args[0] if args else None, force_tk="--tk" in sys.argv)


if __name__ == "__main__":
    multiprocessing.freeze_support()   # nécessaire au calcul parallèle (spawn) sous Windows
    sys.exit(main())
