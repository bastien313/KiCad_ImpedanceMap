"""Rapport HTML autonome (figures matplotlib intégrées en data URI PNG).

Contenu :
  1. synthèse (board, stackup, options, temps, cache, avertissements) ;
  2. tableau min / max / moyenne / % hors tolérance par cible ;
  3. carte interactive du board (SVG + JS intégrés, viz/board_map.py) : échelle en Ω centrée sur
     la cible, en Ω libre (min/max) ou en écart %, infobulles, zoom ;
  4. courbe Z(s) par net / paire avec la bande de tolérance et les discontinuités ;
  5. coupe transverse au pire point (géométrie + équipotentielles) ;
  6. intégrité du signal (si calculée, analysis/signal.py) : |S11|, perte de désadaptation,
     TDR simulée comparée au profil Z(s) ;
  7. hypothèses physiques et limites.

Couleurs : palette divergente bleu (trop bas) ↔ gris (dans la tolérance) ↔ rouge (trop haut) ;
les discontinuités et pertes de référence ont aussi une forme de marqueur (pas la couleur seule).
"""

from __future__ import annotations

import base64
import datetime
import html
import io
import math
from typing import Optional

import numpy as np

from .. import __version__
from ..analysis.engine import AnalysisRun, Engine, SampleResult, TargetReport
from ..analysis.signal import QUALITY_BAD, QUALITY_BANDS, rl_to_ml
from ..solver.geometry import FLOATING, GROUND, SIGNAL
from .board_map import board_svg

BLUE, RED, MID = "#2a78d6", "#e34948", "#9c9b95"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
CONTEXT = "#dcdbd6"
WARN = "#eda100"


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK2,
                         "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID,
                         "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
                         "figure.dpi": 110})
    cmap = LinearSegmentedColormap.from_list("zdev", [BLUE, "#b7d3f6", "#c9c8c2", "#f4b6b2", RED])
    return plt, cmap


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _esc(s) -> str:
    return html.escape(str(s))


# ------------------------------------------------------------------ figures
def fig_zs(tr: TargetReport) -> Optional[str]:
    plt, _ = _mpl()
    t = tr.target
    zt, tol = t["z_target"], t["tol"]
    by = {}
    for s in tr.samples:
        by.setdefault((s.net, s.path_id), []).append(s)
    if not by:
        return None
    fig, ax = plt.subplots(figsize=(9.5, 3.0))
    offset = 0.0
    xs_all = []
    for (net, pid), smp in sorted(by.items(), key=lambda kv: kv[0][1]):
        smp.sort(key=lambda s: s.s)
        s_mm = np.array([s.s for s in smp]) * 1e3 + offset
        z = np.array([s.z if s.valid else np.nan for s in smp])
        ax.plot(s_mm, z, color=INK, linewidth=1.6, zorder=3)
        ok = ~np.isnan(z)
        if ok.any():
            ax.scatter(s_mm[ok], z[ok], s=6, color=INK, zorder=3)
        for i, s in enumerate(smp):
            if s.status in ("discontinuity", "error"):
                ax.axvspan(s_mm[i] - 0.15, s_mm[i] + 0.15, color=GRID, zorder=1, linewidth=0)
            elif s.status == "no_reference":
                ax.scatter([s_mm[i]], [zt * (1 + 1.6 * tol)], marker="^", s=36, color=WARN, edgecolors=INK,
                           linewidths=0.6, zorder=5)
        xs_all.extend(s_mm.tolist())
        offset = s_mm[-1] + 2.0 if len(s_mm) else offset
        if len(by) > 1:
            ax.axvline(offset - 1.0, color=INK2, linewidth=0.6, linestyle=":")
    ax.axhspan(zt * (1 - tol), zt * (1 + tol), color="#cde2fb", alpha=0.55, zorder=0, linewidth=0)
    ax.axhline(zt, color=BLUE, linewidth=1.0, linestyle="--", zorder=2)
    valid = [s.z for s in tr.samples if s.valid]
    lo = min([zt * (1 - 2 * tol)] + valid)
    hi = max([zt * (1 + 2 * tol)] + valid)
    ax.set_ylim(lo - 0.05 * (hi - lo), hi + 0.05 * (hi - lo))
    ax.set_xlabel("abscisse le long du tracé s (mm)" + (" — chemins mis bout à bout" if len(by) > 1 else ""))
    ax.set_ylabel("Zdiff (Ω)" if t["kind"] == "pair" else "Z0 (Ω)")
    ax.set_title(f"{tr.label} — cible {zt:g} Ω ± {tol * 100:g} % (bande bleue) ; zones grises = discontinuités ; "
                 f"▲ = perte de référence", color=INK, loc="left", fontsize=8.5)
    return _png(fig)


def _fmt_f(f):
    return f"{f / 1e9:g} GHz" if f >= 1e9 else f"{f / 1e6:g} MHz"


def _quality_spans(ax, as_loss: bool, lo: float, hi: float):
    """Zones excellent / bon / acceptable / mauvais sur un axe |S11| (dB, négatif) ou perte (dB)."""
    lims = [b[0] for b in QUALITY_BANDS]
    names = [b[1] for b in QUALITY_BANDS] + [QUALITY_BAD[0]]
    cols = [b[2] for b in QUALITY_BANDS] + [QUALITY_BAD[1]]
    if as_loss:
        edges = [lo] + [rl_to_ml(x) for x in lims] + [hi]           # perte croissante
    else:
        edges = [lo] + [-x for x in lims] + [hi]                     # |S11| de −∞ vers 0
    for a, b, n, c in zip(edges[:-1], edges[1:], names, cols):
        a, b = max(a, lo), min(b, hi)
        if b <= a:
            continue
        ax.axhspan(a, b, color=c, zorder=0, linewidth=0)
        ym = math.sqrt(a * b) if as_loss else (a + b) / 2
        ax.annotate(n, (1, ym), xycoords=("axes fraction", "data"), xytext=(-3, 0), textcoords="offset points",
                    ha="right", va="center", fontsize=6.5, color=INK2)


def fig_signal_freq(sig: dict) -> Optional[str]:
    """Deux graphes côte à côte (une unité par axe) : |S11| (dB) et perte de désadaptation (dB),
    avec les zones de qualité usuelles (return loss 20 / 15 / 10 dB)."""
    plt, _ = _mpl()
    f = np.array(sig["curve_f_hz"])
    scale, unit = (1e9, "GHz") if f.max() >= 2e9 else (1e6, "MHz")
    fk = sig["f_key"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.5, 3.0))
    s11 = np.array(sig["curve_s11_db"])
    lo1 = min(-40.0, float(np.nanmin(s11[1:])) - 3 if len(s11) > 1 else -40.0)
    _quality_spans(a1, False, lo1, 0.0)
    a1.plot(f / scale, s11, color=BLUE, linewidth=1.8, zorder=3)
    a1.set_ylim(lo1, 0)
    a1.set_ylabel("|S11| (dB)")
    a1.set_title("Réflexion |S11| (plus bas = mieux)", loc="left", fontsize=8.5, color=INK)
    ml = np.array(sig["curve_ml_db"])
    hi2 = max(1.0, float(ml.max()) * 1.5)
    # bas d'échelle : sous la fréquence clé / 20 la perte tend vers 0 (−∞ en log) ; on ne l'écrête pas
    # à une valeur fixe (faux plateau), on la laisse sortir par le bas du graphe
    band = ml[(f >= fk / 20) & (ml > 0)]
    lo2 = min(1e-3, max(float(band.min()) if band.size else 1e-3, 1e-9) / 2)
    ml = np.maximum(ml, lo2 / 10)
    _quality_spans(a2, True, lo2, hi2)
    a2.plot(f / scale, ml, color=RED, linewidth=1.8, zorder=3)
    a2.set_yscale("log")
    a2.set_ylim(lo2, hi2)
    a2.set_ylabel("perte (dB, échelle log)")
    a2.set_title("Perte de désadaptation −10·log(1−|S11|²)", loc="left", fontsize=8.5, color=INK)
    lab = "Nyquist" if sig["spec"]["kind"] == "digital" else "f"
    for ax in (a1, a2):
        ax.axvline(fk / scale, color=INK2, linewidth=0.8, linestyle="--", zorder=2)
        ax.annotate(f"{lab} {_fmt_f(fk)}", (fk / scale, 1), xycoords=("data", "axes fraction"), fontsize=7,
                    color=INK2, xytext=(3, -10), textcoords="offset points")
        ax.set_xlabel(f"fréquence ({unit})")
        ax.set_xlim(0, f.max() / scale)
    fig.tight_layout()
    return _png(fig)


SECTION_COLORS = {"ok": "#9c9b95", "high": RED, "low": BLUE, "pad": "#7a5bbf", "nan": "#c9c8c2",
                  "via": "#1b8a5a", "corner": "#b86e00", "slot": WARN, "stub": "#5b3f8f"}


def fig_sections(sig: dict) -> Optional[str]:
    """Contribution de chaque tronçon : perte « seul » à f clé le long du tracé (barres) et en
    fréquence pour les principaux (courbes), avec la perte totale et les zones de qualité."""
    secs = sig.get("sections") or []
    if not secs:
        return None
    plt, _ = _mpl()
    fk = sig["f_key"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.5, 3.2), gridspec_kw={"width_ratios": [1.1, 1]})
    vals = [max(s["ml_alone_db"], 1e-6) for s in secs]
    hi = max(1.0, max(vals) * 2, sig["mismatch_loss_db_at_fkey"] * 2)
    lo = min(1e-4, min(vals) / 2)
    _quality_spans(a1, True, lo, hi)
    L = max(sig["length_mm"], 1e-3)
    for s in secs:
        c = SECTION_COLORS.get(s["kind"], MID)
        w = s["length_mm"] if s["length_mm"] > 0 else 0.012 * L
        a1.bar(s["s0_mm"] if s["length_mm"] > 0 else s["s0_mm"] - w / 2, max(s["ml_alone_db"], 1e-6) - lo,
               bottom=lo, width=w, align="edge", color=c, edgecolor=INK, linewidth=0.5, zorder=3)
    for s in secs[:3]:
        if s["ml_alone_db"] <= lo:
            continue
        x = (s["s0_mm"] + s["s1_mm"]) / 2
        txt = f"{s['ml_alone_db']:.3g} dB" + (f"\nZ {s['z_mean']:.0f} Ω" if math.isfinite(s["z_mean"]) else "")
        a1.annotate(txt, (x, s["ml_alone_db"]), xytext=(0, 3), textcoords="offset points", ha="center",
                    va="bottom", fontsize=6.5, color=INK, zorder=4)
    a1.axhline(max(sig["mismatch_loss_db_at_fkey"], lo), color=INK, linewidth=1.0, linestyle="--", zorder=4)
    a1.annotate(f"ligne complète {sig['mismatch_loss_db_at_fkey']:.3g} dB",
                (0, max(sig["mismatch_loss_db_at_fkey"], lo)), xycoords=("axes fraction", "data"),
                xytext=(3, 3), textcoords="offset points", fontsize=6.5, color=INK)
    a1.set_yscale("log")
    a1.set_ylim(lo, hi)
    a1.set_xlim(0, L)
    a1.set_xlabel("abscisse le long du tracé (mm)")
    a1.set_ylabel("perte si seul (dB, log)")
    a1.set_title(f"Perte due à chaque tronçon seul à {_fmt_f(fk)}", loc="left", fontsize=8.5, color=INK)
    f = np.array(sig["curve_f_hz"])
    scale, unit = (1e9, "GHz") if f.max() >= 2e9 else (1e6, "MHz")
    tot = np.maximum(np.array(sig["curve_ml_db"]), 1e-6)
    curves = [c for c in (sig.get("section_curves") or []) if max(c["ml"]) > 1e-5]     # contributions visibles
    allv = [tot[1:]] + [np.maximum(np.array(c["ml"])[1:], 1e-6) for c in curves]
    hi2 = max(1.0, max(float(v.max()) for v in allv) * 1.5)
    lo2 = min(1e-4, min(float(v.min()) for v in allv) / 2)
    _quality_spans(a2, True, lo2, hi2)
    a2.plot(f / scale, tot, color=INK, linewidth=2.2, label="ligne complète", zorder=3)
    for c in curves:
        a2.plot(f / scale, np.maximum(np.array(c["ml"]), 1e-6), linewidth=1.3, zorder=4,
                color=SECTION_COLORS.get(c["kind"], MID), label=c["label"],
                linestyle="-" if c["kind"] in ("high", "low") else "--")
    a2.axvline(fk / scale, color=INK2, linewidth=0.8, linestyle="--", zorder=2)
    a2.set_yscale("log")
    a2.set_ylim(lo2, hi2)
    a2.set_xlim(0, f.max() / scale)
    a2.set_xlabel(f"fréquence ({unit})")
    a2.set_ylabel("perte (dB, log)")
    a2.set_title("Perte en fréquence : ligne complète et tronçons seuls", loc="left", fontsize=8.5, color=INK)
    a2.legend(loc="lower right", frameon=True, fontsize=6, framealpha=0.85)
    fig.tight_layout()
    return _png(fig)


def _sections_html(sig: dict, n: int = 8) -> str:
    secs = sig.get("sections") or []
    if not secs:
        return ""
    rows = "".join(
        f"<tr><td style='text-align:left'>{_esc(s['name'])}</td><td>{s['ml_alone_db']:.4f}</td>"
        f"<td>{s['rl_alone_db']:.1f}</td><td>{s['gain_if_fixed_db']:+.4f}</td><td>{s['rl_if_fixed_db']:.1f}</td></tr>"
        for s in secs[:n])
    more = f"<p class='sub'>… {len(secs) - n} autres tronçons de contribution plus faible.</p>" if len(secs) > n else ""
    return ("<p><b>Contribution de chaque tronçon</b> à " + _fmt_f(sig["f_key"]) + " (ligne complète : return loss "
            f"{sig['return_loss_db_at_fkey']:.1f} dB, perte {sig['mismatch_loss_db_at_fkey']:.4f} dB, "
            f"<b>{_esc(sig.get('quality_at_fkey', ''))}</b>). « Seul » : le reste de la ligne à Zref ; « si corrigé » : "
            "ce tronçon ramené à Zref (élément supprimé). Les réflexions interfèrent : les valeurs ne s'additionnent "
            "pas, et un gain négatif signifie que ce tronçon compense en partie un autre défaut.</p>"
            "<table><thead><tr><th style='text-align:left'>Tronçon</th><th>Perte seul (dB)</th>"
            "<th>Return loss seul (dB)</th><th>Gain si corrigé (dB)</th><th>Return loss si corrigé (dB)</th></tr>"
            "</thead><tbody>" + rows + "</tbody></table>" + more)


def fig_tdr(sig: dict, tr: TargetReport) -> Optional[str]:
    if "tdr_x_mm" not in sig:
        return None
    plt, _ = _mpl()
    zt, tol = tr.target["z_target"], tr.target["tol"]
    fig, ax = plt.subplots(figsize=(9.5, 2.8))
    ax.axhspan(zt * (1 - tol), zt * (1 + tol), color="#cde2fb", alpha=0.55, linewidth=0, zorder=0)
    ax.plot(sig["profile_s_mm"], sig["profile_z"], color=INK2, linewidth=1.0, linestyle=":",
            label="Z(s) calculée en 2D (coupes)")
    if "tdr_line_only_z" in sig:
        ax.plot(sig["tdr_line_only_x_mm"], sig["tdr_line_only_z"], color=BLUE, linewidth=1.2, alpha=0.7,
                label="TDR de la ligne seule (sans pads, vias, stubs, coins, fentes)")
    ax.plot(sig["tdr_x_mm"], sig["tdr_z"], color=RED, linewidth=1.8,
            label=f"TDR avec discontinuités modélisées (front {sig['spec']['rise'] * 1e12:.0f} ps)")
    for el in sig.get("elements", []) + sig.get("pads", []):
        ax.axvline(el["s_mm"], color=INK2, linewidth=0.6, linestyle=":", zorder=1)
        ax.annotate(TYPE_FR.get(el["type"], el["type"]), (el["s_mm"], 1), xycoords=("data", "axes fraction"),
                    fontsize=6.5, color=INK2, rotation=90, va="top", ha="right", xytext=(-2, -2),
                    textcoords="offset points")
    ax.axhline(sig["zref"], color=BLUE, linewidth=1.0, linestyle="--", label=f"Zref {sig['zref']:g} Ω")
    ax.set_xlabel("distance depuis le début du tracé (mm)")
    ax.set_ylabel("Zdiff (Ω)" if tr.target["kind"] == "pair" else "Z (Ω)")
    ax.set_xlim(0, sig["length_mm"] * 1.05)
    ax.legend(loc="best", frameon=False, fontsize=7.5)
    title = "TDR simulée (extrémités adaptées sur Zref ; boîtier du connecteur et composants non modélisés)"
    rt, rise = sig.get("tdr_round_trip_ps"), sig["spec"]["rise"] * 1e12
    if rt is not None and rt < rise:
        title += (f"\nFront de {rise:.0f} ps plus long que l'aller-retour du tracé ({rt:.0f} ps) : la TDR ne localise "
                  "pas les défauts, elle montre leur effet global (Z vue minimale / maximale)")
    ax.set_title(title, loc="left", fontsize=8.5, color=INK)
    return _png(fig)


TYPE_FR = {"pad": "pad", "via": "via / broche", "corner": "coin", "slot": "fente", "stub": "stub"}


def _elements_html(sig: dict) -> str:
    items = sorted(sig.get("pads", []) + sig.get("elements", []), key=lambda e: e["s_mm"])
    if not items:
        return "<p class='sub'>Aucune discontinuité modélisable sur le chemin principal.</p>"
    rows = "".join(f"<tr><td>{TYPE_FR.get(e['type'], e['type'])}</td><td>{e['s_mm']:.2f}</td>"
                   f"<td style='text-align:left;white-space:normal'>{_esc(e['info'])}</td></tr>" for e in items)
    lo = sig.get("line_only")
    cmp = ""
    if lo:
        cmp = (f"<p>Effet des discontinuités : return loss {lo['return_loss_db_at_fkey']:.1f} → "
               f"<b>{sig['return_loss_db_at_fkey']:.1f} dB</b>"
               + (f", réflexion crête {lo['tdr_peak_reflection_pct']:.1f} → <b>{sig['tdr_peak_reflection_pct']:.1f} %</b>"
                  if "tdr_peak_reflection_pct" in lo and "tdr_peak_reflection_pct" in sig else "")
               + " (ligne seule → avec discontinuités).</p>")
    return (cmp + "<table><thead><tr><th>Élément</th><th>s (mm)</th><th style='text-align:left'>Modèle</th></tr>"
            "</thead><tbody>" + rows + "</tbody></table>")


def fig_pair_skew(pair: dict) -> Optional[str]:
    """Écart de longueur cumulé P − N le long du tracé (mm, et ps sur l'axe de droite)."""
    if not pair or len(pair.get("curve_s_mm", [])) < 2:
        return None
    plt, _ = _mpl()
    x = np.array(pair["curve_s_mm"])
    d = np.array(pair["curve_delta_mm"])
    k = math.sqrt(pair["eps_eff"]) / 299_792_458.0 * 1e-3 * 1e12        # ps par mm
    fig, ax = plt.subplots(figsize=(9.5, 2.6))
    ax.axhline(0, color=INK2, linewidth=0.8)
    ax.plot(x, d, color=BLUE, linewidth=1.8, drawstyle="steps-mid")
    if "worst_step_at_mm" in pair:
        ax.axvline(pair["worst_step_at_mm"], color=RED, linewidth=0.8, linestyle="--")
        ax.annotate(f"plus forte marche : {pair['worst_step_mm']:.2f} mm", (pair["worst_step_at_mm"], 1),
                    xycoords=("data", "axes fraction"), xytext=(-3, -10), textcoords="offset points", ha="right",
                    fontsize=7, color=RED)
    ax.set_xlabel("abscisse le long du brin P (mm)")
    ax.set_ylabel("écart P − N (mm)")
    sec = ax.secondary_yaxis("right", functions=(lambda v: v * k, lambda v: v / k))
    sec.set_ylabel("skew (ps)")
    unc = pair.get("uncoupled_delta_mm")
    ax.set_title(f"Écart de longueur cumulé P − N sur la partie couplée (marches = où le déséquilibre se crée) ; "
                 f"total {pair['delta_mm']:+.2f} mm = {pair['skew_ps']:+.1f} ps"
                 + (f", dont {unc:+.2f} mm aux extrémités découplées" if unc is not None and abs(unc) > 0.005 else ""),
                 loc="left", fontsize=8.5, color=INK)
    fig.tight_layout()
    return _png(fig)


def _pair_html(tr: TargetReport) -> str:
    p = getattr(tr, "pair", None)
    if not p:
        return ""
    sig = getattr(tr, "signal", None) or {}
    rows = [("Longueur P / N", f"{p['len_p_mm']:.2f} / {p['len_n_mm']:.2f} mm "
                                f"({_esc(p['p_net'])} / {_esc(p['n_net'])})"),
            ("Écart de longueur", f"<b>{p['delta_mm']:+.2f} mm</b> (P − N)"),
            ("Skew intra-paire", f"<b>{p['skew_ps']:+.1f} ps</b>"),
            ("Dont extrémités découplées", f"{p['uncoupled_delta_mm']:+.2f} mm (connecteur, sortie de boîtier…)"
             if p.get("uncoupled_delta_mm") is not None else "—"),
            ("Couches P / N", f"{', '.join(p['layers_p'])} / {', '.join(p['layers_n'])}"
             + (" — <span class='warn'>les deux brins ne suivent pas les mêmes couches</span>"
                if p["layers_p"] != p["layers_n"] else ""))]
    if sig.get("skew_pct_rise") is not None:
        rows.append(("Skew / temps de montée", f"{sig['skew_pct_rise']:.1f} % (repère : ≤ 10 %)"))
    if sig.get("skew_pct_ui") is not None:
        rows.append(("Skew / durée d'un bit (UI)", f"{sig['skew_pct_ui']:.2f} %"))
    if "mode_conversion_db_at_fkey" in sig:
        rows.append((f"Conversion différentiel → commun due au skew à {_fmt_f(sig['f_key'])}",
                     f"{sig['mode_conversion_db_at_fkey']:.1f} dB (|sin(π f Δt)|)"))
    table = "<table><tbody>" + "".join(f"<tr><td style='text-align:left'>{a}</td><td style='text-align:left'>{b}</td></tr>"
                                       for a, b in rows) + "</tbody></table>"
    img = fig_pair_skew(p)
    return ("<div class='card'><b>Symétrie de la paire</b>" + table +
            ("<img alt='écart de longueur cumulé' src='" + img + "'>" if img else "") + PAIR_HELP + "</div>")


PAIR_HELP = """
<details><summary>Ce qui compte sur une paire différentielle</summary><ol>
<li><b>Zdiff le long du tracé</b> (courbe Z(s), carte) : dans la tolérance (souvent ±10 %) sur toute la
longueur ; un tronçon hors tolérance réfléchit.</li>
<li><b>Symétrie P / N</b> : un écart de longueur (skew) convertit une partie du signal différentiel en mode
commun (œil qui se ferme, rayonnement CEM). Repère courant : skew ≤ 10 % du temps de montée ; les guides de
conception demandent typiquement quelques millimètres pour l'USB 2.0 et environ 0,1 à 0,15 mm pour l'USB 3.x
ou le PCIe. Compenser <i>près de la cause</i> (le virage qui crée l'écart), par petites ondulations sur le
brin court.</li>
<li><b>Discontinuités</b> : connecteur (broches traversantes : fût, stubs), vias, pads (ESD, résistances),
virages ; voir la TDR et la contribution de chaque tronçon. Un élément présent sur un seul brin est aussi
une asymétrie.</li>
<li><b>Chemin de retour</b> : plan de référence continu sous la paire (pas de fente), vias de retour
(masse) près de chaque changement de couche.</li>
<li><b>Couplage et Zcomm</b> (détail au pire point) : un gap constant garde Zdiff constant ; Zcomm renseigne
sur la terminaison et le filtrage du mode commun.</li>
</ol></details>
"""


def _signal_table(run: AnalysisRun) -> str:
    rows = []
    for tr in run.targets:
        g = getattr(tr, "signal", None)
        if not g:
            continue
        tdr = (f"{g['tdr_z_min']:.1f} – {g['tdr_z_max']:.1f}", f"{g['tdr_peak_reflection_pct']:.1f} %") \
            if "tdr_z_min" in g else ("—", "—")
        lo = g.get("line_only", {})
        tdr_lo = f"{lo['tdr_peak_reflection_pct']:.1f} %" if "tdr_peak_reflection_pct" in lo else \
            (tdr[1] if not g.get("elements") and not g.get("pads") else "—")
        n_el = len(g.get("elements", [])) + len(g.get("pads", []))
        rows.append(f"<tr><td>{_esc(tr.label)}</td><td>{_esc(g['spec']['name'])}</td><td>{g['zref']:g}</td>"
                    f"<td>{_fmt_f(g['f_key'])}</td><td>{g['return_loss_db_at_fkey']:.1f}</td>"
                    f"<td>{g['mismatch_loss_db_at_fkey']:.3f}</td><td>{g['worst_mismatch_loss_db_in_band']:.3f}</td>"
                    f"<td>{g['gamma_static_pct']:.1f} %</td><td>{tdr[0]}</td><td>{tdr_lo}</td><td>{tdr[1]}</td>"
                    f"<td>{n_el}</td><td>{g['interpolated_pct']:.0f} %</td>"
                    f"<td>{(format(g['skew_ps'], '+.1f') + ' ps') if 'skew_ps' in g else '—'}</td></tr>")
    if not rows:
        return ""
    return ("<table><thead><tr><th>Cible</th><th>Signal</th><th>Zref (Ω)</th><th>f clé</th>"
            "<th>Return loss (dB)</th><th>Perte désadapt. (dB)</th><th>Pire perte ≤ f clé (dB)</th>"
            "<th>|Γ| statique max</th><th>Z vue TDR (Ω)</th><th>Réflexion crête ligne seule</th>"
            "<th>Réflexion crête avec discontinuités</th><th>Discontinuités modélisées</th><th>Longueur interpolée</th>"
            "<th>Skew P/N</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>" + SIGNAL_HELP)


SIGNAL_HELP = """
<details><summary>Comment lire ces valeurs ?</summary><ul>
<li><b>f clé</b> : fréquence de Nyquist (débit / 2) pour un signal numérique, fréquence d'intérêt en RF.</li>
<li><b>Return loss</b> = −|S11| en dB à f clé : plus il est grand, moins le signal est réfléchi
(≥ 20 dB : réflexion ≤ 10 % ; 10 dB : ≈ 32 %).</li>
<li><b>Zones de qualité</b> des courbes (usage RF courant) : return loss ≥ 20 dB <b>excellent</b> (perte de
désadaptation ≤ 0,044 dB, ≥ 99 % de la puissance transmise) ; 15–20 dB <b>bon</b> (≤ 0,14 dB) ; 10–15 dB
<b>acceptable</b> (≤ 0,46 dB ; 10 dB est le critère classique d'adaptation d'une <i>antenne</i>) ; &lt; 10 dB
<b>mauvais</b>. Une <i>piste</i> ne devrait pas consommer ce budget : viser ≥ 20 dB pour la liaison seule.
Pour le numérique, ces zones sont indicatives ; la norme (USB, PCIe…) fixe son propre gabarit SDD11.</li>
<li><b>Pertes dissipatives</b> (diélectrique, cuivre) : non calculées ici. Ordre de grandeur sur FR-4
(tan δ ≈ 0,02) à 2,4 GHz pour une ligne 50 Ω : environ 0,05 à 0,1 dB/cm ; sur une piste RF courte, elles
dépassent souvent la perte de désadaptation.</li>
<li><b>Perte de désadaptation</b> = −10·log(1 − |S11|²) : puissance renvoyée vers la source au lieu d'atteindre
la charge. Une désadaptation ne dissipe rien ; cette « perte » est faible en dB (≈ 0,1 dB pour 64 Ω au lieu de
90 Ω), c'est pourquoi elle n'est <b>pas</b> le bon critère pour une liaison numérique.</li>
<li><b>Pour l'USB et les liaisons numériques</b>, ce qui compte est la <b>réflexion</b> : elle produit de la
sonnerie et de l'interférence entre symboles (fermeture de l'œil). Regarder la <b>réflexion crête</b> vue par un
front réel (TDR simulée, en % de l'amplitude) et le <b>return loss jusqu'à Nyquist</b> ; les normes (USB, PCIe…)
spécifient l'impédance différentielle et le return loss (SDD11), pas une « perte de désadaptation ».</li>
<li><b>|Γ| statique</b> : |Z − Zref| / (Z + Zref) le plus défavorable le long du tracé (réflexion d'une
interface en basse fréquence).</li>
<li><b>Discontinuités modélisées</b> (modèles localisés, quasi-statiques) : pads sur le trajet (coupe 2D avec le
cuivre du même net fusionné → ΔC), vias (fût coaxial, antipad mesuré, stubs de via, inductance de boucle sans
via de retour), stubs de piste (ligne ouverte), coins (ΔC d'excès de cuivre), fentes (L = 0,2·D·ln(D/W) nH).
Non modélisés : connecteurs, composants, virages de paire (interpolés).</li>
</ul></details>
"""


def fig_section(xs, sol, res, title: str) -> Optional[str]:
    if xs is None or sol is None or sol.phi is None:
        return None
    plt, _ = _mpl()
    from matplotlib.patches import Polygon as MplPoly, Rectangle
    fig, ax = plt.subplots(figsize=(9.5, 3.6))
    ax.grid(False)
    g = sol.grid
    sig = [c for c in xs.conductors if c.role == SIGNAL]
    x0 = min(c.xmin for c in sig)
    x1 = max(c.xmax for c in sig)
    span = x1 - x0
    y_lo, y_hi = xs.content_bounds_y()
    ys0 = min(c.y0 for c in sig)
    ys1 = max(c.y1 for c in sig)
    # hauteur de vue : du plan de référence le plus proche (+ marge) de part et d'autre du signal
    below = [c.y1 for c in xs.conductors if c.role == GROUND and c.y1 <= ys0 + 1e-12]
    above = [c.y0 for c in xs.conductors if c.role == GROUND and c.y0 >= ys1 - 1e-12]
    dists = ([ys0 - max(below)] if below else []) + ([min(above) - ys1] if above else [])
    h = max(max(dists) if dists else span / 2, 0.15 * span)
    yl = (max(y_lo, ys0 - 1.6 * h) - 0.2 * h, min(y_hi, ys1 + 1.6 * h) + 0.6 * h)
    half_x = max(2.5 * span, 2.0 * (yl[1] - yl[0]))
    xl = (max(xs.x_min, (x0 + x1) / 2 - half_x), min(xs.x_max, (x0 + x1) / 2 + half_x))
    ers = sorted({s.er for s in xs.slabs})
    for s in xs.slabs:
        k = ers.index(s.er) / max(len(ers) - 1, 1)
        ax.add_patch(Rectangle((xs.x_min * 1e3, s.y0 * 1e3), (xs.x_max - xs.x_min) * 1e3, (s.y1 - s.y0) * 1e3,
                               facecolor=(0.93 - 0.08 * k, 0.9 - 0.05 * k, 0.8 - 0.02 * k), edgecolor="none", zorder=0))
    for m in xs.masks:
        y0, y1 = (m.y_surface, m.y_surface + m.c1) if m.side == "top" else (m.y_surface - m.c1, m.y_surface)
        ax.add_patch(Rectangle((xs.x_min * 1e3, y0 * 1e3), (xs.x_max - xs.x_min) * 1e3, (y1 - y0) * 1e3,
                               facecolor="#c7e3cf", edgecolor="none", zorder=0.5))
    phi = sol.phi[..., 0] if sol.phi.shape[-1] == 1 else sol.phi[..., 0] - sol.phi[..., 1]
    X, Y = np.meshgrid(g.x * 1e3, g.y * 1e3)
    levels = np.linspace(phi.min(), phi.max(), 23)[1:-1]
    ax.contour(X, Y, phi, levels=levels, colors=BLUE, linewidths=0.6, alpha=0.8, zorder=2)
    colors = {SIGNAL: "#b5651d", GROUND: "#7d7c77", FLOATING: WARN}
    for c in xs.conductors:
        pts = [(c.x0b, c.y0), (c.x1b, c.y0), (c.x1t, c.y1), (c.x0t, c.y1)]
        if c.y1 - c.y0 < 1e-7:
            ax.plot([c.xmin * 1e3, c.xmax * 1e3], [c.y0 * 1e3, c.y0 * 1e3], color=colors[c.role], linewidth=2.5, zorder=3)
        else:
            ax.add_patch(MplPoly(np.array(pts) * 1e3, closed=True, facecolor=colors[c.role], edgecolor=INK,
                                 linewidth=0.4, zorder=3))
        if c.role == SIGNAL or (c.role != GROUND and xl[0] < c.xmin < xl[1]):
            ax.annotate(c.net or "flottant", ((c.xmin + c.xmax) / 2 * 1e3, c.y1 * 1e3), fontsize=7, color=INK,
                        ha="center", va="bottom", xytext=(0, 3), textcoords="offset points", zorder=5)
    ax.set_xlim(xl[0] * 1e3, xl[1] * 1e3)
    ax.set_ylim(yl[0] * 1e3, yl[1] * 1e3)
    ax.set_xlabel("position dans la coupe (mm)")
    ax.set_ylabel("hauteur (mm)")
    ax.set_title(title, color=INK, loc="left", fontsize=8.5)
    return _png(fig)


# ------------------------------------------------------------------ HTML
CSS = """
:root{--bg:#fcfcfb;--card:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e3df;--accent:#2a78d6;--bad:#c73a39;--warn:#8a5a00}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#1a1a19;--card:#232322;--ink:#f4f4f2;--ink2:#c3c2b7;--line:#3a3a37;--accent:#6da7ec;--bad:#e66767;--warn:#eda100}}
:root[data-theme="dark"]{--bg:#1a1a19;--card:#232322;--ink:#f4f4f2;--ink2:#c3c2b7;--line:#3a3a37;--accent:#6da7ec;--bad:#e66767;--warn:#eda100}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}
h3{font-size:15px;margin:20px 0 6px}.sub{color:var(--ink2)}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;margin:10px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--ink2);font-weight:600}td.bad{color:var(--bad);font-weight:600}
img{max-width:100%;height:auto;display:block;background:#fff;border-radius:4px}ul{margin:4px 0;padding-left:20px}.warn{color:var(--warn)}
code{font-size:12px}details summary{cursor:pointer;color:var(--ink2)}
"""


def _stats_table(run: AnalysisRun) -> str:
    rows = []
    for tr in run.targets:
        st, t = tr.stats, tr.target
        def f(k, fmt="{:.1f}"):
            v = st.get(k)
            return fmt.format(v) if isinstance(v, (int, float)) and math.isfinite(v) else "—"
        out = st.get("pct_out")
        cls = ' class="bad"' if isinstance(out, float) and out > 0 else ""
        rows.append(
            f"<tr><td>{_esc(tr.label)}</td><td>{'paire' if t['kind'] == 'pair' else 'simple'}</td>"
            f"<td>{t['z_target']:g} ± {t['tol'] * 100:g} %</td><td>{f('z_min')}</td><td>{f('z_mean')}</td>"
            f"<td>{f('z_max')}</td><td{cls}>{f('pct_out', '{:.0f} %')}</td><td>{f('pct_high', '{:.0f} %')}</td>"
            f"<td>{f('pct_low', '{:.0f} %')}</td><td>{f('length_mm')}</td><td>{f('pct_valid_length', '{:.0f} %')}</td>"
            f"<td>{st.get('n_discontinuity', 0)}</td><td>{st.get('n_no_reference', 0)}</td></tr>")
    return ("<table><thead><tr><th>Cible</th><th>Type</th><th>Cible (Ω)</th><th>Min</th><th>Moy.</th><th>Max</th>"
            "<th>Hors tol.</th><th>Trop haut</th><th>Trop bas</th><th>Longueur (mm)</th><th>Longueur calculée</th>"
            "<th>Discont.</th><th>Pertes réf.</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<p class='sub'>Pourcentages pondérés par la longueur, sur les portions calculées "
            "(hors discontinuités et pertes de référence).</p>")


def _stackup_table(st: dict) -> str:
    rows = []
    for l in st["layers"]:
        er = l.get("er", "")
        rows.append(f"<tr><td>{_esc(l['name'])}</td><td>{_esc(l['kind'])} {_esc(l.get('dielectric_type', ''))}</td>"
                    f"<td>{l['thickness']:.4f}</td><td>{er}</td><td>{_esc(l.get('material', ''))}</td></tr>")
    return ("<table><thead><tr><th>Couche</th><th>Type</th><th>Épaisseur (mm)</th><th>εr</th><th>Matériau</th></tr>"
            "</thead><tbody>" + "".join(rows) + "</tbody></table>")


def _worst(tr: TargetReport) -> Optional[SampleResult]:
    i = tr.stats.get("worst_index")
    return tr.samples[i] if i is not None and 0 <= i < len(tr.samples) else None


def build_report(run: AnalysisRun, engine: Optional[Engine] = None, bm=None, path=None) -> str:
    """Construit le HTML ; l'écrit dans `path` si fourni. `engine` permet la coupe détaillée (champ)."""
    parts = [f"<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Impedance Map</title><style>{CSS}</style></head><body><main>"]
    parts.append(f"<h1>Impedance Map — {_esc(run.board)}</h1>"
                 f"<div class='sub'>Rapport généré le {datetime.datetime.now():%d/%m/%Y %H:%M} · plugin v{__version__} · "
                 f"stackup : {_esc(run.stackup.get('name'))} ({_esc(run.stackup.get('source'))}) · "
                 f"mode {_esc(run.options.get('mode'))} · pas {run.options.get('step', 0) * 1e3:g} mm</div>")
    tm = run.timing
    parts.append(f"<div class='card'>Calcul : {tm.get('total_s', 0):.1f} s · {tm.get('n_samples', 0)} échantillons · "
                 f"{tm.get('n_cuts', 0)} coupes · {tm.get('n_unique', 0)} coupes uniques · "
                 f"{tm.get('n_solved', 0)} résolues (le reste venait du cache).</div>")
    warns = list(run.warnings) + [f"{tr.label} : {w}" for tr in run.targets for w in tr.warnings]
    if warns:
        parts.append("<div class='card warn'><b>Avertissements</b><ul>" +
                     "".join(f"<li>{_esc(w)}</li>" for w in warns) + "</ul></div>")
    parts.append("<h2>Synthèse</h2><div class='card'>" + _stats_table(run) + "</div>")
    svg = board_svg(run, bm)
    if svg:
        parts.append("<h2>Vue du board</h2><div class='card'>" + svg + "</div>")
    sig_tab = _signal_table(run)
    if sig_tab:
        parts.append("<h2>Intégrité du signal</h2><div class='card'>" + sig_tab + "</div>")
    parts.append("<h2>Détail par cible</h2>")
    for tr in run.targets:
        parts.append(f"<h3>{_esc(tr.label)}</h3>")
        z = fig_zs(tr)
        if z:
            parts.append("<div class='card'><img alt='Z le long du tracé' src='" + z + "'></div>")
        parts.append(_pair_html(tr))
        w = _worst(tr)
        if w is not None and w.result:
            r = w.result
            if tr.target["kind"] == "pair":
                desc = (f"Zdiff {r['zdiff']:.1f} Ω · Zodd {r['zodd']:.1f} · Zeven {r['zeven']:.1f} · Zcomm {r['zcomm']:.1f} Ω · "
                        f"Z0 brins {r['z_self'][0]:.1f}/{r['z_self'][1]:.1f} Ω · k = {r['k_coupling']:.3f} · "
                        f"εeff impair/pair {r['eps_eff_odd']:.2f}/{r['eps_eff_even']:.2f}")
            else:
                desc = f"Z0 {r['z0']:.1f} Ω · εeff {r['eps_eff']:.2f} · {r['delay_ps_per_mm']:.2f} ps/mm"
            parts.append(f"<div class='card'><b>Pire point</b> : s = {w.s * 1e3:.2f} mm, ({w.x * 1e3:.2f} ; {w.y * 1e3:.2f}) mm, "
                         f"couche {_esc(w.layer)}, références : {_esc(', '.join(w.references) or '—')}<br>{desc}"
                         f"<br><span class='sub'>Erreur de discrétisation estimée : {r.get('error_estimate', float('nan')) * 100:.2f} %</span>")
            if engine is not None:
                det = engine.detailed_section(run, w)
                if det:
                    xs, sol, res = det
                    img = fig_section(xs, sol, res, f"Coupe au pire point — {tr.label} — équipotentielles" +
                                      (" du mode impair (pointillés : potentiel négatif)"
                                       if tr.target["kind"] == "pair" else ""))
                    if img:
                        parts.append("<img alt='coupe transverse' src='" + img + "'>")
            parts.append("</div>")
        sig = getattr(tr, "signal", None)
        if sig:
            parts.append("<div class='card'><b>Intégrité du signal</b> — " + _esc(sig["spec"]["name"]) +
                         f", Zref {sig['zref']:g} Ω : return loss {sig['return_loss_db_at_fkey']:.1f} dB et perte de "
                         f"désadaptation {sig['mismatch_loss_db_at_fkey']:.3f} dB à {_fmt_f(sig['f_key'])}" +
                         (f" ; réflexion crête {sig['tdr_peak_reflection_pct']:.1f} % (TDR)" if "tdr_z_min" in sig else "") +
                         ".")
            if sig.get("no_reference_mm", 0) > 0 and not any(e["type"] == "slot" for e in sig.get("elements", [])):
                parts.append(f"<br><span class='warn'>⚠ {sig['no_reference_mm']:.1f} mm sans plan de référence "
                             "non identifiés comme une fente : effet interpolé, donc SOUS-ESTIMÉ.</span>")
            parts.append(_elements_html(sig))
            parts.append(_sections_html(sig))
            for img in (fig_signal_freq(sig), fig_sections(sig), fig_tdr(sig, tr)):
                if img:
                    parts.append("<img alt='intégrité du signal' src='" + img + "'>")
            parts.append("</div>")
        disc = {}
        for s in tr.samples:
            if s.status != "ok":
                disc[(s.status, s.reason)] = disc.get((s.status, s.reason), 0) + 1
        if disc:
            parts.append("<details class='card'><summary>Points non calculés ou signalés</summary><ul>" +
                         "".join(f"<li>{_esc(k[0])} — {_esc(k[1])} : {v}</li>" for k, v in sorted(disc.items())) +
                         "</ul></details>")
    parts.append("<h2>Stackup utilisé</h2><div class='card'>" + _stackup_table(run.stackup) + "</div>")
    parts.append(HYPOTHESES)
    parts.append("</main></body></html>")
    doc = "\n".join(parts)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(doc)
    return doc


HYPOTHESES = """
<h2>Hypothèses et limites</h2><div class='card'><ul>
<li>Solveur électrostatique 2D quasi-statique (mode quasi-TEM), différences finies sur grille non uniforme,
extrapolation de Richardson ; L = μ0·ε0·C0⁻¹, conducteurs parfaits.</li>
<li>Conducteurs voisins au repos à 0 V ; cuivre sans net flottant (charge nette nulle) ; seul le cuivre
réellement présent dans la coupe sert de référence (fentes et changements de plan visibles).</li>
<li>Coins, arcs serrés, pads, vias, changements de couche et virages de paire : effets 3D, marqués
« discontinuité » (aucune valeur affichée).</li>
<li>Hors périmètre : pertes (tan δ, conducteur), dispersion en fréquence, rugosité, effet de peau,
couplage 3D le long de la ligne. Les valeurs sont des impédances statiques (basse fréquence du modèle quasi-TEM).</li>
<li>Précision de la géométrie : épaisseurs et εr du stackup choisi ; masque conforme (c1 sur substrat, c2 sur piste).</li>
</ul></div>
"""
