"""Régénère impedance_map/stackup/data/jlcpcb_stackups.json depuis https://jlcpcb.com/impedance.

Usage :
    python tools/fetch_jlcpcb_stackups.py            # télécharge la page
    python tools/fetch_jlcpcb_stackups.py page.html  # utilise un HTML déjà téléchargé

Le script lit :
  * les tableaux « N) JLCxxxxx Stackup » (couches, matériaux, épaisseurs) ;
  * le tableau des Dk des prepregs, le Dk du core et les paramètres du masque.
Il échoue bruyamment si la structure de la page a changé (à vérifier alors à la main).
Les stackups 2 couches ne figurent pas sur cette page : ils sont générés séparément
(voir `two_layer_presets`) et marqués « non officiels ».
"""

from __future__ import annotations

import datetime
import html as htmlmod
import json
import pathlib
import re
import sys
import urllib.request

URL = "https://jlcpcb.com/impedance"
OUT = pathlib.Path(__file__).resolve().parents[1] / "impedance_map" / "stackup" / "data" / "jlcpcb_stackups.json"
MIL = 0.0254  # mm


def fetch(path=None) -> str:
    if path:
        return pathlib.Path(path).read_text(encoding="utf-8")
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def page_text(s: str) -> str:
    t = re.sub(r"<script.*?</script>", "", s, flags=re.S)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S)
    t = htmlmod.unescape(re.sub(r"<[^>]+>", "\n", t))
    return " | ".join(l.strip() for l in t.split("\n") if l.strip())


def parse_parameters(txt: str):
    m = re.search(r"Prepreg type \| Dielectric constant \|(.*?)\| 2\.Solder mask", txt)
    if not m:
        raise RuntimeError("Tableau des Dk prepreg introuvable")
    toks = [t.strip() for t in m.group(1).split("|") if t.strip()]
    dk = {toks[i]: float(toks[i + 1]) for i in range(0, len(toks) - 1, 2)}
    m = re.search(r"Coating Dielectric CEr \| ([\d.]+)mil \| ([\d.]+)mil \| ([\d.]+)mil \| ([\d.]+)", txt)
    if not m:
        raise RuntimeError("Paramètres du masque introuvables")
    c1, c2, c3, cer = (float(x) for x in m.groups())
    m = re.search(r"Core dielectric constant \| Core dielectric constant \| ([\d.]+)", txt)
    if not m:
        raise RuntimeError("Dk du core introuvable")
    return dk, {"c1_mm": c1 * MIL, "c2_mm": c2 * MIL, "c3_mm": c3 * MIL, "er": cer}, float(m.group(1))


def parse_stackups(s: str):
    parts = re.split(r'<p class="my-10"[^>]*>\s*\d+\)\s*(JLC[0-9A-Z-]+) Stackup</p>', s)
    out = {}
    for i in range(1, len(parts), 2):
        name, body = parts[i], parts[i + 1].split("</li>")[0]
        spans = [htmlmod.unescape(x).strip() for x in re.findall(r"<span[^>]*>(.*?)</span>", body)]
        out.setdefault(name, [x for x in spans if x])
    if not out:
        raise RuntimeError("Aucun stackup trouvé : structure de page changée ?")
    return out


def mm(tok: str) -> float:
    m = re.fullmatch(r"([\d.]+)mm", tok)
    if not m:
        raise ValueError(f"épaisseur illisible : {tok}")
    return float(m.group(1))


def tokens_to_layers(tokens, dk, mask, core_dk):
    layers = [{"kind": "mask", "name": "F.Mask", "thickness": round(mask["c1_mm"], 5), "er": mask["er"],
               "mask_c2": round(mask["c2_mm"], 5), "material": "JLCPCB solder mask"}]
    notes = []
    i, n_cu, n_d = 0, 0, 0

    def copper(t):
        nonlocal n_cu
        n_cu += 1
        layers.append({"kind": "copper", "name": f"L{n_cu}", "thickness": t})

    def diel(t, er, typ, mat):
        nonlocal n_d
        n_d += 1
        layers.append({"kind": "dielectric", "name": f"{typ} {n_d}", "thickness": t, "er": er,
                       "dielectric_type": typ, "material": mat})

    while i < len(tokens):
        t = tokens[i]
        if t in ("Top Layer", "Bottom Layer"):
            assert tokens[i + 1] == "Copper"
            copper(mm(tokens[i + 2]))
            i += 3
        elif t == "Prepreg":
            style = tokens[i + 1].split("*")[0]
            if style in dk:
                er = dk[style]
            else:
                er = dk.get("3313", 4.1)
                notes.append(f"Dk du prepreg {style} absent du tableau JLCPCB : {er} (valeur 3313) utilisé")
            diel(mm(tokens[i + 2]), er, "prepreg", f"Prepreg {tokens[i + 1]}")
            i += 3
        elif t.startswith("Inner Layer"):
            # Inner Layer Lx | Core | Inner Layer Ly | Copper | Core | Copper | t | t_core | t | total
            cu1, core_t, cu2 = mm(tokens[i + 6]), mm(tokens[i + 7]), mm(tokens[i + 8])
            copper(cu1)
            diel(core_t, core_dk, "core", f"Core ({tokens[i + 9]})")
            copper(cu2)
            i += 10
        elif t == "Core" and i + 1 < len(tokens) and tokens[i + 1] == "Core":
            diel(mm(tokens[i + 2]), core_dk, "core", f"Core ({tokens[i + 3]})")
            i += 4
        else:
            raise ValueError(f"Jeton inattendu : {t!r} dans {tokens}")
    layers.append({"kind": "mask", "name": "B.Mask", "thickness": round(mask["c1_mm"], 5), "er": mask["er"],
                   "mask_c2": round(mask["c2_mm"], 5), "material": "JLCPCB solder mask"})
    return layers, list(dict.fromkeys(notes))


def two_layer_presets(mask, core_dk):
    """Stackups 2 couches (non publiés sur la page impédance : dérivés, à confirmer)."""
    out = {}
    for total in (0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0):
        cu = 0.035
        name = f"JLC-2L-{total:.1f}mm (non officiel)"
        out[name] = {
            "name": name, "source": "jlcpcb:" + name, "units": "mm", "official": False,
            "notes": ("Préréglage DÉRIVÉ, non publié par JLCPCB comme stackup d'impédance : core FR-4 "
                      f"Dk {core_dk} (valeur core de la page impédance), cuivre 1 oz, masque JLCPCB. "
                      "À confirmer avant fabrication."),
            "layers": [
                {"kind": "mask", "name": "F.Mask", "thickness": round(mask["c1_mm"], 5), "er": mask["er"],
                 "mask_c2": round(mask["c2_mm"], 5)},
                {"kind": "copper", "name": "L1", "thickness": cu},
                {"kind": "dielectric", "name": "core 1", "thickness": round(total - 2 * cu, 4), "er": core_dk,
                 "dielectric_type": "core", "material": "FR-4 core"},
                {"kind": "copper", "name": "L2", "thickness": cu},
                {"kind": "mask", "name": "B.Mask", "thickness": round(mask["c1_mm"], 5), "er": mask["er"],
                 "mask_c2": round(mask["c2_mm"], 5)},
            ]}
    return out


def main():
    s = fetch(sys.argv[1] if len(sys.argv) > 1 else None)
    txt = page_text(s)
    dk, mask, core_dk = parse_parameters(txt)
    raw = parse_stackups(s)
    presets = {}
    for name, toks in raw.items():
        layers, notes = tokens_to_layers(toks, dk, mask, core_dk)
        presets[name] = {"name": name, "source": "jlcpcb:" + name, "units": "mm", "official": True,
                         "notes": " ; ".join(notes), "layers": layers}
    presets.update(two_layer_presets(mask, core_dk))
    data = {
        "_source": URL,
        "_fetched": datetime.date.today().isoformat(),
        "_parameters": {"prepreg_dk": dk, "core_dk": core_dk, "solder_mask": mask},
        "presets": dict(sorted(presets.items(), key=lambda kv: (sum(l["kind"] == "copper" for l in kv[1]["layers"]),
                                                                  kv[0]))),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(presets)} stackups écrits dans {OUT}")


if __name__ == "__main__":
    main()
