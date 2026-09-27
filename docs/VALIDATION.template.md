# Validation du solveur

> Fichier **généré** par `python tools/validation_table.py` à partir de
> `tests/_validation_results.json`, lui-même écrit par `pytest tests/test_solver_validation.py`.
> Ne pas éditer à la main : relancer les deux commandes après toute modification du solveur.

## Résultats mesurés ({{N}} cas)

Écart = (Z solveur / Z référence − 1). Pire écart : **{{WORST_FAST}} %** en mode rapide,
**{{WORST_PRECISE}} %** en mode précis.

{{TABLE}}

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
