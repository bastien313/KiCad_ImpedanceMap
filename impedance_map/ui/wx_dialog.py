"""Boîte de dialogue wxPython du plugin (interface principale)."""

from __future__ import annotations

import threading
import traceback

import wx
import wx.grid

from ..analysis import Cancelled
from ..stackup import Stackup
from .controller import Controller, UiSettings

USER_LAYERS = [f"User.{i}" for i in range(1, 10)] + ["Eco1.User", "Eco2.User", "Cmts.User", "Dwgs.User"]


def _num(ctrl: wx.TextCtrl, default: float) -> float:
    try:
        return float(ctrl.GetValue().replace(",", "."))
    except ValueError:
        return default


class ImpedanceDialog(wx.Dialog):
    def __init__(self, ctl: Controller, settings: UiSettings):
        super().__init__(None, title="Impedance Map — impédance le long des pistes",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.ctl = ctl
        self.s = settings
        self._build()
        self._load_settings()
        self.SetMinSize((720, 640))
        self.Fit()
        self.CentreOnScreen()

    # ================================================================== construction
    def _build(self):
        root = wx.BoxSizer(wx.VERTICAL)
        cols = wx.BoxSizer(wx.HORIZONTAL)
        left = wx.BoxSizer(wx.VERTICAL)
        right = wx.BoxSizer(wx.VERTICAL)

        # --- cible
        box = wx.StaticBoxSizer(wx.VERTICAL, self, "Cible")
        p = box.GetStaticBox()
        self.rb_sel = wx.RadioButton(p, label="Sélection courante", style=wx.RB_GROUP)
        self.rb_nets = wx.RadioButton(p, label="Liste de nets")
        self.rb_cls = wx.RadioButton(p, label="Netclass")
        self.rb_pairs = wx.RadioButton(p, label="Toutes les paires différentielles")
        for rb in (self.rb_sel, self.rb_nets, self.rb_cls, self.rb_pairs):
            box.Add(rb, 0, wx.ALL, 2)
            rb.Bind(wx.EVT_RADIOBUTTON, lambda e: self._update_enable())
        self.filter = wx.SearchCtrl(p)
        self.filter.ShowCancelButton(True)
        self.filter.SetDescriptiveText("Filtrer les nets")
        self.filter.Bind(wx.EVT_TEXT, lambda e: self._fill_nets())
        box.Add(self.filter, 0, wx.EXPAND | wx.ALL, 2)
        self.net_list = wx.CheckListBox(p, size=(-1, 170))
        box.Add(self.net_list, 1, wx.EXPAND | wx.ALL, 2)
        self.cls_choice = wx.Choice(p, choices=self.ctl.netclasses())
        box.Add(self.cls_choice, 0, wx.EXPAND | wx.ALL, 2)
        self.cb_pairs = wx.CheckBox(p, label="Détecter les paires (netclass puis suffixes _P/_N, +/-, P/N)")
        box.Add(self.cb_pairs, 0, wx.ALL, 2)
        pairs = self.ctl.pairs()
        box.Add(wx.StaticText(p, label=f"{len(pairs)} paire(s) détectée(s) : " + (", ".join(pairs[:4]) +
                                                                                   (" …" if len(pairs) > 4 else ""))),
                0, wx.ALL, 2)
        left.Add(box, 1, wx.EXPAND | wx.ALL, 4)

        # --- impédance
        box = wx.StaticBoxSizer(wx.VERTICAL, self, "Impédance cible")
        p = box.GetStaticBox()
        g = wx.FlexGridSizer(2, 4, 4, 6)
        self.t_zs = wx.TextCtrl(p, size=(60, -1))
        self.t_zd = wx.ComboBox(p, choices=["90", "100"], size=(70, -1))
        self.t_tol = wx.TextCtrl(p, size=(60, -1))
        g.AddMany([(wx.StaticText(p, label="Simple (Ω)"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_zs,),
                   (wx.StaticText(p, label="Différentielle (Ω)"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_zd,),
                   (wx.StaticText(p, label="Tolérance ± (%)"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_tol,)])
        box.Add(g, 0, wx.ALL, 2)
        self.cb_zcls = wx.CheckBox(p, label="Utiliser l'impédance du nom de netclass (ex. « USB_90R » → 90 Ω)")
        box.Add(self.cb_zcls, 0, wx.ALL, 2)
        left.Add(box, 0, wx.EXPAND | wx.ALL, 4)

        # --- stackup
        box = wx.StaticBoxSizer(wx.VERTICAL, self, "Stackup")
        p = box.GetStaticBox()
        self.rs_board = wx.RadioButton(p, label="Stackup du board", style=wx.RB_GROUP)
        self.rs_jlc = wx.RadioButton(p, label="Préréglage JLCPCB")
        self.rs_custom = wx.RadioButton(p, label="Stackup perso (JSON)")
        self.jlc_choice = wx.Choice(p, choices=self.ctl.jlc_choices())
        self.custom_file = wx.FilePickerCtrl(p, message="Stackup JSON", wildcard="JSON (*.json)|*.json",
                                             style=wx.FLP_USE_TEXTCTRL | wx.FLP_OPEN)
        for rb in (self.rs_board, self.rs_jlc, self.rs_custom):
            rb.Bind(wx.EVT_RADIOBUTTON, lambda e: self._update_enable())
        box.Add(self.rs_board, 0, wx.ALL, 2)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(self.rs_jlc, 0, wx.ALIGN_CENTER_VERTICAL)
        row.Add(self.jlc_choice, 1, wx.LEFT, 6)
        box.Add(row, 0, wx.EXPAND | wx.ALL, 2)
        box.Add(self.rs_custom, 0, wx.ALL, 2)
        box.Add(self.custom_file, 0, wx.EXPAND | wx.ALL, 2)
        row = wx.BoxSizer(wx.HORIZONTAL)
        b1 = wx.Button(p, label="Voir / éditer…")
        b1.Bind(wx.EVT_BUTTON, self.on_edit_stackup)
        row.Add(b1, 0, wx.RIGHT, 6)
        box.Add(row, 0, wx.ALL, 2)
        right.Add(box, 0, wx.EXPAND | wx.ALL, 4)

        # --- calcul
        box = wx.StaticBoxSizer(wx.VERTICAL, self, "Calcul")
        p = box.GetStaticBox()
        g = wx.FlexGridSizer(0, 2, 4, 6)
        self.t_step = wx.TextCtrl(p, size=(70, -1))
        self.rw_auto = wx.RadioButton(p, label="Fenêtre auto : max(kw·w, kh·h)", style=wx.RB_GROUP)
        self.rw_fixed = wx.RadioButton(p, label="Fenêtre fixe (mm)")
        self.t_kw = wx.TextCtrl(p, size=(50, -1))
        self.t_kh = wx.TextCtrl(p, size=(50, -1))
        self.t_win = wx.TextCtrl(p, size=(70, -1))
        self.mode = wx.RadioBox(p, label="Mode", choices=["Rapide", "Précis"], majorDimension=2)
        self.t_etch = wx.TextCtrl(p, size=(70, -1))
        self.t_workers = wx.SpinCtrl(p, min=0, max=64, size=(70, -1))
        kwh = wx.BoxSizer(wx.HORIZONTAL)
        kwh.AddMany([(wx.StaticText(p, label="kw"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_kw, 0, wx.LEFT | wx.RIGHT, 4),
                     (wx.StaticText(p, label="kh"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_kh, 0, wx.LEFT, 4)])
        g.AddMany([(wx.StaticText(p, label="Pas d'échantillonnage (mm)"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_step,),
                   (self.rw_auto, 0, wx.ALIGN_CENTER_VERTICAL), (kwh,),
                   (self.rw_fixed, 0, wx.ALIGN_CENTER_VERTICAL), (self.t_win,),
                   (wx.StaticText(p, label="Facteur de gravure (0 = rectangle)"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_etch,),
                   (wx.StaticText(p, label="Processus parallèles (0 = auto)"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_workers,)])
        box.Add(g, 0, wx.ALL, 2)
        box.Add(self.mode, 0, wx.EXPAND | wx.ALL, 2)
        right.Add(box, 0, wx.EXPAND | wx.ALL, 4)

        # --- sorties
        box = wx.StaticBoxSizer(wx.VERTICAL, self, "Sorties")
        p = box.GetStaticBox()
        self.cb_overlay = wx.CheckBox(p, label="Overlay dans l'éditeur PCB")
        self.cb_clear = wx.CheckBox(p, label="Remplacer l'overlay précédent")
        self.cb_report = wx.CheckBox(p, label="Rapport HTML")
        self.cb_open = wx.CheckBox(p, label="Ouvrir le rapport")
        for c in (self.cb_overlay, self.cb_clear, self.cb_report, self.cb_open):
            box.Add(c, 0, wx.ALL, 2)
        g = wx.FlexGridSizer(0, 4, 4, 6)
        self.l_ok = wx.Choice(p, choices=USER_LAYERS)
        self.l_hi = wx.Choice(p, choices=USER_LAYERS)
        self.l_lo = wx.Choice(p, choices=USER_LAYERS)
        self.l_an = wx.Choice(p, choices=USER_LAYERS)
        g.AddMany([(wx.StaticText(p, label="Dans tol."), 0, wx.ALIGN_CENTER_VERTICAL), (self.l_ok,),
                   (wx.StaticText(p, label="Trop haut"), 0, wx.ALIGN_CENTER_VERTICAL), (self.l_hi,),
                   (wx.StaticText(p, label="Trop bas"), 0, wx.ALIGN_CENTER_VERTICAL), (self.l_lo,),
                   (wx.StaticText(p, label="Étiquettes"), 0, wx.ALIGN_CENTER_VERTICAL), (self.l_an,)])
        box.Add(g, 0, wx.ALL, 2)
        right.Add(box, 0, wx.EXPAND | wx.ALL, 4)

        # --- intégrité du signal
        from ..analysis.signal import PRESETS
        box = wx.StaticBoxSizer(wx.VERTICAL, self, "Intégrité du signal (rapport)")
        p = box.GetStaticBox()
        self.cb_sig = wx.CheckBox(p, label="Calculer |S11|, perte de désadaptation et TDR")
        self.cb_sig.Bind(wx.EVT_CHECKBOX, lambda e: self._update_enable())
        box.Add(self.cb_sig, 0, wx.ALL, 2)
        self.sig_preset = wx.Choice(p, choices=list(PRESETS))
        self.sig_preset.Bind(wx.EVT_CHOICE, self.on_sig_preset)
        box.Add(self.sig_preset, 0, wx.EXPAND | wx.ALL, 2)
        g = wx.FlexGridSizer(0, 4, 4, 6)
        self.t_rate = wx.TextCtrl(p, size=(60, -1))
        self.t_rise = wx.TextCtrl(p, size=(60, -1))
        self.t_freq = wx.TextCtrl(p, size=(60, -1))
        self.t_zref = wx.TextCtrl(p, size=(60, -1))
        g.AddMany([(wx.StaticText(p, label="Débit (Mb/s)"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_rate,),
                   (wx.StaticText(p, label="Montée (ps)"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_rise,),
                   (wx.StaticText(p, label="Fréq. RF (MHz)"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_freq,),
                   (wx.StaticText(p, label="Zref (Ω, 0 = cible)"), 0, wx.ALIGN_CENTER_VERTICAL), (self.t_zref,)])
        box.Add(g, 0, wx.ALL, 2)
        right.Add(box, 0, wx.EXPAND | wx.ALL, 4)

        cols.Add(left, 1, wx.EXPAND)
        cols.Add(right, 1, wx.EXPAND)
        root.Add(cols, 1, wx.EXPAND | wx.ALL, 4)

        info = "Mode direct : " + self.ctl.board.name if self.ctl.live else f"Mode fichier : {self.ctl.pcb_path}"
        self.status = wx.StaticText(self, label=info + f" — {len(self.ctl.bm.copper_names)} couches cuivre")
        root.Add(self.status, 0, wx.LEFT | wx.RIGHT, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.b_run = wx.Button(self, label="Analyser")
        self.b_clear = wx.Button(self, label="Effacer l'overlay…")
        self.b_synth = wx.Button(self, label="Calcul inverse…")
        self.b_close = wx.Button(self, wx.ID_CLOSE, label="Fermer")
        self.b_run.SetDefault()
        self.b_run.Bind(wx.EVT_BUTTON, self.on_run)
        self.b_clear.Bind(wx.EVT_BUTTON, self.on_clear)
        self.b_synth.Bind(wx.EVT_BUTTON, self.on_synth)
        self.b_close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        btns.Add(self.b_run, 0, wx.RIGHT, 6)
        btns.Add(self.b_clear, 0, wx.RIGHT, 6)
        btns.Add(self.b_synth, 0, wx.RIGHT, 6)
        btns.AddStretchSpacer()
        btns.Add(self.b_close, 0)
        root.Add(btns, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(root)
        if not self.ctl.live:
            self.b_clear.Disable()

    # ================================================================== réglages <-> widgets
    def _fill_nets(self):
        flt = self.filter.GetValue().lower()
        checked = set(self._checked_nets())
        nets = [n for n in self.ctl.routed_nets() if flt in n.lower()]
        self.net_list.Set(nets)
        for i, n in enumerate(nets):
            if n in checked or n in self.s.nets:
                self.net_list.Check(i)

    def _checked_nets(self):
        return [self.net_list.GetString(i) for i in self.net_list.GetCheckedItems()] if self.net_list.GetCount() else []

    def _load_settings(self):
        s = self.s
        {"selection": self.rb_sel, "nets": self.rb_nets, "netclass": self.rb_cls, "pairs": self.rb_pairs}.get(
            s.target_mode, self.rb_sel).SetValue(True)
        if not self.ctl.live and s.target_mode == "selection":
            self.rb_nets.SetValue(True)
        self._fill_nets()
        if s.netclass in self.ctl.netclasses():
            self.cls_choice.SetStringSelection(s.netclass)
        elif self.cls_choice.GetCount():
            self.cls_choice.SetSelection(0)
        self.cb_pairs.SetValue(s.detect_pairs)
        self.t_zs.SetValue(f"{s.z_single:g}")
        self.t_zd.SetValue(f"{s.z_diff:g}")
        self.t_tol.SetValue(f"{s.tol_pct:g}")
        self.cb_zcls.SetValue(s.z_from_netclass)
        {"board": self.rs_board, "jlcpcb": self.rs_jlc, "custom": self.rs_custom}[s.stackup_mode].SetValue(True)
        choices = self.ctl.jlc_choices()
        if s.jlc_preset in choices:
            self.jlc_choice.SetStringSelection(s.jlc_preset)
        elif choices:
            self.jlc_choice.SetStringSelection(next((c for c in choices if "7628" in c), choices[0]))
        self.custom_file.SetPath(s.custom_path)
        self.t_step.SetValue(f"{s.step_mm:g}")
        (self.rw_fixed if s.window_mode == "fixed" else self.rw_auto).SetValue(True)
        self.t_kw.SetValue(f"{s.window_w_factor:g}")
        self.t_kh.SetValue(f"{s.window_h_factor:g}")
        self.t_win.SetValue(f"{s.window_mm:g}")
        self.mode.SetSelection(1 if s.mode == "precise" else 0)
        self.t_etch.SetValue(f"{s.etch_factor:g}")
        self.t_workers.SetValue(s.workers)
        self.cb_overlay.SetValue(s.do_overlay and self.ctl.live)
        self.cb_clear.SetValue(s.clear_previous)
        self.cb_report.SetValue(s.do_report)
        self.cb_open.SetValue(s.open_report)
        for ch, v in ((self.l_ok, s.layer_ok), (self.l_hi, s.layer_high), (self.l_lo, s.layer_low),
                      (self.l_an, s.layer_annot)):
            ch.SetStringSelection(v)
        self.cb_sig.SetValue(s.sig_enabled)
        if s.sig_preset in [self.sig_preset.GetString(i) for i in range(self.sig_preset.GetCount())]:
            self.sig_preset.SetStringSelection(s.sig_preset)
        else:
            self.sig_preset.SetSelection(0)
        self.t_rate.SetValue(f"{s.sig_bitrate_mbps:g}")
        self.t_rise.SetValue(f"{s.sig_rise_ps:g}")
        self.t_freq.SetValue(f"{s.sig_freq_mhz:g}")
        self.t_zref.SetValue(f"{s.sig_zref:g}")
        self._update_enable()

    def on_sig_preset(self, _evt):
        from ..analysis.signal import PRESETS
        sp = PRESETS[self.sig_preset.GetStringSelection()]
        if sp.kind == "digital":
            self.t_rate.SetValue(f"{sp.bitrate / 1e6:g}")
            self.t_rise.SetValue(f"{sp.rise * 1e12:.0f}")
        self._update_enable()

    def _read_settings(self) -> UiSettings:
        s = self.s
        s.target_mode = ("selection" if self.rb_sel.GetValue() else "nets" if self.rb_nets.GetValue()
                         else "netclass" if self.rb_cls.GetValue() else "pairs")
        s.nets = self._checked_nets()
        s.netclass = self.cls_choice.GetStringSelection()
        s.detect_pairs = self.cb_pairs.GetValue()
        s.z_single = _num(self.t_zs, 50.0)
        s.z_diff = _num(self.t_zd, 100.0)
        s.tol_pct = _num(self.t_tol, 10.0)
        s.z_from_netclass = self.cb_zcls.GetValue()
        s.stackup_mode = "board" if self.rs_board.GetValue() else "jlcpcb" if self.rs_jlc.GetValue() else "custom"
        s.jlc_preset = self.jlc_choice.GetStringSelection()
        s.custom_path = self.custom_file.GetPath()
        s.step_mm = _num(self.t_step, 0.5)
        s.window_mode = "fixed" if self.rw_fixed.GetValue() else "auto"
        s.window_w_factor = _num(self.t_kw, 10.0)
        s.window_h_factor = _num(self.t_kh, 6.0)
        s.window_mm = _num(self.t_win, 3.0)
        s.mode = "precise" if self.mode.GetSelection() == 1 else "fast"
        s.etch_factor = _num(self.t_etch, 0.0)
        s.workers = self.t_workers.GetValue()
        s.do_overlay = self.cb_overlay.GetValue()
        s.clear_previous = self.cb_clear.GetValue()
        s.do_report = self.cb_report.GetValue()
        s.open_report = self.cb_open.GetValue()
        s.layer_ok = self.l_ok.GetStringSelection()
        s.layer_high = self.l_hi.GetStringSelection()
        s.layer_low = self.l_lo.GetStringSelection()
        s.layer_annot = self.l_an.GetStringSelection()
        s.sig_enabled = self.cb_sig.GetValue()
        s.sig_preset = self.sig_preset.GetStringSelection()
        s.sig_bitrate_mbps = _num(self.t_rate, 480.0)
        s.sig_rise_ps = _num(self.t_rise, 500.0)
        s.sig_freq_mhz = _num(self.t_freq, 1000.0)
        s.sig_zref = _num(self.t_zref, 0.0)
        return s

    def _update_enable(self):
        self.rb_sel.Enable(self.ctl.live)
        nets = self.rb_nets.GetValue()
        self.filter.Enable(nets)
        self.net_list.Enable(nets)
        self.cls_choice.Enable(self.rb_cls.GetValue())
        self.jlc_choice.Enable(self.rs_jlc.GetValue())
        self.custom_file.Enable(self.rs_custom.GetValue())
        self.cb_overlay.Enable(self.ctl.live)
        self.cb_clear.Enable(self.ctl.live)
        if hasattr(self, "sig_preset"):
            rf = "RF" in self.sig_preset.GetStringSelection()
            on = self.cb_sig.GetValue()
            self.sig_preset.Enable(on)
            self.t_rate.Enable(on and not rf)
            self.t_rise.Enable(on and not rf)
            self.t_freq.Enable(on and rf)
            self.t_zref.Enable(on)

    # ================================================================== actions
    def _error(self, msg: str):
        wx.MessageBox(msg, "Impedance Map", wx.OK | wx.ICON_ERROR, self)

    def on_run(self, _evt):
        s = self._read_settings()
        s.save()
        uz = self.ctl.bm.unfilled_zones()
        if uz:
            names = ", ".join(sorted({z.name or z.net for z in uz}))
            if wx.MessageBox(f"{len(uz)} zone(s) ne sont pas remplies ({names}).\n\n"
                             "Le calcul n'utilise que les remplissages réels : ces zones seront ignorées. "
                             "Remplissez les zones (touche B) puis relancez pour un résultat correct.\n\n"
                             "Continuer quand même ?", "Zones non remplies",
                             wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self) != wx.YES:
                return
        cancel = threading.Event()
        dlg = wx.ProgressDialog("Impedance Map", "Préparation…", maximum=1000, parent=self,
                                style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME | wx.PD_AUTO_HIDE)
        state = {"frac": 0.0, "msg": "Préparation…", "done": False, "run": None, "err": None}

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

        th = threading.Thread(target=work, daemon=True)
        th.start()
        while not state["done"]:
            cont, _ = dlg.Update(min(999, int(state["frac"] * 1000)), state["msg"])
            if not cont and not cancel.is_set():
                cancel.set()
                dlg.Update(999, "Annulation…")
            wx.MilliSleep(100)
            wx.YieldIfNeeded()
        th.join()
        dlg.Destroy()
        if state["err"] == "cancel":
            self.status.SetLabel("Analyse annulée.")
            return
        if state["err"]:
            self._error("Échec de l'analyse :\n" + state["err"][:3000])
            return
        run = state["run"]
        msgs = [f"Analyse terminée en {run.timing['total_s']:.1f} s ({run.timing['n_unique']} coupes uniques)."]
        try:
            if s.do_overlay and self.ctl.live:
                if s.clear_previous:
                    self.ctl.clear_overlay(lambda n, g: True)
                r = self.ctl.apply_overlay(run, s)
                msgs.append(f"Overlay : {r['n_items']} objets dans le groupe « {r['group'] or 'sans groupe'} ».")
                msgs.extend(r.get("notes", []))
            if s.do_report:
                p = self.ctl.write_report(run, s.open_report)
                msgs.append(f"Rapport : {p}")
        except Exception as e:  # noqa: BLE001
            self._error(f"Erreur lors de la création des sorties :\n{e}\n\n{traceback.format_exc()[:2000]}")
        lines = []
        for tr in run.targets:
            st = tr.stats
            if "z_mean" in st:
                lines.append(f"• {tr.label} : {st['z_min']:.1f} / {st['z_mean']:.1f} / {st['z_max']:.1f} Ω "
                             f"(min/moy/max), {st['pct_out']:.0f} % hors tolérance")
            else:
                lines.append(f"• {tr.label} : aucune portion calculable")
        warn = run.warnings + [w for tr in run.targets for w in tr.warnings]
        text = "\n".join(msgs) + "\n\n" + "\n".join(lines[:25])
        if warn:
            text += "\n\nAvertissements :\n" + "\n".join("– " + w for w in warn[:10])
        self.status.SetLabel(msgs[0])
        wx.MessageBox(text, "Impedance Map — résultat", wx.OK | wx.ICON_INFORMATION, self)

    def on_clear(self, _evt):
        n, g = self.ctl.overlay_count()
        if n == 0 and g == 0:
            wx.MessageBox("Aucun overlay Impedance Map dans ce board.", "Impedance Map", wx.OK, self)
            return

        def confirm(n_items, n_groups):
            return wx.MessageBox(f"Supprimer {n_items} objet(s) de {n_groups} groupe(s) « ImpedanceMap » ?\n"
                                 "Seuls les objets créés par ce plugin sont concernés (annulable par Ctrl+Z).",
                                 "Effacer l'overlay", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) == wx.YES

        try:
            k = self.ctl.clear_overlay(confirm)
            self.status.SetLabel(f"{k} objet(s) d'overlay supprimé(s).")
        except Exception as e:  # noqa: BLE001
            self._error(str(e))

    def _current_stackup(self) -> Stackup:
        return self.ctl.stackup_for(self._read_settings())

    def on_edit_stackup(self, _evt):
        try:
            st = self._current_stackup()
        except Exception as e:  # noqa: BLE001
            self._error(str(e))
            return
        dlg = StackupEditor(self, st)
        if dlg.ShowModal() == wx.ID_OK and dlg.saved_path:
            self.rs_custom.SetValue(True)
            self.custom_file.SetPath(dlg.saved_path)
            self._update_enable()
        dlg.Destroy()

    def on_synth(self, _evt):
        try:
            st = self._current_stackup()
        except Exception as e:  # noqa: BLE001
            self._error(str(e))
            return
        dlg = SynthesisDialog(self, st, self._read_settings())
        dlg.ShowModal()
        dlg.Destroy()


class StackupEditor(wx.Dialog):
    COLS = ["Nom", "Type", "Épaisseur (mm)", "εr", "tan δ", "Matériau", "core/prepreg", "Masque c2 (mm)"]

    def __init__(self, parent, st: Stackup):
        super().__init__(parent, title=f"Stackup — {st.name}", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.st = st
        self.saved_path = None
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=f"Source : {st.source}. Modifiez les valeurs puis « Enregistrer sous… » "
                                        "pour créer un stackup perso."), 0, wx.ALL, 6)
        self.grid = wx.grid.Grid(self)
        self.grid.CreateGrid(len(st.layers), len(self.COLS))
        for j, c in enumerate(self.COLS):
            self.grid.SetColLabelValue(j, c)
        for i, l in enumerate(st.layers):
            vals = [l.name, l.kind, f"{l.thickness * 1e3:.5g}", f"{l.er:g}" if l.kind != "copper" else "",
                    f"{l.loss_tangent:g}" if l.kind != "copper" else "", l.material, l.dielectric_type,
                    f"{l.mask_c2 * 1e3:.5g}" if l.mask_c2 is not None else ""]
            for j, v in enumerate(vals):
                self.grid.SetCellValue(i, j, v)
            self.grid.SetReadOnly(i, 1)
        self.grid.AutoSizeColumns()
        s.Add(self.grid, 1, wx.EXPAND | wx.ALL, 6)
        if st.notes:
            s.Add(wx.StaticText(self, label=st.notes[:300]), 0, wx.ALL, 6)
        b = wx.BoxSizer(wx.HORIZONTAL)
        save = wx.Button(self, label="Enregistrer sous…")
        save.Bind(wx.EVT_BUTTON, self.on_save)
        b.Add(save, 0, wx.RIGHT, 6)
        b.AddStretchSpacer()
        b.Add(wx.Button(self, wx.ID_CANCEL, label="Fermer"))
        s.Add(b, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizerAndFit(s)
        self.SetSize((820, 460))

    def _collect(self) -> Stackup:
        d = {"name": "Stackup perso", "source": "custom", "units": "mm", "layers": []}
        for i, l in enumerate(self.st.layers):
            def cell(j):
                return self.grid.GetCellValue(i, j).strip().replace(",", ".")
            ld = {"kind": l.kind, "name": cell(0) or l.name, "thickness": float(cell(2))}
            if l.kind != "copper":
                ld["er"] = float(cell(3) or 1)
                ld["loss_tangent"] = float(cell(4) or 0)
            if cell(5):
                ld["material"] = self.grid.GetCellValue(i, 5)
            if cell(6):
                ld["dielectric_type"] = cell(6)
            if l.kind == "mask" and cell(7):
                ld["mask_c2"] = float(cell(7))
            d["layers"].append(ld)
        return Stackup.from_dict(d)

    def on_save(self, _evt):
        try:
            st = self._collect()
        except Exception as e:  # noqa: BLE001
            wx.MessageBox(f"Stackup invalide : {e}", "Stackup", wx.OK | wx.ICON_ERROR, self)
            return
        with wx.FileDialog(self, "Enregistrer le stackup", wildcard="JSON (*.json)|*.json",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as fd:
            if fd.ShowModal() != wx.ID_OK:
                return
            path = fd.GetPath()
        st.to_json(path)
        self.saved_path = path
        self.EndModal(wx.ID_OK)


class SynthesisDialog(wx.Dialog):
    """Calcul inverse : largeur (et gap) pour une impédance cible sur une couche."""

    def __init__(self, parent, st: Stackup, s: UiSettings):
        super().__init__(parent, title="Calcul inverse (largeur / gap)")
        self.st = st
        v = wx.BoxSizer(wx.VERTICAL)
        g = wx.FlexGridSizer(0, 2, 5, 8)
        self.layer = wx.Choice(self, choices=st.copper_names)
        self.layer.SetSelection(0)
        self.kind = wx.RadioBox(self, choices=["Simple", "Paire (gap fixé)", "Paire (largeur fixée)"], majorDimension=1)
        self.z = wx.TextCtrl(self, value=f"{s.z_single:g}")
        self.fixed = wx.TextCtrl(self, value="0.2")
        self.kind.Bind(wx.EVT_RADIOBOX, lambda e: self.z.SetValue(f"{s.z_single if self.kind.GetSelection() == 0 else s.z_diff:g}"))
        g.AddMany([(wx.StaticText(self, label="Couche"),), (self.layer,),
                   (wx.StaticText(self, label="Z cible (Ω)"),), (self.z,),
                   (wx.StaticText(self, label="Gap ou largeur fixé (mm)"),), (self.fixed,)])
        v.Add(wx.StaticText(self, label=f"Stackup : {st.name}. Références : couches cuivre adjacentes "
                                        "(plans pleins)."), 0, wx.ALL, 8)
        v.Add(self.kind, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        v.Add(g, 0, wx.ALL, 8)
        self.out = wx.StaticText(self, label=" ")
        v.Add(self.out, 0, wx.ALL, 8)
        b = wx.BoxSizer(wx.HORIZONTAL)
        go = wx.Button(self, label="Calculer")
        go.Bind(wx.EVT_BUTTON, self.on_go)
        b.Add(go, 0, wx.RIGHT, 6)
        b.Add(wx.Button(self, wx.ID_CANCEL, label="Fermer"))
        v.Add(b, 0, wx.ALL, 8)
        self.SetSizerAndFit(v)

    def on_go(self, _evt):
        from ..synthesis import impedance, solve_gap, solve_width
        layer = self.layer.GetStringSelection()
        try:
            z = _num(self.z, 50.0)
            fixed = _num(self.fixed, 0.2) * 1e-3
            wx.BeginBusyCursor()
            k = self.kind.GetSelection()
            if k == 0:
                w = solve_width(self.st, layer, z)
                r = impedance(self.st, layer, w)
                txt = f"w = {w * 1e3:.4f} mm → Z0 = {r.z0:.2f} Ω, εeff = {r.eps_eff:.3f}"
            elif k == 1:
                w = solve_width(self.st, layer, z, s=fixed)
                r = impedance(self.st, layer, w, fixed)
                txt = f"w = {w * 1e3:.4f} mm (gap {fixed * 1e3:.3f}) → Zdiff = {r.zdiff:.2f} Ω, Zodd {r.zodd:.1f}, Zeven {r.zeven:.1f}"
            else:
                gap = solve_gap(self.st, layer, z, fixed)
                r = impedance(self.st, layer, fixed, gap)
                txt = f"gap = {gap * 1e3:.4f} mm (w {fixed * 1e3:.3f}) → Zdiff = {r.zdiff:.2f} Ω"
        except Exception as e:  # noqa: BLE001
            txt = f"Erreur : {e}"
        finally:
            if wx.IsBusy():
                wx.EndBusyCursor()
        self.out.SetLabel(txt)
        self.Fit()


def run_wx(ctl: Controller, settings: UiSettings):
    app = wx.App.Get() or wx.App(False)
    dlg = ImpedanceDialog(ctl, settings)
    dlg.ShowModal()
    dlg.Destroy()
    return app
