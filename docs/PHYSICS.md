# Modèle physique et méthode numérique

Ce document décrit **ce que calcule** le plugin et **comment**, avec les choix faits et leur
justification. Code correspondant entre crochets.

## 1. Modèle

Ligne de transmission quasi-TEM, conducteurs parfaits, milieu non magnétique (μ = μ0).
Dans la coupe 2D : ∇·(ε(x, y) ∇φ) = 0.

* Matrice de capacités de Maxwell **C** (F/m) des conducteurs actifs, diélectriques réels.
* **C0** : même géométrie, tous εr = 1 (masque compris).
* **L = μ0·ε0·C0⁻¹** (inductance externe, valable en TEM/quasi-TEM).

### Ligne simple [solver/lines.py]
Z0 = √(L/C) = 1 / (c·√(C·C0)) ; εeff = C/C0 ; délai = √εeff / c.

### Paire couplée — analyse modale [solver/lines.py]
M = L·C = T Λ T⁻¹ ; √M = T Λ^½ T⁻¹ ; matrice d'impédance caractéristique **Zc = (√M)⁻¹·L**
(V = Zc·I pour une onde progressive), symétrisée.

| Grandeur | Formule | Paire symétrique |
|---|---|---|
| Zdiff | Z11 + Z22 − 2 Z12 (I1 = −I2) | 2·Zodd |
| Zcomm | (Z11 + Z22 + 2 Z12)/4 (brins en parallèle) | Zeven/2 |
| Zodd, Zeven | Zdiff/2, 2·Zcomm | Z11 ∓ Z12 |
| Z0 de chaque brin | Z11, Z22 | |
| εeff impair/pair | c²·λ_i, mode identifié par le signe du vecteur propre | |
| k | (Zeven − Zodd)/(Zeven + Zodd) | |

Vérifié exactement contre les formules de Cohn (paire stripline, milieu homogène) et à < 0,6 %
contre Kirschning-Jansen (microstrip, milieu inhomogène).

## 2. Discrétisation [solver/fdm.py, solver/mesh.py]

* **Volumes finis à 5 points** sur grille rectilinéaire non uniforme ; potentiels aux nœuds,
  permittivité constante par cellule ; toutes les interfaces diélectriques et arêtes de conducteurs
  sont des lignes de grille. Conductance d'une arête = ε moyen pondéré des deux demi-cellules × longueur
  duale / longueur de l'arête.
* **Maillage** : points clés (arêtes, interfaces, bords du masque) avec taille locale ; taille
  autorisée h(x) = s_k + (r−1)|x − p_k| (= progression géométrique de raison r), plafonnée par couche
  (épaisseur / n_layer) ; nœuds placés en équirépartissant ∫dx/h. Taille fine = dimension
  caractéristique minimale (largeur, gap, épaisseur de diélectrique adjacente) / n_res.
* **Conducteurs** : SIGNAL (potentiel imposé), GROUND (0 V), FLOATING (tous les nœuds d'un corps
  fusionnés en une inconnue ; la ligne sommée impose **charge nette nulle** — loi de Gauss discrète).
  Épaisseur nulle autorisée (ligne de nœuds). Trapèzes : escalier sur la grille.
* **Résolution** : A = PᵀKP, B = PᵀKF, LU creuse (scipy.sparse.linalg.splu), une colonne par
  conducteur actif. **Charges** Q = Fᵀ K φ (égales à la formulation énergétique φᵢᵀKφⱼ) → C
  symétrisée.
* **Conditions aux limites** : haut/bas du domaine (loin dans l'air) Dirichlet 0 V ; bords latéraux
  Neumann homogène (symétrie miroir : un plan coupé au bord de la fenêtre est prolongé, ce qui est
  le comportement voulu pour un plan de référence).

### Convergence et extrapolation [solver/api.py]
Les singularités de champ aux arêtes donnent une convergence d'ordre ≈ 1 (rapport d'erreur mesuré
1,5–1,6 pour un raffinement ×1,5). On résout plusieurs niveaux emboîtés et on extrapole
(Richardson) **C et C0 élément par élément** avant de calculer L et Z :

| Mode | Niveaux | Rapport | Nœuds (typ.) | Précision mesurée |
|---|---|---|---|---|
| rapide | 2, 3 | 1,55 fixe | 3 k – 11 k | ≤ 0,25 % (Cohn) |
| précis | 4, 5, 6 | estimé (Aitken) sur la trace de C, borné [1,3 ; 2,5] | 35 k – 120 k | ≤ 0,08 % (Cohn) |

`error_estimate` = |Z extrapolé − Z grille fine| / Z : majorant prudent (≈ 4 à 12 × l’erreur réelle mesurée sur les cas analytiques).
Étude de convergence brute : `solver.api.convergence_study(xs)`.

## 3. Construction des coupes [extraction/crosssection.py]

1. **Échantillonnage** [extraction/sampling.py] : les segments/arcs d'un net sont chaînés (tolérance
   1 µm, à travers les couches) ; un point tous les `step` + points aux bords des zones de
   discontinuité. Direction de coupe = normale à la piste (paire : normale au brin P, centrée au milieu
   P–N).
2. **Fenêtre** : max(kw·w, kh·h_ref) (défauts 10 et 6 ; h_ref = diélectrique le plus mince vers un
   cuivre voisin), + écart P–N + w pour une paire.
3. **Intersection** de la ligne de coupe avec le cuivre de **toutes** les couches : union shapely par
   net et par couche (pistes tamponnées, pastilles de vias, formes de pads, **remplissages** de zones),
   découpée en corps connexes, index STRtree. Chaque intervalle devient un conducteur rectangulaire
   (ou trapèze, facteur de gravure) à la cote de sa couche.
4. **Rôles** : cible → SIGNAL ; autre net → GROUND ; sans net → FLOATING (un corps par îlot) ;
   même net que la cible : à moins de 2 w → **discontinuité**, sinon GROUND.
5. **Géométrie finale d'abord** (nets simples) : les pistes ne servent qu'à guider le trajet (où
   couper, dans quelle direction). La largeur du signal est celle du **cuivre final coupé** (union des
   pistes, pads et remplissages de zones du net sur la couche, `CutOptions.free_width`), et la fenêtre
   est dimensionnée sur cette largeur. Le point n'est calculé que si ce cuivre est **localement
   uniforme** : largeur mesurée à ± d (d = max(w, 1,5 h)/2) à moins de 25 % de celle du point
   (`uniform_width_tol`), sinon « pad / jonction » (pad du net à proximité), « jonction / changement
   de largeur » ou « fin du cuivre ». Un pad noyé dans une zone ou affleurant la ligne est donc
   calculé ; un pad qui dépasse est un modèle localisé (coupe fusionnée). Paires : largeur nominale et
   test ±25 % dans la coupe (inchangé).
   **Discontinuités géométriques** du tracé (pas de calcul, marqueur) : coin (> 5°, ± max(w, 1,5 h)),
   changement de couche (± max(w, 1,5 h, diamètre du via)), arc de rayon < 2 w, extrémités libres
   (± w/2, embout arrondi, avec un point au milieu pour le modèle localisé du pad terminal), via/pad
   traversant **du même net** coupé à moins de max(1,5 w, h), brin N absent/non parallèle (> 15°) ou
   trop éloigné (> 4 (w + gap)). Les vias des autres nets ne sont pas des discontinuités (§5).
   **Paires** : même règle, largeurs P et N mesurées, uniformité vérifiée sur les deux brins.
   **Broches traversantes** (connecteurs, THT) : traitées comme des fûts de via (modèle coaxial, antipad
   mesuré, stubs) — changement de couche par la broche, broche traversée sur place (stubs), broche terminale
   (fût en série jusqu'à la face du composant, ou stub si le composant est sur la face de la piste). Paire :
   fûts P et N à moins de 3 mm avec la même transition → élément symétrique ; sinon élément d'un seul brin,
   approximé par un demi-effet différentiel (série : (2 Z_via + Zdiff)/2 ; stub : 4 Z_via ; L : ×1).
   **Symétrie d'une paire** (`analysis/pair.py`) : Δt = ΔL √εeff,odd / c ; écart cumulé ΔL(s) = s_P − s_N
   (projection du point N en face) sur la partie couplée (distance P–N ≤ 1,5 × la distance typique) ;
   conversion de mode due au seul skew |Scd21| ≈ |sin(π f Δt)|.
   **Zones du même net servant de piste** (`sampling.zone_bridges`) : une extrémité libre de piste
   dans une zone allongée (rectangle minimal ≥ 1,5:1, entrée à moins de 30° du grand axe) est
   prolongée le long de l'axe jusqu'au bord de la zone ; deux extrémités libres dans la même zone sont
   reliées par un segment droit (s'il reste dans la zone).
6. **Référence** : un GROUND d'une autre couche couvrant [signal − w, signal + w] (recherche de la
   couche la plus proche vers le haut et vers le bas), sinon masses coplanaires des deux côtés à
   < 3 w. Sinon **perte de référence** : la valeur est calculée (boîte de calcul comme retour) mais
   exclue des statistiques et de la coloration.
7. **Diélectriques** : tranches du stackup ; niveaux cuivre internes remplis par la résine du prepreg
   adjacent (moyenne si deux prepregs, diélectrique voisin sinon) ; masque conforme : épaisseur c1
   sur substrat nu, c2 au-dessus et sur les flancs des pistes posées sur la surface.
8. **Troncature exacte** : un GROUND couvrant toute la largeur de la fenêtre est, avec les bords de
   Neumann, un écran parfait : tout ce qui est au-delà est retiré et le domaine s'arrête sur ce plan.
9. **Quantification** : abscisses arrondies à 1 µm, recentrées sur les signaux → clé SHA-1 de la
   géométrie (invariante par translation) → **cache** (mémoire + disque `cuts_v3.json`).

## 4. Intégrité du signal [analysis/signal.py]

À partir du chemin le plus long d'une cible : tronçons de longueur Δs (demi-distances entre
échantillons), Z (Z0 ou Zdiff) et εeff (εeff ou εeff impair) de chaque coupe ; échantillons non calculés
interpolés linéairement ; tronçons voisins identiques à 0,1 % fusionnés.

* Cascade de matrices ABCD de lignes **sans pertes** ; source et charge sur Zref (défaut : cible) :
  S11 = (A + B/Zr − C·Zr − D)/Δ, S21 = 2/Δ, Δ = A + B/Zr + C·Zr + D.
* Perte de désadaptation ML = −10·log10(1 − |S11|²) (= −|S21|² en dB, ligne sans pertes).
* TDR : S11(f) × filtre gaussien (échelon 10–90 % = tr ⇒ σ = tr/2,563, G = exp(−2π²σ²f²)), retard t0 = 4σ
  pour la causalité, transformée inverse (irfft) puis intégrale ⇒ ρ(t) ; Z_TDR = Zr(1+ρ)/(1−ρ) ;
  distance = t·v̄/2 (vitesse moyenne du tracé). Fenêtre 8 × retard + 40σ (pas de repliement).
* Métriques à f clé (Nyquist = débit/2 en numérique, fréquence d'intérêt en RF) : return loss, ML, S21,
  VSWR ; pire ML dans [0, f clé] ; |Γ| statique max ; Z vue min/max et réflexion crête (TDR).
* Préréglages : USB 2.0 HS tr = 500 ps (minimum de la norme) ; autres standards tr = 0,3 UI (hypothèse
  typique, modifiable).
* Vérifications : ligne adaptée, quart d'onde (|S11| = (Z1² − Z0²)/(Z1² + Z0²)), |S11|² + |S21|² = 1,
  plateau TDR = Z du tronçon, front lent masquant un tronçon court (tests/test_signal.py).
* Pourquoi pas seulement la « perte » : une désadaptation ne dissipe rien ; en numérique ce sont les
  réflexions (sonnerie, ISI) qui dégradent l'œil ⇒ réflexion crête et return loss jusqu'à Nyquist.

### Modèles localisés des discontinuités [analysis/discontinuities.py, analysis/signal.py]

Pourquoi : la coupe 2D suppose une ligne uniforme ; un pad, un via, un coin, une fente ou une branche sont
courts devant le front (USB 2.0 : ≈ 75 mm) et se comportent comme des éléments localisés (ΔC, ΔL, stub).
Ils sont insérés dans la cascade ABCD au point s où ils se trouvent sur le chemin principal (le plus long).

| Élément | Primitives | Détails |
|---|---|---|
| Pad | tronçon (Z_loc, εeff_loc, Δs) | coupe 2D `merge_same_net` : le cuivre du même net à moins de 2 w est au potentiel du signal ; ΔC rapporté = Σ (C′_pad − C′_piste)·Δs (paire : capacité de mode impair par brin). Désactivé à ± 2 w d'une jonction en T (la coupe longerait la branche : double comptage avec le stub). |
| Via | [stub d'entrée] + ligne (Z_via, εr, h) + [L_boucle] + [stub de sortie] | Z_via = 60/√εr·ln(D2/d), εr moyen des diélectriques traversés, D2 = 2 × distance au cuivre d'un autre net sur les couches traversées (défaut 3 × Ø pad si aucun plan) ; L_boucle = max(0, L_Johnson − L_coax) si aucun via d'un net de plan à moins de 1 mm. |
| Stub de via | ligne ouverte en dérivation | longueur = hauteur du fût au-delà des couches utilisées. |
| Stub de piste | ligne ouverte en dérivation (Zin = A/C de la cascade ouverte) | chemin du même net dont une extrémité est sur le chemin principal (< 5 µm) ; Z et εeff calculés sur la branche (paire : coupe simple brin). |
| Coin | C en dérivation | ΔC = C′ × (A_cuivre − A_droit)/w dans un disque de rayon min(3 w, 0,45 × segments) centré au sommet. |
| Fente | L en série | L = 0,2·D·ln(D/W) nH (D, W en mm, Johnson), D et W par lancer de rayons (± normale, ± tangente, 30 mm max) sur le plan non couvrant le plus proche ; « bord de plan » signalé si un rayon ne rencontre pas de cuivre. |

Paire : éléments construits par brin puis ramenés au mode différentiel (impédances × 2, admittances ÷ 2,
brins supposés identiques ; fente : × 2(1 − k)). Vérifications : tests/test_discontinuities.py.

## 5. Choix documentés

| Sujet | Choix | Raison |
|---|---|---|
| Voisins | 0 V | convention des solveurs 2D (lignes au repos) |
| Cuivre sans net | flottant, charge nulle | physique d'un îlot isolé ; ni masse ni ignoré |
| Même net loin | 0 V | autre portion de la ligne, quasi-statique |
| Arcs | calculés si R ≥ 2 w (coupe radiale), sinon discontinuité | l'effet de courbure décroît en (w/R)² ; au-delà de 2 w (bord intérieur ≥ 1,5 w du centre) la ligne est localement droite. L'ancien seuil (demi-fenêtre ≈ 5 w) excluait les arcs de routage ordinaires. La coupe signale toujours le cuivre du même net à moins de 2 w (virage en U) |
| Vias d'autres nets (clôture, couture) | fût ignoré, pastilles conservées, pas de discontinuité, avertissement | un fût 2D serait un mur infini ; mesuré sur une CPWG 50 Ω réelle : mur à la place du fût −5,9 % sur Z (borne haute), valeur 2D sans fût = convention des calculateurs CPWG |
| Mode précis trop gros | repli sur les niveaux de grille calculables | un `MemoryError` sur le niveau le plus fin laissait le point en erreur |
| Perte de référence | valeur non affichée | dépend de la boîte de calcul, sans sens physique |
| Base du trapèze | côté core (ou substrat pour les couches externes) | sens de gravure habituel |
| Zdiff paire asymétrique | matrice Zc | définition standard, exacte en milieu homogène |

## 6. Hors périmètre
Pertes (tan δ, résistance), dispersion, rugosité, effet de peau, rayonnement, effets 3D (coins, vias,
fentes vues « de biais », stubs). Les pertes et la dispersion nécessiteraient un solveur
fréquentiel (voir la section « Pistes d'évolution » du README).
