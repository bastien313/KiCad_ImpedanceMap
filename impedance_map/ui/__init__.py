"""Interface du plugin : wxPython si disponible, sinon tkinter."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import traceback

from .controller import Controller, UiSettings

log = logging.getLogger("impedance_map")


def setup_logging():
    from ..analysis.cache import default_cache_dir
    d = default_cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=str(d / "plugin.log"), level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    except OSError:
        logging.basicConfig(level=logging.INFO)
    # pythonw.exe (Windows) : stdout/stderr valent None -> éviter les plantages sur print
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def _fatal(msg: str, use_wx: bool):
    log.error(msg)
    try:
        if use_wx:
            import wx
            _ = wx.App.Get() or wx.App(False)
            wx.MessageBox(msg, "Impedance Map", wx.OK | wx.ICON_ERROR)
            return
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("Impedance Map", msg)
        r.destroy()
    except Exception:  # noqa: BLE001
        print(msg, file=sys.stderr)


def run_plugin(pcb_path: str | None = None, force_tk: bool = False):
    """Point d'entrée : mode direct (KiCad) si pcb_path est None, sinon mode fichier."""
    setup_logging()
    use_wx = not force_tk and importlib.util.find_spec("wx") is not None
    ctl = Controller(pcb_path)
    try:
        if pcb_path is None:
            ctl.connect()
        ctl.load()
    except Exception as e:  # noqa: BLE001
        hint = ""
        s = str(e).lower()
        if "busy" in s or "occupé" in s:
            hint = "\n\nKiCad est occupé : appuyez sur Échap, fermez les dialogues et cliquez dans le canevas."
        elif "connect" in s or "timed out" in s or "nng" in s:
            hint = ("\n\nVérifiez que le serveur API est activé : Préférences > Plugins > "
                    "« Activer le serveur API », puis redémarrez KiCad.")
        _fatal(f"Impossible de lire le board : {e}{hint}\n\n{traceback.format_exc()[-1500:]}", use_wx)
        return 1
    settings = UiSettings.load()
    if use_wx:
        from .wx_dialog import run_wx
        run_wx(ctl, settings)
    else:
        from .tk_dialog import run_tk
        run_tk(ctl, settings)
    return 0
