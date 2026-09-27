"""Génère docs/VALIDATION.md à partir de tests/_validation_results.json (écrit par pytest).

    python -m pytest tests/test_solver_validation.py && python tools/validation_table.py
"""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    data = json.loads((ROOT / "tests" / "_validation_results.json").read_text(encoding="utf-8"))
    lines = ["| Cas | Référence | Rapide : Z (Ω) | écart | Précis : Z (Ω) | écart | Seuil |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for case, modes in data.items():
        ref = next(iter(modes.values()))["reference"]
        f = modes.get("fast", {})
        p = modes.get("precise", {})
        seuil = "1 %" if "Cohn" in case else "3 %"
        def cell(m):
            return (f"{m['solveur']:.3f}", f"{m['ecart_pct']:+.2f} %") if m else ("—", "—")
        lines.append(f"| {case} | {ref:.3f} | {cell(f)[0]} | {cell(f)[1]} | {cell(p)[0]} | {cell(p)[1]} | {seuil} |")
    worst_f = max(abs(m["ecart_pct"]) for v in data.values() for k, m in v.items() if k == "fast")
    worst_p = max(abs(m["ecart_pct"]) for v in data.values() for k, m in v.items() if k == "precise")
    txt = (ROOT / "docs" / "VALIDATION.template.md").read_text(encoding="utf-8")
    txt = txt.replace("{{TABLE}}", "\n".join(lines)).replace("{{WORST_FAST}}", f"{worst_f:.2f}") \
             .replace("{{WORST_PRECISE}}", f"{worst_p:.2f}").replace("{{N}}", str(len(data)))
    (ROOT / "docs" / "VALIDATION.md").write_text(txt, encoding="utf-8")
    print("docs/VALIDATION.md écrit :", len(data), "cas ; pire écart rapide", worst_f, "%, précis", worst_p, "%")


if __name__ == "__main__":
    main()
