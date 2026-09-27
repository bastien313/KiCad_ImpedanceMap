"""Interface de repli tkinter (Python sans wxPython, fréquent sous Linux).

Mêmes réglages que la version wx, présentation plus simple ; calcul dans un thread avec
barre de progression et bouton Annuler.
"""

from __future__ import annotations

import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from ..analysis import Cancelled
from .controller import Controller, UiSettings

USER_LAYERS = [f"User.{i}" for i in range(1, 10)] + ["Eco1.User", "Eco2.User", "Cmts.User", "Dwgs.User"]


class TkApp:
    def __init__(self, ctl: Controller, s: UiSettings):
        self.ctl, self.s = ctl, s
        self.root = tk.Tk()
        self.root.title("Impedance Map — impédance le long des pistes")
        self.v = {}
        self._build()

    def var(self, name, value, kind=tk.StringVar):
        v = kind(value=value)
        self.v[name] = v
        return v

    def _build(self):
        s = self.s
        r = self.root
        main = ttk.Frame(r, padding=8)
        main.grid(sticky="nsew")
        r.columnconfigure(0, weight=1)
        r.rowconfigure(0, weight=1)

        f = ttk.LabelFrame(main, text="Cible", padding=6)
        f.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=4, pady=4)
        tm = self.var("target_mode", s.target_mode if (self.ctl.live or s.target_mode != "selection") else "nets")
        for i, (val, lab) in enumerate([("selection", "Sélection courante"), ("nets", "Liste de nets"),
                                        ("netclass", "Netclass"), ("pairs", "Toutes les paires différentielles")]):
            rb = ttk.Radiobutton(f, text=lab, value=val, variable=tm)
            rb.grid(row=i, column=0, sticky="w")
            if val == "selection" and not self.ctl.live:
                rb.state(["disabled"])
        self.lb = tk.Listbox(f, selectmode=tk.MULTIPLE, height=12, exportselection=False)
        nets = self.ctl.routed_nets()
        for n in nets:
            self.lb.insert(tk.END, n)
            if n in s.nets:
                self.lb.selection_set(tk.END)
        self.lb.grid(row=4, column=0, sticky="nsew", pady=4)
        f.rowconfigure(4, weight=1)
        self.cls = ttk.Combobox(f, values=self.ctl.netclasses(), state="readonly")
        self.cls.set(s.netclass if s.netclass in self.ctl.netclasses() else (self.ctl.netclasses() or [""])[0])
        self.cls.grid(row=5, column=0, sticky="ew")
        ttk.Checkbutton(f, text="Détecter les paires", variable=self.var("detect_pairs", s.detect_pairs, tk.BooleanVar)
                        ).grid(row=6, column=0, sticky="w")

        f = ttk.LabelFrame(main, text="Impédance cible", padding=6)
        f.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        for i, (lab, name, val) in enumerate([("Simple (Ω)", "z_single", s.z_single), ("Diff. (Ω)", "z_diff", s.z_diff),
                                              ("Tolérance ± (%)", "tol_pct", s.tol_pct)]):
            ttk.Label(f, text=lab).grid(row=i, column=0, sticky="w")
            ttk.Entry(f, textvariable=self.var(name, f"{val:g}"), width=8).grid(row=i, column=1, sticky="w")
        ttk.Checkbutton(f, text="Impédance tirée du nom de netclass (ex. 90R)",
                        variable=self.var("z_from_netclass", s.z_from_netclass, tk.BooleanVar)).grid(row=3, column=0, columnspan=2, sticky="w")

        f = ttk.LabelFrame(main, text="Stackup", padding=6)
        f.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
        sm = self.var("stackup_mode", s.stackup_mode)
        ttk.Radiobutton(f, text="Stackup du board", value="board", variable=sm).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(f, text="Préréglage JLCPCB", value="jlcpcb", variable=sm).grid(row=1, column=0, sticky="w")
        self.jlc = ttk.Combobox(f, values=self.ctl.jlc_choices(), state="readonly", width=34)
        ch = self.ctl.jlc_choices()
        self.jlc.set(s.jlc_preset if s.jlc_preset in ch else next((c for c in ch if "7628" in c), (ch or [""])[0]))
        self.jlc.grid(row=2, column=0, sticky="ew")
        ttk.Radiobutton(f, text="Stackup perso (JSON)", value="custom", variable=sm).grid(row=3, column=0, sticky="w")
        row = ttk.Frame(f)
        row.grid(row=4, column=0, sticky="ew")
        ttk.Entry(row, textvariable=self.var("custom_path", s.custom_path), width=30).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="…", width=3, command=self._pick).pack(side=tk.LEFT)
        ttk.Button(f, text="Exporter le stackup choisi en JSON…", command=self._export).grid(row=5, column=0, sticky="w", pady=4)

        f = ttk.LabelFrame(main, text="Calcul", padding=6)
        f.grid(row=1, column=1, sticky="nsew", padx=4, pady=4)
        rows = [("Pas (mm)", "step_mm", s.step_mm), ("kw (fenêtre = max(kw·w, kh·h))", "window_w_factor", s.window_w_factor),
                ("kh", "window_h_factor", s.window_h_factor), ("Fenêtre fixe (mm, 0 = auto)", "window_fixed",
                                                              s.window_mm if s.window_mode == "fixed" else 0),
                ("Facteur de gravure (0 = rect.)", "etch_factor", s.etch_factor), ("Processus (0 = auto)", "workers", s.workers)]
        for i, (lab, name, val) in enumerate(rows):
            ttk.Label(f, text=lab).grid(row=i, column=0, sticky="w")
            ttk.Entry(f, textvariable=self.var(name, f"{val:g}"), width=8).grid(row=i, column=1, sticky="w")
        md = self.var("mode", s.mode)
        ttk.Radiobutton(f, text="Rapide", value="fast", variable=md).grid(row=6, column=0, sticky="w")
        ttk.Radiobutton(f, text="Précis", value="precise", variable=md).grid(row=6, column=1, sticky="w")

        f = ttk.LabelFrame(main, text="Sorties", padding=6)
        f.grid(row=2, column=1, sticky="nsew", padx=4, pady=4)
        cbs = [("Overlay dans l'éditeur", "do_overlay", s.do_overlay and self.ctl.live),
               ("Remplacer l'overlay précédent", "clear_previous", s.clear_previous),
               ("Rapport HTML", "do_report", s.do_report), ("Ouvrir le rapport", "open_report", s.open_report)]
        for i, (lab, name, val) in enumerate(cbs):
            cb = ttk.Checkbutton(f, text=lab, variable=self.var(name, val, tk.BooleanVar))
            cb.grid(row=i, column=0, columnspan=2, sticky="w")
            if name in ("do_overlay", "clear_previous") and not self.ctl.live:
                cb.state(["disabled"])
        for i, (lab, name, val) in enumerate([("Dans tol.", "layer_ok", s.layer_ok), ("Trop haut", "layer_high", s.layer_high),
                                              ("Trop bas", "layer_low", s.layer_low), ("Étiquettes", "layer_annot", s.layer_annot)]):
            ttk.Label(f, text=lab).grid(row=4 + i, column=0, sticky="w")
            ttk.Combobox(f, values=USER_LAYERS, textvariable=self.var(name, val), state="readonly", width=10
                         ).grid(row=4 + i, column=1, sticky="w")

        from ..analysis.signal import PRESETS
        f = ttk.LabelFrame(main, text="Intégrité du signal (rapport)", padding=6)
        f.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=4, pady=4)
        ttk.Checkbutton(f, text="Calculer |S11|, perte de désadaptation et TDR",
                        variable=self.var("sig_enabled", s.sig_enabled, tk.BooleanVar)).grid(row=0, column=0, columnspan=4, sticky="w")
        self.sig = ttk.Combobox(f, values=list(PRESETS), state="readonly", width=36)
        self.sig.set(s.sig_preset if s.sig_preset in PRESETS else list(PRESETS)[0])
        self.sig.grid(row=1, column=0, columnspan=4, sticky="w")

        def on_preset(_e=None):
            sp = PRESETS[self.sig.get()]
            if sp.kind == "digital":
                self.v["sig_bitrate_mbps"].set(f"{sp.bitrate / 1e6:g}")
                self.v["sig_rise_ps"].set(f"{sp.rise * 1e12:.0f}")
        self.sig.bind("<<ComboboxSelected>>", on_preset)
        for i, (lab, name, val) in enumerate([("Débit (Mb/s)", "sig_bitrate_mbps", s.sig_bitrate_mbps),
                                              ("Montée (ps)", "sig_rise_ps", s.sig_rise_ps),
                                              ("Fréq. RF (MHz)", "sig_freq_mhz", s.sig_freq_mhz),
                                              ("Zref (Ω, 0 = cible)", "sig_zref", s.sig_zref)]):
            ttk.Label(f, text=lab).grid(row=2 + i // 2, column=(i % 2) * 2, sticky="w")
            ttk.Entry(f, textvariable=self.var(name, f"{val:g}"), width=8).grid(row=2 + i // 2, column=(i % 2) * 2 + 1, sticky="w")

        b = ttk.Frame(main)
        b.grid(row=4, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Button(b, text="Analyser", command=self.on_run).pack(side=tk.LEFT, padx=3)
        clr = ttk.Button(b, text="Effacer l'overlay…", command=self.on_clear)
        clr.pack(side=tk.LEFT, padx=3)
        if not self.ctl.live:
            clr.state(["disabled"])
        ttk.Button(b, text="Fermer", command=self.root.destroy).pack(side=tk.RIGHT, padx=3)
        self.status = ttk.Label(main, text=("Mode direct : " + self.ctl.board.name) if self.ctl.live
                                else f"Mode fichier : {self.ctl.pcb_path}")
        self.status.grid(row=5, column=0, columnspan=2, sticky="w")

    def _pick(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if p:
            self.v["custom_path"].set(p)
            self.v["stackup_mode"].set("custom")

    def _export(self):
        try:
            st = self.ctl.stackup_for(self._read())
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Stackup", str(e))
            return
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if p:
            st.to_json(p)

    def _f(self, name, default):
        try:
            return float(str(self.v[name].get()).replace(",", "."))
        except ValueError:
            return default

    def _read(self) -> UiSettings:
        s = self.s
        s.target_mode = self.v["target_mode"].get()
        s.nets = [self.lb.get(i) for i in self.lb.curselection()]
        s.netclass = self.cls.get()
        s.detect_pairs = self.v["detect_pairs"].get()
        s.z_single, s.z_diff, s.tol_pct = self._f("z_single", 50), self._f("z_diff", 100), self._f("tol_pct", 10)
        s.z_from_netclass = self.v["z_from_netclass"].get()
        s.stackup_mode = self.v["stackup_mode"].get()
        s.jlc_preset = self.jlc.get()
        s.custom_path = self.v["custom_path"].get()
        s.step_mm = self._f("step_mm", 0.5)
        s.window_w_factor, s.window_h_factor = self._f("window_w_factor", 10), self._f("window_h_factor", 6)
        wf = self._f("window_fixed", 0)
        s.window_mode, s.window_mm = ("fixed", wf) if wf > 0 else ("auto", s.window_mm)
        s.etch_factor = self._f("etch_factor", 0)
        s.workers = int(self._f("workers", 0))
        s.mode = self.v["mode"].get()
        for k in ("do_overlay", "clear_previous", "do_report", "open_report"):
            setattr(s, k, bool(self.v[k].get()))
        for k in ("layer_ok", "layer_high", "layer_low", "layer_annot"):
            setattr(s, k, self.v[k].get())
        s.sig_enabled = bool(self.v["sig_enabled"].get())
        s.sig_preset = self.sig.get()
        s.sig_bitrate_mbps = self._f("sig_bitrate_mbps", 480)
        s.sig_rise_ps = self._f("sig_rise_ps", 500)
        s.sig_freq_mhz = self._f("sig_freq_mhz", 1000)
        s.sig_zref = self._f("sig_zref", 0)
        return s

    def on_run(self):
        s = self._read()
        s.save()
        uz = self.ctl.bm.unfilled_zones()
        if uz and not messagebox.askyesno("Zones non remplies",
                                          f"{len(uz)} zone(s) non remplie(s) seront ignorées. Remplissez les zones "
                                          "(touche B) pour un résultat correct.\nContinuer quand même ?"):
            return
        cancel = threading.Event()
        top = tk.Toplevel(self.root)
        top.title("Analyse en cours")
        top.transient(self.root)
        msg = ttk.Label(top, text="Préparation…", width=60)
        msg.pack(padx=10, pady=6)
        bar = ttk.Progressbar(top, maximum=1000, length=420)
        bar.pack(padx=10, pady=6)
        ttk.Button(top, text="Annuler", command=cancel.set).pack(pady=6)
        state = {"frac": 0.0, "msg": "", "done": False, "run": None, "err": None}

        def progress(f, m):
            state["frac"], state["msg"] = f, m

        def work():
            try:
                state["run"] = self.ctl.run(s, progress, cancel)
            except Cancelled:
                state["err"] = "cancel"
            except Exception as e:  # noqa: BLE001
                state["err"] = f"{e}\n\n{traceback.format_exc()}"
            finally:
                state["done"] = True

        threading.Thread(target=work, daemon=True).start()

        def poll():
            bar["value"] = int(state["frac"] * 1000)
            msg["text"] = state["msg"] or "…"
            if not state["done"]:
                self.root.after(100, poll)
                return
            top.destroy()
            self._finish(s, state)

        poll()

    def _finish(self, s, state):
        if state["err"] == "cancel":
            self.status["text"] = "Analyse annulée."
            return
        if state["err"]:
            messagebox.showerror("Impedance Map", "Échec de l'analyse :\n" + state["err"][:3000])
            return
        run = state["run"]
        out = [f"Analyse terminée en {run.timing['total_s']:.1f} s."]
        try:
            if s.do_overlay and self.ctl.live:
                if s.clear_previous:
                    self.ctl.clear_overlay(lambda n, g: True)
                r = self.ctl.apply_overlay(run, s)
                out.append(f"Overlay : {r['n_items']} objets ({r['group'] or 'sans groupe'}).")
            if s.do_report:
                out.append(f"Rapport : {self.ctl.write_report(run, s.open_report)}")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Impedance Map", f"Erreur de sortie : {e}")
        for tr in run.targets:
            st = tr.stats
            if "z_mean" in st:
                out.append(f"• {tr.label} : {st['z_min']:.1f}/{st['z_mean']:.1f}/{st['z_max']:.1f} Ω, "
                           f"{st['pct_out']:.0f} % hors tolérance")
        self.status["text"] = out[0]
        messagebox.showinfo("Impedance Map", "\n".join(out[:30]))

    def on_clear(self):
        def confirm(n, g):
            return messagebox.askyesno("Effacer l'overlay", f"Supprimer {n} objet(s) de {g} groupe(s) « ImpedanceMap » ?")
        try:
            k = self.ctl.clear_overlay(confirm)
            self.status["text"] = f"{k} objet(s) supprimé(s)."
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Impedance Map", str(e))

    def mainloop(self):
        self.root.mainloop()


def run_tk(ctl: Controller, s: UiSettings):
    app = TkApp(ctl, s)
    app.mainloop()
