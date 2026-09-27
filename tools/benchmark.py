"""Mesure de performance (objectif : paire différentielle de 100 mm en < 30 s en mode rapide).

    python tools/benchmark.py                         # board d'exemple
    python tools/benchmark.py chemin/board.kicad_pcb  # toutes les paires d'un autre board

Cache disque désactivé : chaque coupe unique est réellement résolue. Trois mesures :
  1. paire USB du board d'exemple (géométrie idéale : le cache par coupe élimine presque tout) ;
  2. « pire cas » : la même paire avec pas de 0,5 mm mais cache par coupe NEUTRALISÉ
     (chaque échantillon résolu individuellement) ;
  3. optionnel : toutes les paires d'un board réel.
"""

from __future__ import annotations

import os
import pathlib
import platform
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impedance_map.analysis import AnalysisOptions, Engine  # noqa: E402
from impedance_map.extraction.file_reader import read_kicad_pcb  # noqa: E402
from impedance_map.extraction.targets import build_targets  # noqa: E402


def run(bm, targets, mode, workers, nodedup=False):
    opt = AnalysisOptions(mode=mode, workers=workers, persist_cache=False)
    eng = Engine(bm, bm.stackup, opt)
    if nodedup:
        # une clé unique par échantillon : on perturbe le quantum de hachage via un suffixe
        import impedance_map.analysis.engine as E
        orig = E.CrossSection.quantized_key
        counter = {"i": 0}

        def unique_key(self, quantum=1e-7):
            counter["i"] += 1
            return orig(self, quantum) + f"-{counter['i']}"

        E.CrossSection.quantized_key = unique_key
        try:
            t = time.perf_counter()
            r = eng.run(targets)
            dt = time.perf_counter() - t
        finally:
            E.CrossSection.quantized_key = orig
        return r, dt
    t = time.perf_counter()
    r = eng.run(targets)
    return r, time.perf_counter() - t


def main():
    print(f"Machine : {platform.processor() or platform.machine()} — {os.cpu_count()} cœurs — Python {platform.python_version()}")
    bm = read_kicad_pcb(ROOT / "examples" / "demo_impedance.kicad_pcb")
    tg = build_targets(bm, nets=["USB_P"])
    for mode in ("fast", "precise"):
        r, dt = run(bm, tg, mode, None)
        st = r.targets[0].stats
        print(f"[démo, {mode:7}] paire USB {st['length_mm']:.1f} mm : {dt:5.2f} s — {r.timing['n_cuts']} coupes, "
              f"{r.timing['n_unique']} uniques ; Zdiff moyen {st['z_mean']:.2f} Ω")
    for mode in ("fast",):
        r, dt = run(bm, tg, mode, None, nodedup=True)
        st = r.targets[0].stats
        print(f"[pire cas, {mode}] paire USB {st['length_mm']:.1f} mm, 1 résolution par échantillon : {dt:5.2f} s — "
              f"{r.timing['n_solved']} résolutions ({dt / max(r.timing['n_solved'], 1) * 1e3:.0f} ms/coupe en moyenne, parallélisé)")
    if len(sys.argv) > 1:
        bm2 = read_kicad_pcb(sys.argv[1])
        tg2 = build_targets(bm2, all_pairs=True)
        r, dt = run(bm2, tg2, "fast", None)
        L = sum(t.stats.get("length_mm", 0) for t in r.targets)
        print(f"[{bm2.name}] {len(tg2)} paires, {L:.0f} mm : {dt:.2f} s — {r.timing['n_unique']} coupes uniques "
              f"→ {dt / max(L, 1) * 100:.1f} s / 100 mm")


if __name__ == "__main__":
    main()
