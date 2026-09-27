"""Logique de l'interface, indépendante du toolkit (wx ou tkinter).

Deux modes :
  * direct  : KiCad lancé, board ouvert, API IPC (cas du plugin) ;
  * fichier : un .kicad_pcb sur disque (tests, démonstration, CLI) — pas d'overlay.
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import threading
import webbrowser
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from ..analysis import AnalysisOptions, AnalysisRun, Engine
from ..extraction.board_model import BoardModel
from ..extraction.targets import Target, build_targets, find_pairs, pair_label
from ..stackup import Stackup, jlcpcb_presets, load_stackup
from ..viz.overlay_plan import OverlayOptions, plan_overlay

log = logging.getLogger("impedance_map")


def settings_path() -> pathlib.Path:
    from ..analysis.cache import default_cache_dir
    return default_cache_dir() / "settings.json"


@dataclass
class UiSettings:
    """Réglages de la boîte de dialogue (mémorisés entre deux lancements)."""
    target_mode: str = "selection"         # selection / nets / netclass / pairs
    nets: List[str] = field(default_factory=list)
    netclass: str = ""
    detect_pairs: bool = True
    z_single: float = 50.0
    z_diff: float = 100.0
    z_from_netclass: bool = True
    tol_pct: float = 10.0
    stackup_mode: str = "board"             # board / jlcpcb / custom
    jlc_preset: str = ""
    custom_path: str = ""
    step_mm: float = 0.5
    window_mode: str = "auto"               # auto / fixed
    window_w_factor: float = 10.0
    window_h_factor: float = 6.0
    window_mm: float = 3.0
    mode: str = "fast"
    etch_factor: float = 0.0
    workers: int = 0                        # 0 = auto
    do_overlay: bool = True
    clear_previous: bool = True
    do_report: bool = True
    open_report: bool = True
    layer_ok: str = "User.1"
    layer_high: str = "User.2"
    layer_low: str = "User.3"
    layer_annot: str = "User.4"
    sig_enabled: bool = True
    sig_preset: str = "USB 2.0 High-Speed (480 Mb/s)"
    sig_bitrate_mbps: float = 480.0
    sig_rise_ps: float = 500.0
    sig_freq_mhz: float = 1000.0
    sig_zref: float = 0.0                   # 0 = impédance cible de chaque piste/paire

    @staticmethod
    def load() -> "UiSettings":
        p = settings_path()
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            known = {k: v for k, v in d.items() if k in UiSettings.__dataclass_fields__}
            return UiSettings(**known)
        except (OSError, ValueError, TypeError):
            return UiSettings()

    def save(self):
        p = settings_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(asdict(self), indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def analysis_options(self) -> AnalysisOptions:
        return AnalysisOptions(
            step=self.step_mm * 1e-3,
            window=self.window_mm * 1e-3 if self.window_mode == "fixed" else None,
            window_w_factor=self.window_w_factor, window_h_factor=self.window_h_factor,
            mode=self.mode, etch_factor=self.etch_factor, workers=self.workers or None)

    def signal_spec(self):
        from ..analysis.signal import PRESETS, SignalSpec
        base = PRESETS.get(self.sig_preset, PRESETS["Numérique personnalisé"])
        name = base.name if self.sig_preset != "Numérique personnalisé" else \
            f"Numérique {self.sig_bitrate_mbps:g} Mb/s"
        if base.kind == "rf":
            name = f"RF {self.sig_freq_mhz:g} MHz"
        return SignalSpec(name, base.kind, self.sig_bitrate_mbps * 1e6, self.sig_rise_ps * 1e-12,
                          self.sig_freq_mhz * 1e6, self.sig_zref or None)

    def overlay_options(self) -> OverlayOptions:
        return OverlayOptions(layer_ok=self.layer_ok, layer_high=self.layer_high, layer_low=self.layer_low,
                              layer_annot=self.layer_annot)


class Controller:
    def __init__(self, pcb_path: Optional[str] = None):
        self.pcb_path = pcb_path
        self.kicad = None
        self.board = None
        self.bm: Optional[BoardModel] = None
        self.board_stackup: Optional[Stackup] = None
        self.last_run: Optional[AnalysisRun] = None
        self.last_engine: Optional[Engine] = None
        self.notes: List[str] = []

    # ------------------------------------------------------------------ connexion / lecture
    @property
    def live(self) -> bool:
        return self.board is not None

    def connect(self):
        """Mode direct : connexion à KiCad (variables KICAD_API_SOCKET/TOKEN fournies par KiCad)."""
        from ..extraction.kipy_reader import connect
        self.kicad = connect()
        self.board = self.kicad.get_board()
        if self.board is None:
            raise RuntimeError("Aucun board ouvert dans l'éditeur PCB.")

    def load(self, progress: Optional[Callable[[str], None]] = None) -> BoardModel:
        if self.board is not None:
            from ..extraction.kipy_reader import read_board
            self.board_stackup = self._board_stackup_live()
            self.bm = read_board(self.board, self.board_stackup, progress)
        else:
            from ..extraction.file_reader import read_kicad_pcb
            self.bm = read_kicad_pcb(self.pcb_path)
            self.board_stackup = self.bm.stackup
        return self.bm

    def _board_stackup_live(self) -> Stackup:
        from ..stackup.sources import from_kicad_pcb_tree, from_kipy
        try:
            return from_kipy(self.board)
        except (AttributeError, ValueError) as e:
            # kicad-python trop ancien (< 0.8) ou KiCad < 10.0.6 : repli sur le fichier disque
            path = self.board_file()
            self.notes.append(f"Stackup lu depuis le fichier ({e}) : sauvegardez le board si vous l'avez modifié.")
            from ..extraction import sexpr
            return from_kicad_pcb_tree(sexpr.parse(pathlib.Path(path).read_text(encoding="utf-8")))

    def board_file(self) -> Optional[pathlib.Path]:
        if self.board is not None:
            return pathlib.Path(self.board.document.project.path) / self.board.name
        return pathlib.Path(self.pcb_path) if self.pcb_path else None

    # ------------------------------------------------------------------ listes pour l'interface
    def routed_nets(self) -> List[str]:
        return sorted({s.net for s in self.bm.segs if s.net})

    def netclasses(self) -> List[str]:
        used = set()
        for n in self.routed_nets():
            used.update(self.bm.netclass_all.get(n, [self.bm.netclass_of.get(n, "Default")]))
        return sorted(used)

    def pairs(self) -> List[str]:
        return [pair_label(p, n) for p, n, _ in find_pairs(self.routed_nets(), self.bm)]

    def selection_nets(self) -> List[str]:
        if self.board is None:
            return []
        nets = set()
        for it in self.board.get_selection():
            net = getattr(it, "net", None)
            name = getattr(net, "name", "") if net is not None else ""
            if name:
                nets.add(name)
        return sorted(nets)

    def jlc_choices(self) -> List[str]:
        n = len(self.bm.copper_names) if self.bm else None
        return jlcpcb_presets(n)

    def stackup_for(self, s: UiSettings) -> Stackup:
        names = self.bm.copper_names
        if s.stackup_mode == "jlcpcb":
            if not s.jlc_preset:
                raise ValueError("Choisissez un préréglage JLCPCB.")
            return load_stackup("jlcpcb:" + s.jlc_preset, names)
        if s.stackup_mode == "custom":
            if not s.custom_path:
                raise ValueError("Choisissez un fichier de stackup JSON.")
            return load_stackup(s.custom_path, names)
        return self.board_stackup

    # ------------------------------------------------------------------ cibles
    def targets_for(self, s: UiSettings) -> List[Target]:
        kw = dict(z_single=s.z_single, z_diff=s.z_diff, tol=s.tol_pct / 100.0, pair_detection=s.detect_pairs,
                  z_from_class=s.z_from_netclass)
        if s.target_mode == "pairs":
            return build_targets(self.bm, all_pairs=True, **kw)
        if s.target_mode == "netclass":
            return build_targets(self.bm, netclass=s.netclass, **kw)
        if s.target_mode == "selection":
            nets = self.selection_nets()
            if not nets:
                raise ValueError("La sélection courante ne contient aucune piste : sélectionnez des pistes "
                                 "dans l'éditeur ou choisissez une autre cible.")
            return build_targets(self.bm, nets=nets, **kw)
        return build_targets(self.bm, nets=s.nets, **kw)

    # ------------------------------------------------------------------ calcul
    def run(self, s: UiSettings, progress: Callable[[float, str], None], cancel: threading.Event) -> AnalysisRun:
        targets = self.targets_for(s)
        if not targets:
            raise ValueError("Aucune cible routée à analyser.")
        st = self.stackup_for(s)
        eng = Engine(self.bm, st, s.analysis_options(), progress=progress, cancel=cancel)
        run = eng.run(targets)
        run.warnings = self.notes + run.warnings
        if s.sig_enabled:
            from ..analysis.signal import analyze_run
            progress(1.0, "Intégrité du signal")
            analyze_run(run, s.signal_spec())
        self.last_run, self.last_engine = run, eng
        return run

    # ------------------------------------------------------------------ sorties
    def output_dir(self) -> pathlib.Path:
        f = self.board_file()
        d = (f.parent if f else pathlib.Path.cwd()) / "impedance_map"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_report(self, run: AnalysisRun, open_it: bool = True) -> pathlib.Path:
        from ..viz.report import build_report
        stem = pathlib.Path(run.board).stem
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        d = self.output_dir()
        html_path = d / f"{stem}_impedance_{ts}.html"
        build_report(run, self.last_engine, self.bm, path=html_path)
        run.to_json(d / f"{stem}_impedance_{ts}.json")
        if open_it:
            webbrowser.open(html_path.as_uri())
        return html_path

    def overlay_id_store(self) -> pathlib.Path:
        return self.output_dir() / f"{pathlib.Path(self.board.name).stem}_overlay_ids.json"

    def overlay_count(self, prefix: str = "ImpedanceMap"):
        from ..viz.overlay_kicad import find_overlay
        groups, ids = find_overlay(self.board, prefix, self.overlay_id_store())
        return len(ids), len(groups)

    def apply_overlay(self, run: AnalysisRun, s: UiSettings) -> Dict[str, object]:
        if not self.live:
            raise RuntimeError("Overlay disponible uniquement avec KiCad (mode direct).")
        from ..viz.overlay_kicad import apply_overlay
        opt = s.overlay_options()
        return apply_overlay(self.board, plan_overlay(run, opt), opt, id_store=self.overlay_id_store())

    def clear_overlay(self, confirm: Callable[[int, int], bool]) -> int:
        if not self.live:
            return 0
        from ..viz.overlay_kicad import clear_overlay
        return clear_overlay(self.board, confirm, id_store=self.overlay_id_store())
