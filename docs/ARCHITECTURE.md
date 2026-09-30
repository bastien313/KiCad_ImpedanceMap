# Architecture

## Flux de données

```
KiCad (API IPC)            fichier .kicad_pcb (+ .kicad_pro)
      │ kipy_reader.read_board         │ file_reader.read_kicad_pcb
      └──────────────┬─────────────────┘
                     ▼
              BoardModel (shapely, mètres, repère KiCad y↓)      Stackup (board / jlcpcb / JSON)
                     │                                                 │
   targets.build_targets ─► [Target]                                   │
                     │                                                 │
                     ▼                                                 ▼
   analysis.Engine.run ─► sampling.build_paths / sample_path ─► crosssection.build_cut ─► CrossSection
                     │                                                         │ quantized_key
                     │                     ResultCache (mémoire + disque) ◄────┤
                     │                                                         ▼
                     │                     ProcessPoolExecutor (spawn) ─► solver.solve_cross_section
                     ▼                                                         │
              AnalysisRun (JSON-sérialisable ; .sections en mémoire) ◄────────┘ LineResult
                     │
        │  signal.analyze_run (optionnel) ─► TargetReport.signal
        ┌────────────┼──────────────────────────┐
        ▼            ▼                          ▼
 overlay_plan   report.build_report      CLI / UI (controller)
                 (+ board_map.board_svg)
        │ (pur Python)
        ▼
 overlay_kicad.apply_overlay (kipy : 1 commit + 1 groupe + fichier d'IDs)
```

## Modules

| Module | Rôle | Dépend de KiCad ? |
|---|---|---|
| `solver/geometry.py` | `CrossSection`, `Conductor` (trapèze), `Slab`, `MaskSpec`, clé de cache | non |
| `solver/mesh.py` | grille non uniforme (`graded_axis`, `build_grid`), réglages `MeshSettings` | non |
| `solver/fdm.py` | assemblage volumes finis, conducteurs flottants, LU, matrice C | non |
| `solver/lines.py` | Z0/εeff, analyse modale (`LineResult`) | non |
| `solver/api.py` | niveaux de grille + Richardson (`solve_cross_section`, `SolveOptions`) | non |
| `solver/formulas.py` | Cohn, Hammerstad-Jensen, Kirschning-Jansen, Wadell | non |
| `solver/canonical.py` | coupes canoniques (tests, démonstrations) | non |
| `stackup/model.py` | `Stackup`, `StackupLayer`, JSON, disposition verticale | non |
| `stackup/sources.py` | préréglages JLCPCB, `from_kipy`, `from_kicad_pcb_tree`, `load_stackup` | kipy optionnel |
| `stackup/data/jlcpcb_stackups.json` | données JLCPCB (générées par `tools/fetch_jlcpcb_stackups.py`) | — |
| `extraction/board_model.py` | `BoardModel`, `Seg`, `ViaObj`, `PadObj`, `ZoneObj`, `LayerCopper.cut` | non |
| `extraction/sexpr.py`, `file_reader.py` | lecture .kicad_pcb / .kicad_pro | non |
| `extraction/kipy_reader.py` | lecture en direct (kipy), `wait_idle`, noms/enum de couches | **oui** |
| `extraction/targets.py` | cibles, paires (netclass puis suffixes), Z depuis le nom de netclass | non |
| `analysis/pair.py` | symétrie d'une paire : longueurs, skew, écart cumulé, part des extrémités découplées | non |
| `extraction/sampling.py` | chaînage, échantillonnage, zones de discontinuité, tronçons issus des zones du net (`zone_bridges`) | non |
| `extraction/crosssection.py` | coupe 2D, rôles, références, troncature | non |
| `analysis/engine.py` | orchestration, parallélisme, stats, vias de retour, résultats JSON | non |
| `analysis/cache.py` | cache mémoire/disque (`CACHE_VERSION`) | non |
| `analysis/signal.py` | intégrité du signal : cascade de primitives (T, C, L, stub), S11/S21, perte de désadaptation, TDR, préréglages, comparaison ligne seule | non |
| `analysis/discontinuities.py` | détection et paramètres des vias, stubs de via, coins, fentes, stubs de piste du chemin principal | non |
| `viz/overlay_plan.py` | tronçons/étiquettes/marqueurs (pur Python, testable) | non |
| `viz/overlay_kicad.py` | application kipy, effacement sélectif | **oui** |
| `viz/report.py` | rapport HTML (matplotlib Agg, data URI) | non |
| `viz/board_map.py` | carte interactive SVG + JS intégrés : cuivre final par couche, bandes à la largeur mesurée, ligne de coupe au survol, tronçons non calculés regroupés (échelles Ω / Ω libre / %, infobulles, zoom) | non |
| `synthesis.py` | calcul inverse (Brent sur log w / log s) | non |
| `ui/controller.py` | logique d'interface commune, réglages persistés | kipy en mode direct |
| `ui/wx_dialog.py`, `ui/tk_dialog.py` | dialogues | wx / tkinter |
| `cli.py`, `__main__.py` | ligne de commande | kipy pour `--live` |

## Conventions

* **Unités internes : mètres** partout sauf à la frontière kipy (nm, convertis dans `kipy_reader` et
  `overlay_kicad`) et dans le JSON de stackup (mm, champ `units`).
* **Repère board** : celui de KiCad (y vers le bas). **Repère coupe** : x le long de la coupe
  (centré sur le(s) signal(aux)), y vertical vers le haut, y = 0 sous B.Cu.
* **Noms de couches** : chaînes KiCad (`F.Cu`, `In1.Cu`, `User.1`) ; conversion vers l'enum kipy par
  `kipy_reader.layer_enum` / `layer_name` (`BL_` + remplacement `.`→`_`).
* **Statuts d'échantillon** : `ok`, `no_reference`, `discontinuity`, `error`. Seuls les `ok` entrent
  dans les statistiques et la coloration (`SampleResult.valid`).
* **Langue** : code (identifiants) en anglais, commentaires/docstrings/messages en français.
* Tout ce qui touche KiCad est importé **paresseusement** (dans les fonctions) pour que le reste
  fonctionne sans kipy.

## Points d'extension

* **Nouveau type de sortie** : consommer `AnalysisRun` (ou son JSON) — ne pas relancer le calcul.
* **Nouvelle règle de discontinuité** : `sampling.sample_path` (géométrie du tracé) ou
  `crosssection.build_cut` (contenu de la coupe) ; renseigner `status`/`reason`.
* **Autre solveur** (FEM/BEM, fréquentiel) : implémenter `solve_cross_section(xs, options) ->
  (LineResult, FieldSolution|None)` avec la même `CrossSection` ; incrémenter `CACHE_VERSION`.
* **Nouveau fabricant de stackups** : un JSON au format de `jlcpcb_stackups.json` + une fonction de
  chargement dans `stackup/sources.py` + une entrée dans l'UI.
* **Nouvelle couche d'overlay** : `OverlayOptions` + `plan_overlay` (classification) ; `resolve_layers`
  gère le repli si la couche est désactivée.

## Cycle de vie dans KiCad

1. KiCad lit `plugin.json`, crée le venv (`--system-site-packages`) et installe `requirements.txt`.
2. Clic sur le bouton → `impedance_map_action.py` lancé (pythonw sous Windows, cwd = dossier du
   plugin, `KICAD_API_SOCKET`/`KICAD_API_TOKEN` dans l'environnement).
3. `ui.run_plugin` : journalisation (fichier), connexion (timeout 20 s), lecture du board, dialogue.
4. Analyse dans un thread ; le calcul lourd dans un pool de processus `spawn` (le module principal
   est protégé par `if __name__ == "__main__"` + `freeze_support`).
5. Sorties : overlay (commit unique), rapport + JSON dans `<projet>/impedance_map/`.
