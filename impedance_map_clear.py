"""Action KiCad « Impedance Map : effacer l'overlay ».

Ne supprime QUE les membres des groupes dont le nom commence par « ImpedanceMap »,
après affichage du nombre d'objets et confirmation. Un seul commit (annulable par Ctrl+Z).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main():
    from impedance_map.ui import setup_logging
    from impedance_map.ui.controller import Controller
    setup_logging()
    try:
        import wx
        main.app = wx.App.Get() or wx.App(False)   # garder une référence à l'application wx

        def ask(msg, title="Impedance Map"):
            return wx.MessageBox(msg, title, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION) == wx.YES

        def info(msg):
            wx.MessageBox(msg, "Impedance Map", wx.OK | wx.ICON_INFORMATION)
    except ImportError:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()

        def ask(msg, title="Impedance Map"):
            return messagebox.askyesno(title, msg)

        def info(msg):
            messagebox.showinfo("Impedance Map", msg)

    ctl = Controller()
    try:
        ctl.connect()
        n, g = ctl.overlay_count()
        if n == 0 and g == 0:
            info("Aucun overlay Impedance Map dans ce board.")
            return 0
        k = ctl.clear_overlay(lambda n_items, n_groups: ask(
            f"Supprimer {n_items} objet(s) de {n_groups} groupe(s) « ImpedanceMap » ?\n"
            "Seuls les objets créés par ce plugin sont concernés (annulable par Ctrl+Z).",
            "Effacer l'overlay"))
        if k:
            info(f"{k} objet(s) supprimé(s).")
    except Exception as e:  # noqa: BLE001
        info(f"Erreur : {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
