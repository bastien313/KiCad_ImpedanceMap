"""Génère les boards d'exemple (stackup JLC04161H-7628, 4 couches, 1,6 mm).

    python examples/make_demo_board.py            # les deux boards
    python examples/make_demo_board.py base       # demo_impedance.kicad_pcb seulement
    python examples/make_demo_board.py disc       # demo_discontinuites.kicad_pcb seulement
    option --no-refill : ne pas lancer kicad-cli

⚠ Ne pas régénérer un board ouvert dans KiCad (KiCad l'écraserait à l'enregistrement).

Largeurs calculées avec le calcul inverse du plugin (mode fast) : 50 Ω microstrip F.Cu ≈ 0,350 mm,
stripline In2 ≈ 0,268 mm, paire 90 Ω F.Cu ≈ 0,283 / 0,200 mm.

demo_impedance.kicad_pcb (cas du cahier des charges) :
  * MS_50    microstrip F.Cu sur plan In1 avec coin à 45°, pad d'entrée, voisine AGGR
             à 0,15 mm sur 15 mm, îlot de cuivre SANS NET (flottant) à 0,3 mm ;
  * MS_SLOT  microstrip F.Cu traversant une FENTE de 1 mm dans le plan In1 ;
  * MS_VIA   microstrip F.Cu -> via -> B.Cu, SANS via de retour GND proche ;
  * SL_50    stripline In2.Cu entre In1 (GND) et B.Cu (GND), masse coplanaire In2 ;
  * USB_P/N  paire différentielle 90 Ω F.Cu d'environ 104 mm avec 2 coins à 45°.

demo_discontinuites.kicad_pcb (modèles localisés de l'intégrité du signal) :
  * MS_PAD     microstrip traversant un pad CMS 1,0 × 1,2 mm (type protection ESD) ;
  * MS_STUB    microstrip avec une branche en T de 8 mm vers un point de test (stub) ;
  * SL_VIA     F.Cu -> via traversant -> stripline In2 (stub de via In2 -> B.Cu), via de retour GND ;
  * MS_SLOT8   microstrip traversant une fente de 8 mm × 1 mm dans le plan In1 ;
  * ESD_P/N    paire 90 Ω traversant deux pads CMS (protection ESD).

Les remplissages de zones sont calculés par KiCad :
    kicad-cli pcb drc --refill-zones --save-board <board>
(fait automatiquement par ce script si kicad-cli est trouvé).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import uuid

from shapely.geometry import LineString

HERE = pathlib.Path(__file__).resolve().parent

W_MS = 0.350
W_SL = 0.268
W_DP, S_DP = 0.283, 0.200
W_AGGR = 0.20


def u():
    return str(uuid.uuid4())


class Board:
    def __init__(self, nets):
        self.nets = [""] + list(nets)
        self.idx = {n: i for i, n in enumerate(self.nets)}
        self.items = []

    def seg(self, a, b, w, layer, net):
        self.items.append(f'\t(segment (start {a[0]:.4f} {a[1]:.4f}) (end {b[0]:.4f} {b[1]:.4f}) (width {w}) '
                          f'(layer "{layer}") (net {self.idx[net]}) (uuid "{u()}"))')

    def track(self, pts, w, layer, net):
        for a, b in zip(pts, pts[1:]):
            self.seg(a, b, w, layer, net)

    def via(self, p, net, size=0.6, drill=0.3):
        self.items.append(f'\t(via (at {p[0]:.4f} {p[1]:.4f}) (size {size}) (drill {drill}) (layers "F.Cu" "B.Cu") '
                          f'(net {self.idx[net]}) (uuid "{u()}"))')

    def zone(self, net, layer, pts, clearance=0.3, island_mode=None, priority=0):
        pts_s = " ".join(f"(xy {x:.4f} {y:.4f})" for x, y in pts)
        isl = f" (island_removal_mode {island_mode})" if island_mode is not None else ""
        self.items.append(
            f'\t(zone (net {self.idx[net]}) (net_name "{net}") (layer "{layer}") (uuid "{u()}") (hatch edge 0.5)\n'
            f'\t\t(priority {priority})\n'
            f'\t\t(connect_pads (clearance {clearance})) (min_thickness 0.2) (filled_areas_thickness no)\n'
            f'\t\t(fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5){isl})\n'
            f'\t\t(polygon (pts {pts_s})))')

    def keepout(self, layer, pts):
        pts_s = " ".join(f"(xy {x:.4f} {y:.4f})" for x, y in pts)
        self.items.append(
            f'\t(zone (net 0) (net_name "") (layer "{layer}") (uuid "{u()}") (name "fente") (hatch edge 0.5)\n'
            f'\t\t(connect_pads (clearance 0)) (min_thickness 0.2) (filled_areas_thickness no)\n'
            f'\t\t(keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))\n'
            f'\t\t(fill (thermal_gap 0.5) (thermal_bridge_width 0.5))\n'
            f'\t\t(polygon (pts {pts_s})))')

    def pad(self, ref, at, net, size=(1.5, 1.0), shape="rect"):
        self.items.append(
            f'\t(footprint "demo:PAD_SMD" (layer "F.Cu") (uuid "{u()}") (at {at[0]:.4f} {at[1]:.4f})\n'
            f'\t\t(property "Reference" "{ref}" (at 0 -1.5 0) (layer "F.SilkS") (uuid "{u()}")\n'
            f'\t\t\t(effects (font (size 0.8 0.8) (thickness 0.12))))\n'
            f'\t\t(property "Value" "PAD" (at 0 1.5 0) (layer "F.Fab") (uuid "{u()}")\n'
            f'\t\t\t(effects (font (size 0.8 0.8) (thickness 0.12))))\n'
            f'\t\t(attr smd)\n'
            f'\t\t(pad "1" smd {shape} (at 0 0) (size {size[0]} {size[1]}) (layers "F.Cu" "F.Mask" "F.Paste") '
            f'(net {self.idx[net]} "{net}") (uuid "{u()}")))')

    def pair(self, center_pts, net_p, net_n, layer="F.Cu"):
        c = LineString(center_pts)
        off = (W_DP + S_DP) / 2
        self.track(list(c.offset_curve(off, join_style="mitre").coords), W_DP, layer, net_p)
        self.track(list(c.offset_curve(-off, join_style="mitre").coords), W_DP, layer, net_n)

    def write(self, path, bbox):
        X0, Y0, X1, Y1 = bbox
        edge = (f'\t(gr_rect (start {X0} {Y0}) (end {X1} {Y1}) (stroke (width 0.1) (type default)) '
                f'(fill none) (layer "Edge.Cuts") (uuid "{u()}"))')
        nets = "\n".join(f'\t(net {i} "{n}")' for i, n in enumerate(self.nets))
        path.write_text(HEADER + nets + "\n" + "\n".join([edge] + self.items) + "\n)\n", encoding="utf-8")


HEADER = """(kicad_pcb (version 20240108) (generator "impedance_map_demo") (generator_version "8.0")
\t(general (thickness 1.5862) (legacy_teardrops no))
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "In2.Cu" signal) (31 "B.Cu" signal)
\t\t(32 "B.Adhes" user "B.Adhesive") (33 "F.Adhes" user "F.Adhesive")
\t\t(34 "B.Paste" user) (35 "F.Paste" user)
\t\t(36 "B.SilkS" user "B.Silkscreen") (37 "F.SilkS" user "F.Silkscreen")
\t\t(38 "B.Mask" user) (39 "F.Mask" user)
\t\t(40 "Dwgs.User" user "User.Drawings") (41 "Cmts.User" user "User.Comments")
\t\t(42 "Eco1.User" user "User.Eco1") (43 "Eco2.User" user "User.Eco2")
\t\t(44 "Edge.Cuts" user) (45 "Margin" user)
\t\t(46 "B.CrtYd" user "B.Courtyard") (47 "F.CrtYd" user "F.Courtyard")
\t\t(48 "B.Fab" user) (49 "F.Fab" user)
\t\t(50 "User.1" user) (51 "User.2" user) (52 "User.3" user) (53 "User.4" user)
\t)
\t(setup
\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))
\t\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (thickness 0.03048) (epsilon_r 3.8))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "prepreg") (thickness 0.2104) (material "7628") (epsilon_r 4.4) (loss_tangent 0.02))
\t\t\t(layer "In1.Cu" (type "copper") (thickness 0.0152))
\t\t\t(layer "dielectric 2" (type "core") (thickness 1.065) (material "FR4") (epsilon_r 4.6) (loss_tangent 0.02))
\t\t\t(layer "In2.Cu" (type "copper") (thickness 0.0152))
\t\t\t(layer "dielectric 3" (type "prepreg") (thickness 0.2104) (material "7628") (epsilon_r 4.4) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.03048) (epsilon_r 3.8))
\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))
\t\t\t(copper_finish "None")
\t\t\t(dielectric_constraints no)
\t\t)
\t\t(pad_to_mask_clearance 0)
\t\t(allow_soldermask_bridges_in_footprints no)
\t)
"""


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def netclass(name, tw, dpw=None, dpg=None, prio=0):
    return {"name": name, "bus_width": 12, "clearance": 0.15, "diff_pair_gap": dpg or 0.25,
            "diff_pair_via_gap": 0.25, "diff_pair_width": dpw or 0.2, "line_style": 0,
            "microvia_diameter": 0.3, "microvia_drill": 0.1, "pcb_color": "rgba(0, 0, 0, 0.000)",
            "priority": prio, "schematic_color": "rgba(0, 0, 0, 0.000)", "track_width": tw,
            "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6}


def write_project(pcb: pathlib.Path, patterns):
    pro = {
        "board": {"design_settings": {"defaults": {}, "rules": {}}},
        "meta": {"filename": pcb.with_suffix(".kicad_pro").name, "version": 1},
        "net_settings": {
            "classes": [netclass("Default", 0.2, prio=2147483647), netclass("RF_50R", W_MS, prio=1),
                        netclass("USB_90R", W_DP, W_DP, S_DP, prio=0)],
            "meta": {"version": 3}, "net_colors": None, "netclass_assignments": None,
            "netclass_patterns": patterns,
        },
    }
    pcb.with_suffix(".kicad_pro").write_text(json.dumps(pro, indent=2), encoding="utf-8")


def build_base() -> pathlib.Path:
    pcb = HERE / "demo_impedance.kicad_pcb"
    b = Board(["GND", "MS_50", "MS_SLOT", "MS_VIA", "SL_50", "USB_P", "USB_N", "AGGR"])
    b.track([(106.0, 55.0), (140.0, 55.0), (150.0, 65.0)], W_MS, "F.Cu", "MS_50")
    b.pad("J1", (105.25, 55.0), "MS_50")
    y_aggr = 55.0 - (W_MS / 2 + 0.15 + W_AGGR / 2)
    b.track([(115.0, y_aggr), (130.0, y_aggr)], W_AGGR, "F.Cu", "AGGR")
    y_isl = 55.0 + W_MS / 2 + 0.30
    b.zone("", "F.Cu", rect(120.0, y_isl, 123.0, y_isl + 2.0), clearance=0.1, island_mode=1, priority=5)
    b.track([(106.0, 70.0), (150.0, 70.0)], W_MS, "F.Cu", "MS_SLOT")
    b.keepout("In1.Cu", rect(125.0, 66.0, 126.0, 74.0))
    b.track([(106.0, 80.0), (125.0, 80.0)], W_MS, "F.Cu", "MS_VIA")
    b.via((125.0, 80.0), "MS_VIA")
    b.track([(125.0, 80.0), (150.0, 80.0)], W_MS, "B.Cu", "MS_VIA")
    b.track([(106.0, 95.0), (150.0, 95.0)], W_SL, "In2.Cu", "SL_50")
    b.pair([(104.0, 106.0), (172.0, 106.0), (176.0, 102.0), (176.0, 72.0)], "USB_P", "USB_N")
    X0, Y0, X1, Y1 = 100.0, 50.0, 182.0, 112.0
    b.zone("GND", "In1.Cu", rect(X0 + 0.5, Y0 + 0.5, X1 - 0.5, Y1 - 0.5))
    b.zone("GND", "In2.Cu", rect(X0 + 0.5, 88.0, 160.0, Y1 - 0.5))
    b.zone("GND", "B.Cu", rect(X0 + 0.5, 88.0, 160.0, Y1 - 0.5))
    for x in range(108, 150, 6):
        b.via((float(x), 92.5), "GND")
        b.via((float(x), 97.5), "GND")
    b.write(pcb, (X0, Y0, X1, Y1))
    write_project(pcb, [{"netclass": "USB_90R", "pattern": "USB_*"}, {"netclass": "RF_50R", "pattern": "MS_*"},
                        {"netclass": "RF_50R", "pattern": "SL_*"}])
    return pcb


def build_disc() -> pathlib.Path:
    pcb = HERE / "demo_discontinuites.kicad_pcb"
    b = Board(["GND", "MS_PAD", "MS_STUB", "SL_VIA", "MS_SLOT8", "ESD_P", "ESD_N"])
    # pad CMS sur le trajet (segments arrêtés au centre du pad, comme un routage réel)
    b.track([(105.0, 55.0), (125.0, 55.0)], W_MS, "F.Cu", "MS_PAD")
    b.track([(125.0, 55.0), (145.0, 55.0)], W_MS, "F.Cu", "MS_PAD")
    b.pad("D1", (125.0, 55.0), "MS_PAD", size=(1.0, 1.2))
    # branche en T de 8 mm vers un point de test
    b.track([(105.0, 63.0), (120.0, 63.0)], W_MS, "F.Cu", "MS_STUB")
    b.track([(120.0, 63.0), (145.0, 63.0)], W_MS, "F.Cu", "MS_STUB")
    b.track([(120.0, 63.0), (120.0, 71.0)], W_MS, "F.Cu", "MS_STUB")
    b.pad("TP1", (120.0, 71.0), "MS_STUB", size=(1.5, 1.5), shape="circle")
    # F.Cu -> via -> stripline In2 : stub de via In2 -> B.Cu ; via de retour GND à 0,9 mm
    b.track([(105.0, 95.0), (112.0, 95.0)], W_MS, "F.Cu", "SL_VIA")
    b.via((112.0, 95.0), "SL_VIA")
    b.track([(112.0, 95.0), (145.0, 95.0)], W_SL, "In2.Cu", "SL_VIA")
    b.via((112.0, 95.9), "GND")
    # fente 8 mm × 1 mm dans le plan In1 sous une microstrip
    b.track([(105.0, 78.0), (145.0, 78.0)], W_MS, "F.Cu", "MS_SLOT8")
    b.keepout("In1.Cu", rect(124.5, 74.0, 125.5, 82.0))
    # paire 90 Ω traversant deux pads CMS de protection ESD
    b.pair([(104.0, 104.0), (146.0, 104.0)], "ESD_P", "ESD_N")
    off = (W_DP + S_DP) / 2
    # pads 0,6 × 0,6 mm décalés vers l'EXTÉRIEUR de chaque brin (le gap de la paire reste libre)
    b.pad("D2", (125.0, 104.0 - off - 0.15), "ESD_P", size=(0.6, 0.6))
    b.pad("D3", (125.0, 104.0 + off + 0.15), "ESD_N", size=(0.6, 0.6))
    X0, Y0, X1, Y1 = 100.0, 50.0, 150.0, 110.0
    b.zone("GND", "In1.Cu", rect(X0 + 0.5, Y0 + 0.5, X1 - 0.5, Y1 - 0.5))
    b.zone("GND", "In2.Cu", rect(X0 + 0.5, 88.0, X1 - 0.5, 100.0))
    b.zone("GND", "B.Cu", rect(X0 + 0.5, 88.0, X1 - 0.5, Y1 - 0.5))
    b.write(pcb, (X0, Y0, X1, Y1))
    write_project(pcb, [{"netclass": "USB_90R", "pattern": "ESD_*"}, {"netclass": "RF_50R", "pattern": "MS_*"},
                        {"netclass": "RF_50R", "pattern": "SL_*"}])
    return pcb


def find_kicad_cli():
    for c in ("kicad-cli", r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
              r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe", "/usr/bin/kicad-cli"):
        p = shutil.which(c) or (c if pathlib.Path(c).exists() else None)
        if p:
            return [p]
    if shutil.which("flatpak"):
        return ["flatpak", "run", "--command=kicad-cli", "org.kicad.KiCad"]
    return None


def refill(pcb: pathlib.Path):
    cli = find_kicad_cli()
    if not cli:
        print("kicad-cli introuvable : ouvrez le board dans KiCad, remplissez les zones (B) puis enregistrez.")
        return
    out = pcb.with_name(pcb.stem + "_drc.json")
    r = subprocess.run(cli + ["pcb", "drc", "--refill-zones", "--save-board", "--format", "json",
                              "-o", str(out), str(pcb)], capture_output=True, text=True)
    if out.exists():
        d = json.loads(out.read_text(encoding="utf-8"))
        errs = [v for v in d.get("violations", []) if v.get("severity") == "error"]
        print(f"{pcb.name} : zones remplies ; DRC {len(d.get('violations', []))} violations "
              f"({len(errs)} erreurs), {len(d.get('unconnected_items', []))} non connectés")
        out.unlink()
    else:
        print(r.stdout[-1500:], r.stderr[-1500:])


if __name__ == "__main__":
    which = [a for a in sys.argv[1:] if not a.startswith("--")] or ["base", "disc"]
    for w in which:
        pcb = build_base() if w == "base" else build_disc()
        print("Écrit :", pcb)
        if "--no-refill" not in sys.argv:
            refill(pcb)
