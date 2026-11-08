"""Niches Vinted — calcul des statistiques -> site/stats.json (lu par le tableau de bord).

Pour chaque segment (catégorie, marque, catégorie + marque, catégorie + tranche de prix) :
  - nb d'annonces suivies
  - % vendues en 24 h / 3 j / 7 j (seulement parmi les annonces assez vieilles pour le savoir)
  - délai médian de vente, prix médian vendu, prix médian des annonces, favoris moyens
  - un « score niche » = meilleur % de vente disponible, pondéré par la taille de l'échantillon
"""
import json
import os
import sqlite3
import statistics
import time
from collections import defaultdict

DB = os.environ.get("NICHES_DB", "vinted.db")
SORTIE = os.environ.get("NICHES_SORTIE", "site/stats.json")
N_MIN = int(os.environ.get("NICHES_N_MIN", "8"))      # échantillon minimum pour afficher un segment
TRANCHES = [(0, 5), (5, 10), (10, 20), (20, 40), (40, 80), (80, 10**9)]
FENETRES = {"24h": (24, 1), "3j": (72, 2), "7j": (168, 3)}   # fenêtre -> (heures, index du contrôle)


def tranche(prix):
    if prix is None:
        return None
    for a, b in TRANCHES:
        if a <= prix < b:
            return f"{a}–{b} €" if b < 10**9 else f"{a} € et +"
    return None


def mediane(v):
    v = [x for x in v if x is not None]
    return round(statistics.median(v), 2) if v else None


# Rattrapage : une ancienne annonce vue une fois compte pour la fenêtre qui correspond à son âge
TRANCHES_AGE = {"24h": (18, 48), "3j": (48, 120), "7j": (120, 260)}


ETATS_VENDU = ("vendu", "vendu_probable")          # ventes : confirmées + probables (voir collecte.py)
EXCLUS = ("supprime", "masque", "inconnu", "indisponible")   # jamais comptés comme vendus NI invendus
N_FIABLE = 30   # en dessous : verdict « à confirmer »


def est_vendu(a):
    return a.get("statut") in ETATS_VENDU


def wilson(k, n, z=1.96):
    """Intervalle de confiance de Wilson à 95 % pour une proportion k/n -> (bas %, haut %)."""
    if not n:
        return None, None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    marge = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return round(100 * max(0, centre - marge), 1), round(100 * min(1, centre + marge), 1)


def confiance(n_elig, bas, haut):
    """Note de confiance 0-100 : 100 = fourchette très étroite. Basée sur la largeur de l'intervalle de Wilson."""
    if not n_elig or bas is None:
        return 0
    return max(0, min(100, round(100 - 2 * (haut - bas))))


def segment_stats(lignes, maintenant, ratt=()):
    """lignes : annonces suivies (dict) ; ratt : annonces du rattrapage. Renvoie les indicateurs du segment.
    Les annonces supprimées / masquées / indisponibles / illisibles sont EXCLUES (ni vendues ni invendues)."""
    utiles = [a for a in lignes if a["statut"] not in EXCLUS and a["n_controles"] > 0]
    ratt = [a for a in ratt if a["statut"] in ("en_vente", "reserve") + ETATS_VENDU]
    res = {"n": len(utiles) + len(ratt), "n_rattrapage": len(ratt),
           "n_pro": sum(1 for a in utiles + ratt if a.get("pro") == 1)}
    vendues = [a for a in utiles if est_vendu(a)]
    r_vendues = [a for a in ratt if est_vendu(a)]
    res["vendues"] = len(vendues) + len(r_vendues)
    res["vendues_confirmees"] = sum(1 for a in vendues + r_vendues if a["statut"] == "vendu")
    for nom, (h, idx) in FENETRES.items():
        # éligible = annonce vue il y a au moins h heures (même « âge » pour vendues et invendues,
        # sinon les ventes rapides gonfleraient le taux) dont on connaît l'état à h heures.
        elig = [a for a in utiles if a["vu_le"] <= maintenant - h * 3600 and
                (a["n_controles"] > idx or est_vendu(a))]
        ok = [a for a in elig if est_vendu(a) and (a["vendu_avant_h"] or 0) <= h * 1.25]
        lo, hi = TRANCHES_AGE[nom]
        r_elig = [a for a in ratt if lo <= (a["age_h"] or 0) < hi]
        r_ok = [a for a in r_elig if est_vendu(a)]
        n_elig, n_ok = len(elig) + len(r_elig), len(ok) + len(r_ok)
        res["elig_" + nom] = n_elig          # nombre EXACT d'annonces utilisées pour ce taux
        res["vendues_" + nom] = n_ok
        res["taux_" + nom] = round(100 * n_ok / n_elig, 1) if n_elig >= N_MIN else None
        res["ic_" + nom] = wilson(n_ok, n_elig) if n_elig >= N_MIN else (None, None)
    res["delai_h"] = mediane([((a["vendu_apres_h"] or 0) + (a["vendu_avant_h"] or 0)) / 2 for a in vendues])
    res["prix_vendu"] = mediane([a["prix_final"] or a["prix"] for a in vendues] + [a["prix"] for a in r_vendues])
    res["prix"] = mediane([a["prix"] for a in utiles] + [a["prix"] for a in ratt])
    favs = [a["favoris"] for a in utiles + ratt if a["favoris"] is not None]
    res["favoris"] = round(sum(favs) / len(favs), 1) if favs else None
    # Score : meilleure fenêtre disponible (7 j > 3 j > 24 h), pondérée par la taille de l'échantillon
    for nom in ("7j", "3j", "24h"):
        if res["taux_" + nom] is not None:
            e = res["elig_" + nom]
            res["score"] = round(res["taux_" + nom] * e / (e + 15), 1)
            res["fenetre"] = nom
            break
    else:
        res["score"], res["fenetre"] = None, None
    # Note de confiance (sur la fenêtre utilisée) + nombre d'annonces derrière le chiffre
    f = res["fenetre"]
    res["n_utilisees"] = res["elig_" + f] if f else 0
    res["confiance"] = confiance(res["n_utilisees"], *res["ic_" + f]) if f else 0
    return res


def pays_de(a):
    """Pays du vendeur (code à 2 lettres) lu dans les indicateurs bruts de la page, sinon None."""
    if not a.get("brut"):
        return None
    b = json.loads(a["brut"])
    for k, v in b.items():
        if k in ("ipCountry", "countryCode"):   # pays du visiteur / version du site, pas du vendeur
            continue
        if "country" in k.lower() and isinstance(v, str):
            if len(v) == 2 and v.isalpha():
                return v.upper()
            if v.lower() == "france":
                return "FR"
    return None


def age_heures(a, maintenant):
    """Âge de l'annonce au moment où ses favoris ont été lus."""
    if a.get("age_h") is not None:                       # rattrapage
        return a["age_h"]
    if a.get("dernier_controle") and a.get("vu_le"):     # suivi
        return (a["dernier_controle"] - a["vu_le"]) / 3600
    return None


def favoris_jour(a, maintenant):
    """Favoris gagnés par jour en ligne (signal d'intérêt des acheteurs)."""
    h = age_heures(a, maintenant)
    if a.get("favoris") is None or h is None:
        return None
    return a["favoris"] / max(h / 24, 0.25)


def en_demande(a):
    """L'annonce porte-t-elle un badge de demande (« En demande », « Populaire »...) ?"""
    badge = a.get("badge")
    if not badge and a.get("brut"):
        badge = json.loads(a["brut"]).get("badge")
    return bool(badge)


def carte(a, maintenant):
    c = {k: a.get(k) for k in ("id", "titre", "marque", "taille", "etat", "prix", "prix_final", "statut",
                               "categorie", "favoris", "miniature", "url", "vu_le", "vendu_apres_h",
                               "vendu_avant_h", "vendu_detecte", "pro", "rattrapage")}
    fj = favoris_jour(a, maintenant)
    c["favoris_jour"] = round(fj, 1) if fj is not None else None
    c["badge"] = en_demande(a)
    return c


def calculer_vue(suivies, ratt, maintenant, offre):
    """Toutes les stats d'une « vue » (toute l'Europe, ou France seulement)."""
    # ---------- segments
    noms = ("categorie_large", "categorie", "marque", "categorie_marque", "categorie_prix", "produit")
    groupes = {n: defaultdict(list) for n in noms}
    groupes_r = {n: defaultdict(list) for n in noms}
    for liste, cible in ((suivies, groupes), (ratt, groupes_r)):
        for a in liste:
            cat, marque, tr = a["categorie"], a["marque"] or "(sans marque)", tranche(a["prix"])
            cible["marque"][marque].append(a)
            if cat:
                large = " > ".join(cat.split(" > ")[:3])   # ex. « Femmes > Vêtements > Robes »
                cible["categorie"][cat].append(a)
                cible["categorie_large"][large].append(a)
                cible["categorie_marque"][f"{cat} · {marque}"].append(a)
                # « produit » = catégorie large + marque (ex. « Femmes > Vêtements > Sweats · Nike »)
                cible["produit"][f"{large} · {marque}"].append(a)
                if tr:
                    cible["categorie_prix"][f"{cat} · {tr}"].append(a)
    segments = {}
    for nom in noms:
        g, gr = groupes[nom], groupes_r[nom]
        lignes = []
        for cle in set(g) | set(gr):
            s = segment_stats(g.get(cle, []), maintenant, gr.get(cle, []))
            if s["n"] >= N_MIN:
                tous = g.get(cle, []) + gr.get(cle, [])
                if nom in ("produit", "categorie_large", "marque"):
                    # annonces concernées (affichées sous la ligne du Top) : vendues d'abord, puis les plus aimées
                    ex = sorted(tous, key=lambda a: (not est_vendu(a), -(favoris_jour(a, maintenant) or 0)))
                    s["exemples"] = [carte(a, maintenant) for a in ex if a.get("miniature")][:8]
                fj = [x for x in (favoris_jour(a, maintenant) for a in tous) if x is not None]
                s["favoris_jour"] = round(sum(fj) / len(fj), 2) if fj else None
                s["part_demande"] = round(100 * sum(en_demande(a) for a in tous) / len(tous), 1)
                lignes.append({"nom": cle, **s})
        lignes.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0), -x["n"]))
        segments[nom] = lignes[:400]
    for ligne in segments["marque"]:
        ligne["offre_7j"] = offre.get(ligne["nom"], 0)

    # ---------- références globales (pour comparer chaque produit à la moyenne de Vinted)
    glob = segment_stats(suivies, maintenant, ratt)
    tous = suivies + ratt
    fj_tous = [x for x in (favoris_jour(a, maintenant) for a in tous) if x is not None]
    fav_moy = sum(fj_tous) / len(fj_tous) if fj_tous else 0
    ref = {"taux_24h": glob["taux_24h"], "taux_3j": glob["taux_3j"], "taux_7j": glob["taux_7j"],
           "ic_7j": glob["ic_7j"], "elig_7j": glob["elig_7j"], "vendues_7j": glob["vendues_7j"],
           "favoris_jour": round(fav_moy, 2), "n": glob["n"], "vendues": glob["vendues"],
           "vendues_confirmees": glob["vendues_confirmees"]}

    # ---------- Top du moment : produits classés par un indice de demande (0-100)
    top = []
    for nom_seg in ("produit", "categorie_large", "marque"):
        for l in segments[nom_seg]:
            # taux de vente ramené à 7 jours (la fenêtre 7 j n'est pas toujours disponible)
            t7 = None
            for f in ("7j", "3j", "24h"):
                if l["taux_" + f] is not None and ref["taux_" + f]:
                    t7 = l["taux_" + f] * (ref["taux_7j"] or ref["taux_" + f]) / ref["taux_" + f]
                    break
            if t7 is None:
                continue
            fav = l["favoris_jour"] if l["favoris_jour"] is not None else fav_moy
            r_vente = t7 / ref["taux_7j"] if ref["taux_7j"] else 1
            r_fav = fav / fav_moy if fav_moy else 1
            # Indice brut (0-100, 50 = moyenne), puis ramené vers 50 selon la confiance :
            #   indice = 50 + r × (brut − 50), r = confiance / 100 (petit échantillon -> proche de 50)
            r = l["confiance"] / 100
            brut = min(100.0, 50 * (0.65 * min(r_vente, 3) + 0.35 * min(r_fav, 3)))
            indice = round(50 + r * (brut - 50), 1)
            rv = 1 + r * (r_vente - 1)            # ratios « prudents » pour le verdict
            rf = 1 + r * (r_fav - 1)
            if l["n_utilisees"] < N_FIABLE:
                verdict = "a_confirmer"
            elif rv >= 1.4 or (rv >= 1.1 and rf >= 1.5):
                verdict = "tres_demande"
            elif rv >= 1.0:
                verdict = "bien"
            elif rv <= 0.6 and rf <= 1:
                verdict = "eviter"
            else:
                verdict = "moyen"
            f = l["fenetre"]
            top.append({"nom": l["nom"], "type": nom_seg, "indice": indice, "verdict": verdict,
                        "exemples": l.get("exemples", []),
                        "taux_7j_estime": round(t7, 1), "favoris_jour": l["favoris_jour"],
                        "part_demande": l["part_demande"], "prix_vendu": l["prix_vendu"], "prix": l["prix"],
                        "delai_h": l["delai_h"], "n": l["n"], "vendues": l["vendues"],
                        "vendues_confirmees": l["vendues_confirmees"],
                        "fenetre": f, "taux_fenetre": l["taux_" + f], "ic": l["ic_" + f],
                        "n_utilisees": l["n_utilisees"], "confiance": l["confiance"],
                        "part_pro": round(100 * l["n_pro"] / l["n"], 1) if l["n"] else None,
                        "offre_7j": l.get("offre_7j")})
    top.sort(key=lambda x: -x["indice"])

    # ---------- annonces les plus aimées (7 derniers jours de relevé), vendues ou non
    recentes = [a for a in tous if favoris_jour(a, maintenant) is not None
                and (a.get("verifie_le") or a.get("dernier_controle") or 0) > maintenant - 7 * 86400]
    aimees = sorted(recentes, key=lambda a: -favoris_jour(a, maintenant))[:60]

    # ---------- flux des ventes
    ventes = [a for a in suivies if est_vendu(a)]
    for a in ratt:
        if est_vendu(a):
            # vendue à un moment inconnu avant age_h : on l'affiche « vendue, publiée il y a ~X »
            ventes.append({**a, "vendu_detecte": a["verifie_le"], "vu_le": a["verifie_le"] - int((a["age_h"] or 0) * 3600),
                           "vendu_apres_h": None, "vendu_avant_h": None, "rattrapage": 1, "pro": None, "prix_final": None})
    ventes.sort(key=lambda a: -(a["vendu_detecte"] or 0))
    chaudes = sorted((a for a in suivies if a["vu_le"] > maintenant - 48 * 3600 and a["favoris"]
                      and a["statut"] in ("en_vente", "reserve")),
                     key=lambda a: -(a["favoris"] / max(1, (a["dernier_controle"] - a["vu_le"]) / 3600)))
    return {"segments": segments, "reference": ref, "top": top[:150],
            "aimees": [carte(a, maintenant) for a in aimees],
            "ventes": [carte(a, maintenant) for a in ventes[:60]],
            "chaudes": [carte(a, maintenant) for a in chaudes[:40]],
            "n_annonces": len(tous)}


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    maintenant = int(time.time())
    suivies = [dict(r) for r in con.execute("SELECT * FROM annonces WHERE suivi = 1")]
    ratt = [dict(r) for r in con.execute("SELECT * FROM rattrapage WHERE statut != 'supprime'")]

    # ---------- offre : nouvelles annonces par jour et par marque (7 derniers jours)
    offre = defaultdict(int)
    for marque, n in con.execute("""SELECT COALESCE(marque, '(sans marque)'), COUNT(*) FROM annonces
                                    WHERE vu_le > ? GROUP BY 1""", (maintenant - 7 * 86400,)):
        offre[marque] += n
    for marque, n in con.execute("""SELECT marque, SUM(n) FROM offre_jour
                                    WHERE jour >= date(?, 'unixepoch') GROUP BY 1""", (maintenant - 7 * 86400,)):
        offre[marque] += n

    # ---------- deux vues : toute l'Europe, et France seulement (vendeurs français)
    vue_tous = calculer_vue(suivies, ratt, maintenant, offre)
    # particuliers seulement : vendeur pro connu et = 0 (les annonces dont on ignore le type sont écartées)
    vue_part = calculer_vue([a for a in suivies if a.get("pro") == 0], [a for a in ratt if a.get("pro") == 0],
                            maintenant, offre)
    vue_fr = calculer_vue([a for a in suivies if pays_de(a) == "FR"],
                          [a for a in ratt if pays_de(a) == "FR"], maintenant, offre)
    pays = defaultdict(int)
    for a in suivies + ratt:
        pays[pays_de(a) or "?"] += 1

    # ---------- recherches ciblées (fichiers recherches/*.json écrits par recherche.py)
    recherches = []
    for nom in sorted(os.listdir("recherches")) if os.path.isdir("recherches") else []:
        if not nom.endswith(".json"):
            continue
        with open(os.path.join("recherches", nom), encoding="utf-8") as f:
            r = json.load(f)
        r.pop("a_suivre", None)
        # suivi des annonces de cette recherche (ajoutées au suivi normal par collecte.py)
        liees = [a for a in suivies if a.get("recherche") == r["numero"]]
        r["suivi"] = segment_stats(liees, maintenant) if liees else None
        recherches.append(r)
    recherches.sort(key=lambda r: -r.get("cree_le", 0))

    # ---------- état du relevé
    releves = [dict(r) for r in con.execute("SELECT * FROM releves ORDER BY debut DESC LIMIT 30")]
    statuts = dict(con.execute("SELECT statut, COUNT(*) FROM annonces WHERE suivi = 1 GROUP BY 1").fetchall())
    combos = defaultdict(int)
    for a in suivies + ratt:
        if a["brut"]:
            b = json.loads(a["brut"])
            cle = ", ".join(f"{k}={b[k]}" for k in sorted(b)
                            if k not in ("favourite_count", "catalog_id", "city", "ipCountry", "countryCode"))
            combos[cle] += 1
    vues_total = con.execute("SELECT COUNT(*) FROM annonces").fetchone()[0] + \
        (con.execute("SELECT COALESCE(SUM(n), 0) FROM offre_jour").fetchone()[0])
    en_retard = con.execute("""SELECT COUNT(*) FROM annonces WHERE prochain_controle IS NOT NULL
                               AND prochain_controle <= ?""", (maintenant - 3600,)).fetchone()[0]
    premier = con.execute("SELECT MIN(debut) FROM releves").fetchone()[0]

    stats = {
        "genere_le": maintenant,
        "depuis": premier,
        "resume": {"annonces_vues": vues_total, "suivies": len(suivies),
                   "rattrapage": dict(con.execute("SELECT statut, COUNT(*) FROM rattrapage GROUP BY 1").fetchall()),
                   "statuts": statuts, "controles_en_retard": en_retard,
                   "pays": dict(sorted(pays.items(), key=lambda x: -x[1])[:12])},
        "vues": {"tous": vue_tous, "fr": vue_fr, "part": vue_part},
        "recherches": recherches,
        "releves": releves,
        "indicateurs_bruts": sorted(([k, v] for k, v in combos.items()), key=lambda x: -x[1])[:15],
        "reglages": {"n_min": N_MIN, "fenetres": list(FENETRES)},
    }
    os.makedirs(os.path.dirname(SORTIE) or ".", exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, separators=(",", ":"))
    print(f"stats.json : {len(suivies)} suivies, {len(ratt)} rattrapage, {len(vue_tous['top'])} produits au top, "
          f"{len(vue_fr['top'])} en France")


if __name__ == "__main__":
    main()
