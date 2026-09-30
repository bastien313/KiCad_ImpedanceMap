# Journal des modifications

## 0.3.1 — 2026-09-30
Correctifs issus d'un board réel (ligne coplanaire 50 Ω sur B.Cu routée en arcs, rétrécie aux pads,
bordée de vias GND et prolongée par des zones du net) : 4 % de la longueur calculée → 97 %.
* **Vias d'autres nets** (clôture de vias, couture) : plus des discontinuités ; pastilles prises en
  compte, fût ignoré (convention CPWG), avertissement par cible. Les vias du même net restent des
  discontinuités.
* **Arcs** : discontinuité si R < 2 × largeur de piste (`arc_min_radius_factor`, défaut 2,0), au lieu de
  R < demi-fenêtre qui excluait les arcs de routage ordinaires.
* **Géométrie finale d'abord** (nets simples) : la largeur du signal est mesurée sur le cuivre final
  (pistes + pads + zones du net) et non plus prise sur la piste ; un point est calculé si ce cuivre est
  localement uniforme (± 25 % à ± max(w, 1,5 h)/2, `uniform_width_tol`). Les pistes ne guident plus
  que le trajet. Conséquences : piste noyée dans une zone = largeur de la zone ; pad noyé dans une zone
  ou affleurant la ligne = calculé ; tronçons rétrécis calculés hors des jonctions.
* **Zones de cuivre du même net** servant de piste : chaînées au tracé (`sampling.zone_bridges`).
* Extrémités : marge ± w/2 (embout arrondi, au lieu de ± w) et un point dans chaque zone d'extrémité,
  pour que le pad terminal soit toujours modélisé (avant : selon le hasard de l'échantillonnage).
* **Carte du rapport** : cuivre final de chaque couche (cases à cocher), bandes à la largeur mesurée
  dans chaque coupe, ligne de coupe au survol, pads du net en pointillés, perçages ; un seul repère
  par tronçon non calculé (au lieu d'une croix par point). Échantillons : champs `half` et `wn`.
* Solveur : en mode précis, un maillage trop grand (`MemoryError`) sur le niveau le plus fin ne met
  plus le point en erreur ; repli sur les niveaux calculables.
* **Intégrité du signal** : zones de qualité (return loss 20 / 15 / 10 dB) sur les courbes |S11| et perte
  de désadaptation (désormais en échelle log) ; **contribution de chaque tronçon** (`signal.section_contributions`) :
  perte « seul » et gain « si corrigé » à f clé, figure (barres le long du tracé + courbes en fréquence) et
  tableau dans le rapport ; `quality_at_fkey`. Aide du rapport : repères usuels et ordre de grandeur des
  pertes dissipatives (non calculées).
* **Paires différentielles** :
  * même règle « géométrie finale d'abord » que les nets simples (largeurs P et N mesurées, uniformité
    vérifiée sur les deux brins, pads sur le trajet → modèle localisé) ;
  * **symétrie** (`analysis/pair.py`) : longueurs P / N, écart et skew (ps), écart cumulé le long du tracé
    (où le déséquilibre se crée), part créée aux extrémités découplées, conversion de mode due au skew
    (|sin(π f Δt)|), skew rapporté au temps de montée et à l'UI ; section « Symétrie de la paire » du rapport
    avec aide « ce qui compte sur une paire » ; colonne « Skew P/N » ;
  * carte : le brin N est tracé à sa **position réelle** (point en face relevé par le moteur, champs
    `nx`, `ny`), et non plus par symétrie du brin P (formes aberrantes dans les virages et au connecteur).
* **Broches traversantes** (connecteurs, composants THT) traitées comme des fûts : changement de couche par
  une broche, broche traversée sans changement de couche (stubs), broche en bout de ligne (fût en série
  vers le composant s'il est sur l'autre face, sinon stub). Côté et référence du composant lus (`PadObj.side`,
  `ref` = « J8.A6 »). Pour une paire, fûts P et N appariés (symétriques) ou asymétriques (demi-effet
  différentiel, signalé). Un seul élément par fût (segments multiples au centre d'une broche).
* TDR : avertissement quand le front est plus long que l'aller-retour du tracé (la TDR ne localise pas) ;
  courbe de perte sans faux plateau à basse fréquence.
* `CACHE_VERSION` = 4. Tests : `tests/test_zones_arcs_vias.py` (11 tests). Boards d'exemple : valeurs
  d'impédance et réflexions inchangées (≤ 0,06 Ω sur les moyennes, ≤ 0,04 point sur les réflexions).

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
