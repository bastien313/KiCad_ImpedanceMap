"""Interface en ligne de commande.

Exemples :
    python -m impedance_map analyze examples/demo_impedance.kicad_pcb --pairs --report out.html
    python -m impedance_map analyze board.kicad_pcb --nets MS_50 SL_50 --stackup jlcpcb:JLC04161H-7628
    python -m impedance_map analyze --live --netclass USB_90R --overlay       (KiCad ouvert)
    python -m impedance_map synth --stackup jlcpcb:JLC04161H-7628 --layer F.Cu --z 90 --gap 0.2
    python -m impedance_map stackups --layers 4
    python -m impedance_map stackup-show jlcpcb:JLC04161H-7628 [--json out.json]
    python -m impedance_map ui examples/demo_impedance.kicad_pcb        (interface en mode fichier)
    python -m impedance_map clear --live                                  (effacer l'overlay)
"""

from __future__ import annotations

import argparse
import sys
import time


def _load_board(args):
    if args.live:
        from .ui.controller import Controller
        ctl = Controller()
        ctl.connect()
        ctl.load()
        return ctl.bm, ctl.board_stackup, ctl
    from .extraction.file_reader import read_kicad_pcb
    bm = read_kicad_pcb(args.board)
    return bm, bm.stackup, None


def cmd_analyze(args):
    from .analysis import AnalysisOptions, Engine
    from .extraction.targets import build_targets
    from .stackup import load_stackup
    bm, board_st, ctl = _load_board(args)
    st = load_stackup(args.stackup, bm.copper_names, board_st)
    tg = build_targets(bm, nets=args.nets or [], netclass=args.netclass, all_pairs=args.pairs,
                       z_single=args.z, z_diff=args.zdiff, tol=args.tol / 100, pair_detection=not args.no_pair_detection,
                       z_from_class=not args.no_z_from_netclass)
    if not tg:
        print("Aucune cible : utilisez --nets, --netclass ou --pairs.", file=sys.stderr)
        return 2
    opt = AnalysisOptions(step=args.step * 1e-3, mode=args.mode, workers=args.workers,
                          window=args.window * 1e-3 if args.window else None, etch_factor=args.etch,
                          persist_cache=not args.no_cache)

    def progress(f, m):
        if not args.quiet:
            print(f"\r[{f * 100:5.1f} %] {m:<60}", end="", flush=True)

    eng = Engine(bm, st, opt, progress=progress)
    run = eng.run(tg)
    if not args.quiet:
        print()
    for tr in run.targets:
        s = tr.stats
        if "z_mean" in s:
            print(f"{tr.label:<30} {s['z_min']:7.2f} {s['z_mean']:7.2f} {s['z_max']:7.2f} Ω  "
                  f"cible {s['z_target']:g}±{s['tol'] * 100:g}%  hors tol. {s['pct_out']:5.1f} %  "
                  f"({s['n_valid']}/{s['n_samples']} pts, {s['n_discontinuity']} disc., {s['n_no_reference']} sans réf.)")
        else:
            print(f"{tr.label:<30} aucune portion calculable")
        for w in tr.warnings:
            print("   ⚠", w)
    for w in run.warnings:
        print("⚠", w)
    t = run.timing
    print(f"Temps : {t['total_s']:.2f} s ({t['n_cuts']} coupes, {t['n_unique']} uniques, {t['n_solved']} résolues)")
    if not args.no_signal:
        from .analysis.signal import PRESETS, SignalSpec, analyze_run
        base = PRESETS[args.signal] if args.signal else PRESETS["USB 2.0 High-Speed (480 Mb/s)"]
        name = f"RF {args.freq:g} MHz" if args.freq else (
            f"Numérique {args.bitrate:g} Mb/s" if args.bitrate and not args.signal else base.name)
        spec = SignalSpec(name, "rf" if args.freq else base.kind,
                          args.bitrate * 1e6 if args.bitrate else base.bitrate,
                          args.rise * 1e-12 if args.rise else base.rise,
                          args.freq * 1e6 if args.freq else base.freq, args.zref)
        analyze_run(run, spec)
        for tr in run.targets:
            g = tr.signal
            if g:
                extra = f", réflexion crête {g['tdr_peak_reflection_pct']:.1f} %" if "tdr_z_min" in g else ""
                lo = g.get("line_only", {})
                if "tdr_peak_reflection_pct" in lo:
                    extra += f" (ligne seule {lo['tdr_peak_reflection_pct']:.1f} %)"
                print(f"   {tr.label:<27} {g['spec']['name']} : RL {g['return_loss_db_at_fkey']:.1f} dB, "
                      f"perte de désadaptation {g['mismatch_loss_db_at_fkey']:.3f} dB à {g['f_key'] / 1e6:g} MHz{extra}")
                for e in sorted(g.get("pads", []) + g.get("elements", []), key=lambda e: e["s_mm"]):
                    print(f"      s = {e['s_mm']:6.2f} mm  {e['info']}")
    if args.json:
        run.to_json(args.json)
    if args.report:
        from .viz.report import build_report
        build_report(run, eng, bm, path=args.report)
        print("Rapport :", args.report)
    if args.overlay:
        if ctl is None:
            print("--overlay nécessite --live", file=sys.stderr)
        else:
            from .viz.overlay_kicad import apply_overlay
            from .viz.overlay_plan import OverlayOptions, plan_overlay
            o = OverlayOptions()
            r = apply_overlay(ctl.board, plan_overlay(run, o), o, id_store=ctl.overlay_id_store())
            print(f"Overlay : {r['n_items']} objets, groupe « {r['group'] or 'aucun'} »")
            for n in r["notes"]:
                print("  ", n)
    return 0


def cmd_synth(args):
    from .stackup import load_stackup
    from .synthesis import impedance, solve_gap, solve_width
    st = load_stackup(args.stackup)
    t = time.perf_counter()
    if args.gap is not None:
        w = solve_width(st, args.layer, args.z, s=args.gap * 1e-3, mode=args.mode)
        r = impedance(st, args.layer, w, args.gap * 1e-3, mode=args.mode)
        print(f"w = {w * 1e3:.4f} mm (gap {args.gap} mm) -> Zdiff {r.zdiff:.2f} Ω, Zodd {r.zodd:.2f}, Zeven {r.zeven:.2f}")
    elif args.width is not None:
        s = solve_gap(st, args.layer, args.z, args.width * 1e-3, mode=args.mode)
        r = impedance(st, args.layer, args.width * 1e-3, s, mode=args.mode)
        print(f"gap = {s * 1e3:.4f} mm (w {args.width} mm) -> Zdiff {r.zdiff:.2f} Ω")
    else:
        w = solve_width(st, args.layer, args.z, mode=args.mode)
        r = impedance(st, args.layer, w, mode=args.mode)
        print(f"w = {w * 1e3:.4f} mm -> Z0 {r.z0:.2f} Ω, εeff {r.eps_eff:.3f}, {r.delay_ps_per_mm:.2f} ps/mm")
    print(f"({time.perf_counter() - t:.1f} s, stackup {st.name})")
    return 0


def cmd_stackups(args):
    from .stackup import jlcpcb_metadata, jlcpcb_presets
    meta = jlcpcb_metadata()
    print(f"Source : {meta.get('_source')} (relevé le {meta.get('_fetched')})")
    for n in jlcpcb_presets(args.layers):
        print("  jlcpcb:" + n)
    return 0


def cmd_stackup_show(args):
    from .stackup import load_stackup
    st = load_stackup(args.spec)
    print(st.describe())
    if st.notes:
        print("Notes :", st.notes)
    if args.json:
        st.to_json(args.json)
        print("Écrit :", args.json)
    return 0


def cmd_ui(args):
    from .ui import run_plugin
    return run_plugin(args.board, force_tk=args.tk)


def cmd_clear(args):
    from .ui.controller import Controller
    ctl = Controller()
    ctl.connect()
    n, g = ctl.overlay_count()
    print(f"{n} objet(s) dans {g} groupe(s) ImpedanceMap.")
    if n == 0 and g == 0:
        return 0
    if not args.yes:
        if input("Supprimer ? [o/N] ").strip().lower() not in ("o", "oui", "y", "yes"):
            return 0
    print(ctl.clear_overlay(lambda a, b: True), "objet(s) supprimé(s).")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="impedance_map", description="Impedance Map — simulateur 2D d'impédance")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="analyser un board (fichier ou KiCad ouvert)")
    a.add_argument("board", nargs="?", help="fichier .kicad_pcb (sauf --live)")
    a.add_argument("--live", action="store_true", help="board ouvert dans KiCad (API IPC)")
    a.add_argument("--nets", nargs="*", help="nets à analyser")
    a.add_argument("--netclass", help="tous les nets routés de cette netclass")
    a.add_argument("--pairs", action="store_true", help="toutes les paires différentielles")
    a.add_argument("--no-pair-detection", action="store_true")
    a.add_argument("--no-z-from-netclass", action="store_true")
    a.add_argument("--stackup", default="board", help="board | jlcpcb:<nom> | fichier.json")
    a.add_argument("--z", type=float, default=50.0)
    a.add_argument("--zdiff", type=float, default=100.0)
    a.add_argument("--tol", type=float, default=10.0, help="tolérance en %%")
    a.add_argument("--step", type=float, default=0.5, help="pas d'échantillonnage (mm)")
    a.add_argument("--window", type=float, default=None, help="largeur de fenêtre fixe (mm)")
    a.add_argument("--etch", type=float, default=0.0, help="facteur de gravure (0 = rectangle)")
    a.add_argument("--mode", choices=["fast", "precise"], default="fast")
    a.add_argument("--workers", type=int, default=None)
    a.add_argument("--no-cache", action="store_true", help="ne pas lire/écrire le cache disque")
    a.add_argument("--report", help="rapport HTML")
    a.add_argument("--json", help="résultats JSON")
    a.add_argument("--overlay", action="store_true", help="créer l'overlay (avec --live)")
    a.add_argument("--quiet", action="store_true")
    from .analysis.signal import PRESETS
    a.add_argument("--signal", choices=list(PRESETS), help="signal de référence (défaut : USB 2.0 High-Speed)")
    a.add_argument("--bitrate", type=float, help="débit numérique (Mb/s), remplace celui du préréglage")
    a.add_argument("--rise", type=float, help="temps de montée 10-90 %% (ps) pour la TDR")
    a.add_argument("--freq", type=float, help="fréquence RF d'intérêt (MHz) : active le mode RF")
    a.add_argument("--zref", type=float, help="impédance de référence (Ω), défaut = cible")
    a.add_argument("--no-signal", action="store_true", help="ne pas calculer l'intégrité du signal")
    a.set_defaults(func=cmd_analyze)

    s = sub.add_parser("synth", help="calcul inverse largeur / gap")
    s.add_argument("--stackup", required=True)
    s.add_argument("--layer", required=True)
    s.add_argument("--z", type=float, required=True)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--gap", type=float, help="paire : gap fixé (mm), calcule w")
    g.add_argument("--width", type=float, help="paire : largeur fixée (mm), calcule le gap")
    s.add_argument("--mode", choices=["fast", "precise"], default="fast")
    s.set_defaults(func=cmd_synth)

    l = sub.add_parser("stackups", help="lister les préréglages JLCPCB")
    l.add_argument("--layers", type=int, default=None)
    l.set_defaults(func=cmd_stackups)

    sh = sub.add_parser("stackup-show", help="afficher un stackup (et l'exporter en JSON)")
    sh.add_argument("spec")
    sh.add_argument("--json")
    sh.set_defaults(func=cmd_stackup_show)

    u = sub.add_parser("ui", help="interface graphique en mode fichier")
    u.add_argument("board")
    u.add_argument("--tk", action="store_true")
    u.set_defaults(func=cmd_ui)

    c = sub.add_parser("clear", help="effacer l'overlay du board ouvert dans KiCad")
    c.add_argument("--live", action="store_true", default=True)
    c.add_argument("--yes", action="store_true")
    c.set_defaults(func=cmd_clear)

    args = p.parse_args(argv)
    if getattr(args, "cmd", None) == "analyze" and not args.live and not args.board:
        p.error("indiquez un fichier .kicad_pcb ou --live")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
