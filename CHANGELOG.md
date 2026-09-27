# Journal des modifications

## 0.3.0 — 2026-09-25
* **Discontinuités dans l'intégrité du signal** (analysis/discontinuities.py) : pads sur le trajet (coupe 2D
  avec cuivre du même net fusionné), vias (fût coaxial, antipad mesuré, inductance de boucle sans via de
  retour), stubs de via, stubs de piste (branches en T), coins (ΔC), fentes (L = 0,2·D·ln(D/W) nH, D et W
  mesurés). Cascade de primitives (ligne, C, L, stub ouvert). Rapport : liste des éléments, TDR « ligne seule »
  superposée, comparaison des réflexions ; CLI : détail des éléments.
* Board d'exemple `examples/demo_discontinuites.kicad_pcb` (générateur `examples/make_demo_board.py`, recréé)
  et son rapport ; `examples/stackup_perso_exemple.json` recréé.
* Effacement de l'overlay : le fichier d'identifiants est toujours vidé (plus d'entrée périmée).

## 0.2.0 — 2026-09-25
* Rapport : carte du board **interactive** (SVG + JS intégrés) — échelle en Ω centrée sur la cible
  (par groupe de cibles), en Ω libre (min/max) ou en écart % ; infobulles, zoom, déplacement.
  Remplace la carte PNG graduée en « ±tol ».
* **Intégrité du signal** (analysis/signal.py) : cascade ABCD du profil Z(s) réel, |S11|, perte de
  désadaptation, S21, VSWR, TDR simulée et réflexion crête ; préréglages (USB 2.0 HS, USB 3.x, PCIe,
  HDMI, MIPI, Ethernet, numérique perso, RF) ; section dédiée dans le rapport, le dialogue (wx et tk)
  et la CLI (`--signal`, `--bitrate`, `--rise`, `--freq`, `--zref`, `--no-signal`).
* Avertissement quand une zone sans référence est interpolée dans le calcul de réflexion.

## 0.1.1 — 2026-09-25
* Correctif vérifié en direct : KiCad 10.0.6 abandonne silencieusement un groupe créé dans le même
  commit que ses membres → objets dans un commit, groupe dans un second commit, puis relecture.
* La CLI `--overlay` enregistre aussi le fichier d'identifiants de secours.
* Dépannage : serveur API absent en éditeur PCB autonome ; interpréteur Python des plugins.

## 0.1.0 — 2026-09-25
Première version.
* Solveur 2D quasi-statique : volumes finis sur grille non uniforme, conducteurs flottants,
  extrapolation de Richardson (modes rapide / précis), analyse modale des paires.
* Validation contre Cohn (stripline et paire), Hammerstad-Jensen, Kirschning-Jansen, Wadell ;
  tests de convergence et de sensibilité au domaine.
* Stackups : board (API ou fichier), 32 préréglages JLCPCB officiels + 7 deux-couches dérivés,
  JSON perso (éditeur dans le dialogue).
* Extraction en direct (kipy) et hors ligne (.kicad_pcb), paires par netclass puis suffixes,
  échantillonnage avec discontinuités, coupes avec références, masque conforme, troncature exacte.
* Moteur parallèle avec cache quantifié (mémoire + disque), annulation, statistiques.
* Overlay (3 couches User + annotations, 1 commit, groupe, fichier d'identifiants de secours),
  effacement sélectif ; rapport HTML autonome.
* Interface wx + repli tkinter, calcul inverse, détection des changements de couche sans via de
  retour, CLI, script d'installation, board d'exemple.
