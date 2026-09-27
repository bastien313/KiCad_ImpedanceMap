# Validation du solveur

> Fichier **généré** par `python tools/validation_table.py` à partir de
> `tests/_validation_results.json`, lui-même écrit par `pytest tests/test_solver_validation.py`.
> Ne pas éditer à la main : relancer les deux commandes après toute modification du solveur.

## Résultats mesurés (25 cas)

Écart = (Z solveur / Z référence − 1). Pire écart : **0.93 %** en mode rapide,
**1.01 %** en mode précis.

| Cas | Référence | Rapide : Z (Ω) | écart | Précis : Z (Ω) | écart | Seuil |
|---|---:|---:|---:|---:|---:|---:|
| Stripline Cohn w=0.2 b=0.5 εr=4.4 | 53.798 | 53.935 | +0.25 % | 53.760 | -0.07 % | 1 % |
| Stripline Cohn w=0.1 b=0.8 εr=4.0 | 90.458 | 90.642 | +0.20 % | 90.435 | -0.03 % | 1 % |
| Stripline Cohn w=0.5 b=0.4 εr=3.5 | 29.767 | 29.734 | -0.11 % | 29.757 | -0.03 % | 1 % |
| Stripline Cohn w=0.15 b=0.3 εr=4.6 | 46.827 | 46.868 | +0.09 % | 46.817 | -0.02 % | 1 % |
| Paire stripline Cohn Zodd w=0.2 s=0.2 b=0.6 | 50.252 | 50.287 | +0.07 % | 50.214 | -0.07 % | 1 % |
| Paire stripline Cohn Zeven w=0.2 s=0.2 b=0.6 | 66.585 | 66.616 | +0.05 % | 66.553 | -0.05 % | 1 % |
| Paire stripline Cohn Zodd w=0.1 s=0.15 b=0.4 | 62.113 | 62.210 | +0.16 % | 62.087 | -0.04 % | 1 % |
| Paire stripline Cohn Zeven w=0.1 s=0.15 b=0.4 | 80.978 | 81.065 | +0.11 % | 80.955 | -0.03 % | 1 % |
| Microstrip H-J w=0.3 h=0.2 εr=4.4 | 57.562 | 57.570 | +0.01 % | 57.483 | -0.14 % | 3 % |
| Microstrip H-J w=2.9 h=1.53 εr=4.6 | 49.294 | 49.281 | -0.03 % | 49.209 | -0.17 % | 3 % |
| Microstrip H-J w=0.1 h=0.1 εr=4.1 | 73.261 | 73.314 | +0.07 % | 73.142 | -0.16 % | 3 % |
| Microstrip H-J w=0.2 h=0.0764 εr=3.91 | 43.307 | 43.268 | -0.09 % | 43.198 | -0.25 % | 3 % |
| Microstrip épaisse H-J w=0.3 h=0.2 t=0.035 | 54.532 | 54.608 | +0.14 % | — | — | 3 % |
| Microstrip épaisse H-J w=0.35 h=0.2104 t=0.035 | 51.711 | 51.793 | +0.16 % | — | — | 3 % |
| Paire microstrip K-J Zodd w=0.3 s=0.2 h=0.2 εr=4.4 | 49.753 | 49.853 | +0.20 % | 49.776 | +0.04 % | 3 % |
| Paire microstrip K-J Zeven w=0.3 s=0.2 h=0.2 εr=4.4 | 64.773 | 64.518 | -0.39 % | 64.443 | -0.51 % | 3 % |
| Paire microstrip K-J Zodd w=0.15 s=0.15 h=0.1 εr=4.1 | 54.047 | 54.149 | +0.19 % | 54.052 | +0.01 % | 3 % |
| Paire microstrip K-J Zeven w=0.15 s=0.15 h=0.1 εr=4.1 | 64.523 | 64.321 | -0.31 % | 64.239 | -0.44 % | 3 % |
| Paire microstrip K-J Zodd w=1.0 s=0.5 h=0.5 εr=10.0 | 29.371 | 29.406 | +0.12 % | 29.370 | -0.01 % | 3 % |
| Paire microstrip K-J Zeven w=1.0 s=0.5 h=0.5 εr=10.0 | 36.735 | 36.606 | -0.35 % | 36.574 | -0.44 % | 3 % |
| Paire microstrip K-J Zodd w=0.2 s=1.0 h=0.2 εr=4.4 | 69.976 | 70.034 | +0.08 % | 69.881 | -0.14 % | 3 % |
| Paire microstrip K-J Zeven w=0.2 s=1.0 h=0.2 εr=4.4 | 71.999 | 72.058 | +0.08 % | 71.911 | -0.12 % | 3 % |
| Coplanaire+plan Wadell w=0.3 g=0.15 h=0.2 | 53.656 | 53.370 | -0.53 % | 53.326 | -0.61 % | 3 % |
| Coplanaire+plan Wadell w=0.5 g=0.2 h=1.5 | 66.189 | 66.306 | +0.18 % | 66.116 | -0.11 % | 3 % |
| Coplanaire+plan Wadell w=0.2 g=0.1 h=0.1 | 48.517 | 48.068 | -0.93 % | 48.029 | -1.01 % | 3 % |

## Références

| Référence | Nature | Domaine utilisé |
|---|---|---|
| Cohn (1954), stripline centrée t = 0 | **exacte** (transformation conforme, intégrales elliptiques) | w/b de 0,12 à 1,25 |
| Cohn (1955), paire stripline couplée t = 0 | **exacte** | s/b 0,33–0,375 |
| Hammerstad-Jensen (1980), microstrip | approchée ≈ 0,2 % (t = 0), correction d'épaisseur H&J | w/h 0,5–2,6 ; εr 3,9–4,6 |
| Kirschning-Jansen (1984), paire microstrip, statique | approchée ≈ 1 % | u 0,75–2 ; g 1–5 ; εr 4,1–10 |
| Wadell (1991) §3.4, coplanaire avec plan (CBCPW) | conforme approchée (masses latérales infinies, t = 0) | w/h 0,33–2 |

Lecture des écarts :
* sur les références **exactes** (Cohn), le mode précis est à < 0,1 % : c'est la mesure de la
  précision propre du solveur ;
* le mode précis n'est pas « meilleur » que le rapide face à Hammerstad-Jensen / Kirschning-Jansen /
  Wadell : l'écart restant (0,1–1 %) est dominé par l'approximation des formules elles-mêmes et
  par la troncature du domaine, pas par la grille ;
* Wadell (CBCPW) sous-estime légèrement le couplage aux masses coplanaires finies : l'écart
  de −1 % pour w = 0,2, g = 0,1, h = 0,1 mm est cohérent avec la précision annoncée de la formule.

## Tests de cohérence physique (tests/test_solver_physics.py)

| Test | Vérifie |
|---|---|
| `test_grid_convergence_monotonic` | erreur décroissante, ordre ≈ 1 (rapport 1,3–2,2 par raffinement ×1,5) |
| `test_precise_better_than_fast_error_estimate` | l'estimation d'erreur baisse en mode précis et majore l'erreur réelle |
| `test_window_width_sensitivity` | fenêtre ×2 : < 0,3 % ; fenêtre ÷2 : < 1 % |
| `test_window_minimum_documented` | fenêtre du cahier des charges max(10 w, 6 h) : < 1 % d'une fenêtre de 60 w |
| `test_air_height_sensitivity` | air de 10 h à 40 h : < 0,5 % |
| `test_floating_plane_series_capacitance` | plan flottant = capacité série exacte (charge nette nulle) à < 1 % |
| `test_floating_neighbor_between_ground_and_absent` | Z(voisine à 0 V) < Z(voisine flottante) ≤ Z(seule) |
| `test_solder_mask_lowers_impedance` | masque JLCPCB : Z baisse de 0,5–8 %, εeff augmente |
| `test_trapezoid_between_bounds` | trapèze entre rectangle large et rectangle étroit |
| `test_asymmetric_pair_consistency` | paire asymétrique : Z11 ≠ Z22, 0 < k < 1 |
| `test_cache_key_translation_invariant` | clé de cache invariante par translation, sensible à w |

## Performance (tools/benchmark.py)

Voir la section Performance du README (mesures réelles, machine indiquée).
