"""Effet de l'impédance réelle sur le signal : paramètres S, perte de désadaptation, TDR simulée.

Modèle : la piste (ou la paire, en mode différentiel) est découpée en tronçons de ligne
SANS PERTES de longueur Δs, d'impédance Z(s) et de permittivité effective εeff(s) calculées
par l'analyse 2D ; les tronçons sont cascadés (matrices ABCD), la ligne étant attaquée et
terminée sur l'impédance de référence Zref (par défaut la cible : 50 Ω, 90 Ω…).

    ABCD d'un tronçon : [[cos θ, j Z sin θ], [j sin θ / Z, cos θ]],  θ = 2π f √εeff Δs / c
    S11 = (A + B/Zr − C Zr − D) / Δ,  S21 = 2 / Δ,  Δ = A + B/Zr + C Zr + D

Grandeurs fournies :
  * |S11| en dB (return loss = −|S11|dB) et perte de désadaptation ML = −10 log10(1 − |S11|²)
    (ligne sans pertes : |S21|² = 1 − |S11|² ; ML est la seule « perte » due à l'impédance) ;
  * TDR simulée : réponse réfléchie à un échelon de temps de montée tr (10–90 %, gaussien),
    convertie en impédance vue Z_TDR = Zr (1 + ρ)/(1 − ρ) et en distance (vitesse moyenne) ;
  * coefficient de réflexion crête |ρ| (en %) vu par ce front.

Repères de qualité (`QUALITY_BANDS`, usage RF courant) : return loss ≥ 20 dB excellent (≥ 99 % de la
puissance transmise), 15–20 dB bon, 10–15 dB acceptable (10 dB = 90 %, critère classique d'adaptation
d'antenne), < 10 dB mauvais ; équivalents en perte de désadaptation : 0,044 / 0,14 / 0,46 dB.

Contribution de chaque tronçon (`section_contributions`) : le profil est découpé en tronçons homogènes
(dans la tolérance / trop haut / trop bas / pad / interpolé) et chaque élément localisé (via, coin,
fente, stub) est un tronçon à part. Pour chacun : perte qu'il produirait SEUL (le reste de la ligne à
Zref, sans les autres éléments) et gain si on le CORRIGEAIT (tronçon ramené à Zref, élément supprimé).
Les réflexions interfèrent : les contributions ne s'additionnent pas et un gain peut être négatif (deux
défauts qui se compensent).

Limites : pertes conducteur/diélectrique non modélisées ; pads, vias, connecteurs et zones de
discontinuité non modélisés (dans ces zones Z et εeff sont interpolés entre les points calculés) ;
extrémités supposées adaptées (Zref).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Dict, Optional, Tuple

import numpy as np

C_LIGHT = 299_792_458.0

# (return loss minimal en dB, libellé, couleur) — du meilleur au moins bon ; en dessous du dernier : mauvais
QUALITY_BANDS = [(20.0, "excellent", "#cfe8d4"), (15.0, "bon", "#e6f2d9"), (10.0, "acceptable", "#fbecc8")]
QUALITY_BAD = ("mauvais", "#f6d2d0")


def rl_to_ml(rl_db: float) -> float:
    """Perte de désadaptation (dB) correspondant à un return loss (dB)."""
    g2 = 10 ** (-rl_db / 10)
    return -10 * math.log10(1 - g2)


def quality(rl_db: float) -> str:
    for lim, name, _ in QUALITY_BANDS:
        if rl_db >= lim:
            return name
    return QUALITY_BAD[0]


@dataclass
class SignalSpec:
    """Signal de référence pour l'évaluation.

    kind     : "digital" (débit + temps de montée) ou "rf" (fréquence d'intérêt).
    bitrate  : b/s (numérique) ; fréquence de Nyquist = bitrate / 2.
    rise     : temps de montée 10–90 % (s) pour la TDR (numérique).
    freq     : fréquence d'intérêt (Hz) en mode RF.
    zref     : impédance de référence (Ω) ; None = impédance cible de chaque piste/paire.
    """
    name: str = "USB 2.0 High-Speed"
    kind: str = "digital"
    bitrate: float = 480e6
    rise: float = 500e-12
    freq: float = 1e9
    zref: Optional[float] = None

    @property
    def f_key(self) -> float:
        return self.bitrate / 2 if self.kind == "digital" else self.freq

    def to_dict(self):
        return asdict(self)


# Débits normalisés ; temps de montée : USB 2.0 HS = 500 ps (minimum de la norme, 10–90 %) ;
# pour les autres, hypothèse 0,3 UI (valeur typique, modifiable par l'utilisateur).
PRESETS: Dict[str, SignalSpec] = {
    "USB 2.0 High-Speed (480 Mb/s)": SignalSpec("USB 2.0 High-Speed", "digital", 480e6, 500e-12),
    "USB 3.x Gen 1 (5 Gb/s)": SignalSpec("USB 3.x Gen 1", "digital", 5e9, 0.3 / 5e9),
    "USB 3.x Gen 2 (10 Gb/s)": SignalSpec("USB 3.x Gen 2", "digital", 10e9, 0.3 / 10e9),
    "PCIe Gen 3 (8 GT/s)": SignalSpec("PCIe Gen 3", "digital", 8e9, 0.3 / 8e9),
    "HDMI 1.4 (3,4 Gb/s par voie)": SignalSpec("HDMI 1.4", "digital", 3.4e9, 0.3 / 3.4e9),
    "Ethernet 100BASE-TX (125 MBd)": SignalSpec("100BASE-TX", "digital", 125e6, 0.3 / 125e6),
    "MIPI D-PHY (1,5 Gb/s)": SignalSpec("MIPI D-PHY", "digital", 1.5e9, 0.3 / 1.5e9),
    "Numérique personnalisé": SignalSpec("Numérique personnalisé", "digital", 1e9, 100e-12),
    "RF / analogique (fréquence)": SignalSpec("RF", "rf", 0.0, 0.0, 1e9),
}


# ---------------------------------------------------------------------------- profils
def _path_samples(samples, net: str, path_id: int):
    v = [x for x in samples if x.net == net and x.path_id == path_id]
    v.sort(key=lambda x: x.s)
    return v


def _edges(s: np.ndarray) -> np.ndarray:
    """Bornes des tronçons représentés par chaque échantillon (échantillons sur ]0, L[ avec
    demi-pas aux extrémités : L ≈ s_dernier + s_premier)."""
    return np.concatenate([[0.0], 0.5 * (s[1:] + s[:-1]), [s[-1] + s[0]]])


def _values(path, kind: str, use_si: str):
    """(z, εeff, C′ par brin, source) par échantillon ; source : ok / modèle / nan."""
    n = len(path)
    z, e, c = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    src = ["nan"] * n
    for i, x in enumerate(path):
        if x.valid and x.result is not None:
            r = x.result
            C = np.atleast_2d(np.array(r["C"])) if r.get("C") is not None else None
            if kind == "pair" and use_si == "main":
                z[i], e[i] = r["zdiff"], r["eps_eff_odd"]
                c[i] = C[0, 0] - C[0, 1] if C is not None and C.shape[0] > 1 else np.nan
            elif kind == "pair":       # brin de stub d'une paire : valeur simple brin via si seulement
                pass
            else:
                z[i], e[i] = r["z0"], r["eps_eff"]
                c[i] = C[0, 0] if C is not None else np.nan
            if np.isfinite(z[i]):
                src[i] = "ok"
        si = getattr(x, "si", None)
        if src[i] == "nan" and si is not None and si.get("mode") == use_si:
            z[i], e[i], c[i] = si["z"], si["e"], si["c"]
            src[i] = "modèle"
    return z, e, c, src


def _fill(s, arr):
    ok = np.isfinite(arr)
    if not ok.any():
        return None
    return np.interp(s, s[ok], arr[ok])


def line_profile(samples, kind: str, main_path: Optional[int] = None):
    """Profil du chemin principal : dict(s, ds, z, e, c, src, net, path_id) ou None.

    Valeurs : coupe 2D normale si calculée, sinon modèle localisé (pad fusionné), sinon
    interpolation linéaire entre points calculés.
    """
    by: Dict[Tuple[str, int], list] = {}
    for sr in samples:
        by.setdefault((sr.net, sr.path_id), []).append(sr)
    if not by:
        return None
    if main_path is not None and any(k[1] == main_path for k in by):
        key = next(k for k in by if k[1] == main_path)
    else:
        key = max(by, key=lambda k: (max(x.s for x in by[k]) - min(x.s for x in by[k]), len(by[k])))
    path = _path_samples(samples, *key)
    s = np.array([x.s for x in path])
    z, e, c, src = _values(path, kind, "main")
    zf, ef, cf = _fill(s, z), _fill(s, e), _fill(s, c)
    if zf is None:
        return None
    if cf is None:
        cf = np.full(len(s), np.nan)
    ds = np.maximum(np.diff(_edges(s)), 0.0)
    return {"s": s, "ds": ds, "z": zf, "e": ef, "c": cf, "src": src, "net": key[0], "path_id": key[1],
            "status": [getattr(x, "status", "ok") for x in path]}


# ---------------------------------------------------------------------------- cascade
# Primitives (sérialisables) :
#   ("T", Z, εeff, longueur)   tronçon de ligne en série
#   ("C", C)                   capacité en dérivation
#   ("L", L)                   inductance en série
#   ("S", [(Z, εeff, l), ...]) stub ouvert en dérivation (tronçons depuis la jonction)
def _abcd_line(Z, E, L, w):
    th = w * math.sqrt(E) * L / C_LIGHT
    c, s = np.cos(th), np.sin(th)
    return c, 1j * Z * s, 1j * s / Z, c


def _mul(M, N):
    A, B, C, D = M
    a, b, c, d = N
    return A * a + B * c, A * b + B * d, C * a + D * c, C * b + D * d


def cascade(prims, zref: float, f: np.ndarray):
    """S11, S21 complexes de la cascade de primitives, source et charge sur zref."""
    f = np.asarray(f, dtype=float)
    w = 2 * np.pi * f
    one, zero = np.ones_like(f, dtype=complex), np.zeros_like(f, dtype=complex)
    M = (one, zero, zero, one)
    for p in prims:
        k = p[0]
        if k == "T":
            N = _abcd_line(p[1], p[2], p[3], w)
        elif k == "C":
            N = (one, zero, 1j * w * p[1], one)
        elif k == "L":
            N = (one, 1j * w * p[1], zero, one)
        elif k == "S":
            S = (one, zero, zero, one)
            for Z, E, L in p[1]:
                S = _mul(S, _abcd_line(Z, E, L, w))
            A, _, C, _ = S
            Y = C / np.where(np.abs(A) < 1e-12, 1e-12, A)         # Zin = A/C pour une extrémité ouverte
            N = (one, zero, Y, one)
        else:
            raise ValueError(f"primitive inconnue {k}")
        M = _mul(M, N)
    A, B, C, D = M
    den = A + B / zref + C * zref + D
    return (A + B / zref - C * zref - D) / den, 2.0 / den


def sparams(ds, z, e, zref: float, f: np.ndarray):
    """S11, S21 d'une cascade de lignes sans pertes (tronçons ds, z, e)."""
    return cascade([("T", Z, E, L) for L, Z, E in zip(ds, z, e)], zref, f)


def _tdr_prims(prims, zref: float, rise: float, length: float):
    sigma = rise / 2.563                                  # 10–90 % d'un échelon gaussien = 2,563 σ
    delay = sum(p[3] * math.sqrt(p[2]) for p in prims if p[0] == "T") / C_LIGHT
    T = 8 * delay + 40 * sigma                            # fenêtre (évite le repliement)
    fmax = 3.0 / sigma
    df = 1.0 / T
    n = int(math.ceil(fmax / df)) + 1
    f = np.arange(n) * df
    s11, _ = cascade(prims, zref, f)
    t0 = 4 * sigma
    G = np.exp(-2 * np.pi ** 2 * sigma ** 2 * f ** 2) * np.exp(-2j * np.pi * f * t0)
    h = np.fft.irfft(s11 * G, n=2 * (n - 1))
    rho = np.cumsum(h)
    dt = 1.0 / (2 * (n - 1) * df)
    t = np.arange(len(rho)) * dt - t0
    keep = (t >= 0) & (t <= 2 * delay + 6 * sigma)
    t, rho = t[keep], rho[keep]
    v = length / delay if delay > 0 else C_LIGHT
    x = t * v / 2.0
    rho_c = np.clip(rho, -0.999, 0.999)
    return x, zref * (1 + rho_c) / (1 - rho_c), rho


def tdr(ds, z, e, zref: float, rise: float):
    """TDR simulée d'une cascade de lignes : (distance m, Z vue Ω, ρ(t))."""
    return _tdr_prims([("T", Z, E, L) for L, Z, E in zip(ds, z, e)], zref, rise, float(np.sum(ds)))


# ---------------------------------------------------------------------------- modèle complet
def _nearest(prof, s_e):
    return int(np.argmin(np.abs(prof["s"] - s_e)))


def _element_prims(el, prof, tr, kind):
    """Primitives et résumé lisible d'un élément localisé (déjà ramené au mode de la cible)."""
    k = 2.0 if kind == "pair" else 1.0          # mode différentiel : impédances ×2 (brins supposés identiques)
    t = el["type"]
    i = _nearest(prof, el["s"])
    if t == "via":
        zv, er, h = el["z_via"] * k, el["eps"], el["h_used"]
        asym = kind == "pair" and el.get("strand") in ("P", "N")
        z_series, z_stub, kL = zv, zv, k
        if asym:
            # un seul brin traverse le fût : demi-effet différentiel (l'autre brin reste une ligne de Z
            # locale) ; la conversion vers le mode commun n'est pas calculée
            z_series = 0.5 * (zv + float(prof["z"][i]))
            z_stub, kL = 2.0 * zv, 1.0
        prims = []
        if el["entry_stub"] > 1e-6:
            prims.append(("S", [(z_stub, er, el["entry_stub"])]))
        if h > 0:
            prims.append(("T", z_series, er, h))
        if el["extra_L"] > 0:
            prims.append(("L", el["extra_L"] * kL))
        if el["exit_stub"] > 1e-6:
            prims.append(("S", [(z_stub, er, el["exit_stub"])]))
        stubs = ", ".join(f"{l:.2f} mm (résonance {f:.1f} GHz)" for l, f in zip(el["stub_mm"], el["stub_resonance_GHz"]))
        what = el.get("what", "via")
        side = {"P": " (brin P seul)", "N": " (brin N seul)", "PN": " (P et N)"}.get(el.get("strand"), "")
        what = what + side + (" — broche terminale" if el.get("terminal") else "")
        info = (f"{what} {el['layers']} : fût Z = {el['z_via']:.0f} Ω sur {h * 1e3:.2f} mm, "
                f"L ≈ {el['L_total_nH']:.2f} nH, C ≈ {el['C_barrel_pF']:.3f} pF, antipad Ø {el['D2'] * 1e3:.2f} mm"
                + (f", stub(s) {stubs}" if stubs else ", sans stub")
                + ("" if el["return_via"] or h <= 0 else ", sans via de retour (inductance de boucle ajoutée)")
                + (" ; approximation : demi-effet différentiel, conversion de mode non calculée" if asym else "")
                + (f" — {el['note']}" if el.get("note") else ""))
        return prims, info
    if t == "corner":
        dC = prof["c"][i] * el["extra_len"]
        prims = [("C", dC / k)]
        return prims, f"coin {el['angle']:.0f}° : ΔC ≈ {dC * 1e15:+.1f} fF par brin"
    if t == "slot":
        kc = 0.0
        if kind == "pair":
            j = _nearest(prof, el["s"])
            for dj in range(len(prof["s"])):
                for jj in (j - dj, j + dj):
                    if 0 <= jj < len(prof["src"]) and prof["src"][jj] == "ok":
                        kc = _k_at(tr, prof, jj)
                        break
                else:
                    continue
                break
        Lp = el["L"] * (2.0 * (1.0 - kc) if kind == "pair" else 1.0)
        info = (f"fente sur {el['layer']} : {el['D_mm']:.1f} mm × {el['W_mm']:.2f} mm → L ≈ {el['L_nH']:.2f} nH par brin"
                + (f" (mode différentiel : × 2(1 − k), k = {kc:.2f})" if kind == "pair" else "")
                + (f" — {el['note']}" if el.get("note") else ""))
        return [("L", Lp)], info
    if t == "stub":
        net = prof["net"]
        path = _path_samples(tr.samples, net, el["path_id"])
        if not path:
            return [], None
        s = np.array([x.s for x in path])
        z, e, _, _ = _values(path, "single", "leg" if kind == "pair" else "main")
        zf, ef = _fill(s, z), _fill(s, e)
        if zf is None:                       # aucune valeur : impédance du chemin principal au point de jonction
            zf = np.full(len(s), prof["z"][i] / k)
            ef = np.full(len(s), prof["e"][i])
        ds = np.maximum(np.diff(_edges(s)), 0.0)
        secs = [(Z * k, E, L) for Z, E, L in zip(zf, ef, ds)]
        if el["reverse"]:
            secs = secs[::-1]
        L_tot = float(ds.sum())
        f_res = C_LIGHT / (4 * L_tot * math.sqrt(float(np.mean(ef)))) if L_tot > 0 else float("inf")
        info = (f"stub (branche en T) de {L_tot * 1e3:.1f} mm, Z ≈ {np.mean(zf):.0f} Ω, "
                f"résonance quart d'onde ≈ {f_res / 1e9:.2f} GHz")
        return [("S", secs)], info
    return [], None


def _k_at(tr, prof, j):
    path = _path_samples(tr.samples, prof["net"], prof["path_id"])
    r = path[j].result or {}
    return float(r.get("k_coupling", 0.0) or 0.0)


def build_model(tr, kind: str):
    """(primitives, profil, éléments décrits, pads décrits) du chemin principal d'une cible."""
    prof = line_profile(tr.samples, kind, getattr(tr, "main_path", None))
    if prof is None:
        return None
    # Jonction en T : la coupe perpendiculaire longe la branche, qui serait vue comme un « pad » ;
    # la branche est déjà modélisée par son stub -> pas de modèle localisé de pad à cet endroit.
    path = _path_samples(tr.samples, prof["net"], prof["path_id"])
    for el in getattr(tr, "elements", None) or []:
        if el["type"] != "stub":
            continue
        for i, x in enumerate(path):
            if prof["src"][i] == "modèle" and abs(x.s - el["s"]) < 2.0 * x.width + 0.5e-3:
                prof["src"][i] = "nan"
    _refill(prof)
    edges = _edges(prof["s"])
    inserts: Dict[int, list] = {}
    described = []
    for el in getattr(tr, "elements", None) or []:
        prims, info = _element_prims(el, prof, tr, kind)
        if not prims:
            continue
        k = int(np.argmin(np.abs(edges[:-1] - el["s"])))
        inserts.setdefault(k, []).extend((p, len(described)) for p in prims)
        described.append({"type": el["type"], "s_mm": el["s"] * 1e3, "info": info})
    prims, tags = [], []            # tags : ("pt", indice du point) ou ("el", indice de l'élément décrit)
    for i in range(len(prof["s"])):
        for p, ek in inserts.get(i, []):
            prims.append(p)
            tags.append(("el", ek))
        prims.append(("T", float(prof["z"][i]), float(prof["e"][i]), float(prof["ds"][i])))
        tags.append(("pt", i))
    prof["tags"] = tags
    # pads : groupes consécutifs de points issus du modèle localisé
    pads = []
    src = prof["src"]
    i = 0
    while i < len(src):
        if src[i] != "modèle":
            i += 1
            continue
        j = i
        while j + 1 < len(src) and src[j + 1] == "modèle":
            j += 1
        # C′ de la piste nue autour du pad (interpolation entre points calculés voisins)
        ok = np.array([x == "ok" for x in src])
        if ok.any():
            c_line = np.interp(prof["s"][i:j + 1], prof["s"][ok], prof["c"][ok])
            dC = float(np.sum((prof["c"][i:j + 1] - c_line) * prof["ds"][i:j + 1]))
        else:
            dC = float("nan")
        pads.append({"type": "pad", "s_mm": prof["s"][i] * 1e3,
                     "info": f"pad / cuivre du même net sur {prof['ds'][i:j + 1].sum() * 1e3:.2f} mm : "
                             f"Z locale min {prof['z'][i:j + 1].min():.1f} Ω, ΔC ≈ {dC * 1e15:+.0f} fF "
                             f"{'(mode impair, par brin)' if kind == 'pair' else ''}"})
        i = j + 1
    return prims, prof, described, pads


def _refill(prof, keep=("ok", "modèle")):
    """Réinterpole z, εeff, C′ aux points dont la source n'est pas dans `keep`."""
    ok = np.array([x in keep for x in prof["src"]])
    if not ok.any():
        return
    for k in ("z", "e", "c"):
        v = prof[k]
        if np.isfinite(v[ok]).all():
            prof[k] = np.interp(prof["s"], prof["s"][ok], v[ok])


def _db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-12))


def _ml_of(s11):
    return -10 * np.log10(np.maximum(1 - np.abs(s11) ** 2, 1e-12))


SECTION_CLASS = {"ok": "dans la tolérance", "high": "trop haut", "low": "trop bas", "pad": "pad (modèle localisé)",
                 "nan": "interpolé (non calculé)"}


def section_contributions(prims, prof, described, zref: float, z_target: float, tol: float, fk: float,
                          f: Optional[np.ndarray] = None, n_curves: int = 6):
    """Contribution de chaque tronçon homogène et de chaque élément localisé à la réflexion.

    Retourne (sections triées par perte « seul » décroissante, courbes de perte « seul » des n_curves
    premiers tronçons sur la grille f). Voir l'en-tête du module pour la définition.
    """
    tags = prof.get("tags") or [("pt", i) for i in range(len(prims))]
    s, z, src = prof["s"], prof["z"], prof["src"]
    edges = _edges(s)
    lo, hi = z_target * (1 - tol), z_target * (1 + tol)

    def cls(i):
        if src[i] == "modèle":
            return "pad"
        if src[i] == "nan":
            return "nan"
        return "high" if z[i] > hi else ("low" if z[i] < lo else "ok")

    secs = []
    i = 0
    while i < len(s):
        c = cls(i)
        j = i
        while j + 1 < len(s) and cls(j + 1) == c:
            j += 1
        secs.append({"kind": c, "pts": set(range(i, j + 1)), "els": set(),
                     "s0_mm": edges[i] * 1e3, "s1_mm": edges[j + 1] * 1e3,
                     "z_min": float(z[i:j + 1].min()), "z_max": float(z[i:j + 1].max()),
                     "z_mean": float(np.average(z[i:j + 1], weights=np.maximum(prof["ds"][i:j + 1], 1e-12)))})
        i = j + 1
    for k, d in enumerate(described):
        secs.append({"kind": d["type"], "pts": set(), "els": {k}, "s0_mm": d["s_mm"], "s1_mm": d["s_mm"],
                     "z_min": float("nan"), "z_max": float("nan"), "z_mean": float("nan"), "info": d["info"]})

    def variant(sec, alone: bool):
        out = []
        for p, (tk, idx) in zip(prims, tags):
            mine = idx in (sec["pts"] if tk == "pt" else sec["els"])
            keep = mine if alone else not mine
            if tk == "pt":
                out.append(p if keep else ("T", zref, p[2], p[3]))
            elif keep:
                out.append(p)
        return out

    fk_arr = np.array([fk])
    ml_full = float(_ml_of(cascade(prims, zref, fk_arr)[0])[0])
    for sec in secs:
        s11a = cascade(variant(sec, True), zref, fk_arr)[0][0]
        s11f = cascade(variant(sec, False), zref, fk_arr)[0][0]
        sec["ml_alone_db"] = float(_ml_of(s11a))
        sec["rl_alone_db"] = float(-_db(s11a))
        sec["ml_if_fixed_db"] = float(_ml_of(s11f))
        sec["rl_if_fixed_db"] = float(-_db(s11f))
        sec["gain_if_fixed_db"] = ml_full - sec["ml_if_fixed_db"]
        sec["length_mm"] = sec["s1_mm"] - sec["s0_mm"]
        sec["label"] = SECTION_CLASS.get(sec["kind"], {"via": "via", "corner": "coin", "slot": "fente",
                                                        "stub": "stub"}.get(sec["kind"], sec["kind"]))
    secs.sort(key=lambda x: -x["ml_alone_db"])
    curves = []
    if f is not None:
        for sec in secs[:n_curves]:
            curves.append({"label": _section_name(sec), "kind": sec["kind"],
                           "ml": _ml_of(cascade(variant(sec, True), zref, f)[0]).tolist()})
    for sec in secs:
        sec.pop("pts")
        sec.pop("els")
        sec["name"] = _section_name(sec)
    return secs, curves


def _section_name(sec) -> str:
    if sec["kind"] in SECTION_CLASS:
        return f"{sec['s0_mm']:.1f}–{sec['s1_mm']:.1f} mm · {sec['label']} · Z {sec['z_mean']:.0f} Ω"
    return f"{sec['label']} à {sec['s0_mm']:.1f} mm"


def analyze_target(tr, spec: SignalSpec, n_plot: int = 301, lumped: bool = True) -> Optional[dict]:
    """Métriques et courbes (listes, sérialisables) pour une cible de l'analyse.

    lumped=False : ligne seule (sans pads, vias, stubs, coins, fentes), pour comparaison.
    """
    kind = tr.target["kind"]
    model = build_model(tr, kind)
    if model is None:
        return None
    prims, prof, described, pads = model
    if not lumped:
        # ligne seule : les pads sont remplacés par l'impédance de la piste (interpolation)
        _refill(prof, keep=("ok",))
        prims = [("T", float(z), float(e), float(d)) for z, e, d in zip(prof["z"], prof["e"], prof["ds"])]
    s, ds, z = prof["s"], prof["ds"], prof["z"]
    src = prof["src"]
    zref = spec.zref or tr.target["z_target"]
    fk = spec.f_key
    fmax = max(3 * fk, 1e6)
    f = np.linspace(0, fmax, n_plot)
    s11, s21 = cascade(prims, zref, f)
    s11k, s21k = cascade(prims, zref, np.array([fk]))
    g = np.abs(s11)
    gk = float(abs(s11k[0]))
    ml = -10 * np.log10(np.maximum(1 - g ** 2, 1e-12))
    band = f <= fk + 1e-9
    interp = np.array([x == "nan" for x in src])
    noref = np.array([x == "no_reference" for x in prof["status"]])
    out = {
        "spec": spec.to_dict(), "zref": zref, "f_key": fk, "length_mm": float(np.sum(ds) * 1e3),
        "interpolated_pct": float(100 * ds[interp].sum() / max(ds.sum(), 1e-15)),
        "no_reference_mm": float(ds[noref].sum() * 1e3),
        "lumped": bool(lumped), "elements": described if lumped else [], "pads": pads if lumped else [],
        "s11_db_at_fkey": float(_db(gk)), "return_loss_db_at_fkey": float(-_db(gk)),
        "mismatch_loss_db_at_fkey": float(-10 * math.log10(max(1 - gk ** 2, 1e-12))),
        "s21_db_at_fkey": float(_db(s21k[0])),
        "vswr_at_fkey": float((1 + gk) / (1 - gk)) if gk < 1 else float("inf"),
        "worst_s11_db_in_band": float(_db(g[band]).max()),
        "worst_mismatch_loss_db_in_band": float(ml[band].max()),
        "gamma_static_pct": float(100 * np.max(np.abs((z - zref) / (z + zref)))),
        "curve_f_hz": f.tolist(), "curve_s11_db": _db(s11).tolist(), "curve_ml_db": ml.tolist(),
        "profile_s_mm": (s * 1e3).tolist(), "profile_z": z.tolist(),
        "quality_at_fkey": quality(float(-_db(gk))),
    }
    pair = getattr(tr, "pair", None)
    if kind == "pair" and pair:
        dt = abs(pair["skew_ps"]) * 1e-12
        conv = abs(math.sin(math.pi * fk * dt))
        out["skew_ps"] = pair["skew_ps"]
        out["mode_conversion_db_at_fkey"] = float(20 * math.log10(max(conv, 1e-12)))
        ui = 1.0 / spec.bitrate if spec.kind == "digital" and spec.bitrate > 0 else None
        out["skew_pct_ui"] = float(100 * dt / ui) if ui else None
        out["skew_pct_rise"] = float(100 * dt / spec.rise) if spec.kind == "digital" and spec.rise > 0 else None
    if lumped:
        secs, curves = section_contributions(prims, prof, described if lumped else [], zref,
                                             tr.target["z_target"], tr.target.get("tol", 0.1), fk, f)
        out["sections"] = secs
        out["section_curves"] = curves
    if spec.kind == "digital" and spec.rise > 0:
        x, zt, rho = _tdr_prims(prims, zref, spec.rise, float(np.sum(ds)))
        step = max(1, len(x) // 400)
        out.update({
            "tdr_x_mm": (x[::step] * 1e3).tolist(), "tdr_z": zt[::step].tolist(),
            "tdr_z_min": float(zt.min()), "tdr_z_max": float(zt.max()),
            "tdr_peak_reflection_pct": float(100 * np.max(np.abs(rho))),
            # aller-retour plus court que le front : la TDR ne localise pas, elle intègre (ligne « courte »)
            "tdr_round_trip_ps": float(2e12 * np.sum(ds * np.sqrt(prof["e"])) / C_LIGHT),
        })
        if lumped and (described or pads):
            ref = analyze_target(tr, spec, n_plot, lumped=False)
            out["line_only"] = {k: ref[k] for k in ("return_loss_db_at_fkey", "mismatch_loss_db_at_fkey",
                                                     "tdr_peak_reflection_pct", "tdr_z_min", "tdr_z_max")}
            out["tdr_line_only_z"] = ref["tdr_z"]
            out["tdr_line_only_x_mm"] = ref["tdr_x_mm"]
    elif lumped and (described or pads):
        ref = analyze_target(tr, spec, n_plot, lumped=False)
        out["line_only"] = {k: ref[k] for k in ("return_loss_db_at_fkey", "mismatch_loss_db_at_fkey")}
    return out


def analyze_run(run, spec: SignalSpec) -> None:
    """Ajoute `signal` à chaque TargetReport de l'analyse (en place)."""
    for tr in run.targets:
        tr.signal = analyze_target(tr, spec)
