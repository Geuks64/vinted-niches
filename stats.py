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


def segment_stats(lignes, maintenant):
    """lignes : annonces suivies (dict). Renvoie les indicateurs du segment."""
    utiles = [a for a in lignes if a["statut"] not in ("supprime", "masque", "inconnu") and a["n_controles"] > 0]
    res = {"n": len(utiles)}
    vendues = [a for a in utiles if a["statut"] == "vendu"]
    res["vendues"] = len(vendues)
    for nom, (h, idx) in FENETRES.items():
        # éligible = annonce vue il y a au moins h heures (même « âge » pour vendues et invendues,
        # sinon les ventes rapides gonfleraient le taux) dont on connaît l'état à h heures.
        elig = [a for a in utiles if a["vu_le"] <= maintenant - h * 3600 and
                (a["n_controles"] > idx or a["statut"] == "vendu")]
        ok = [a for a in elig if a["statut"] == "vendu" and (a["vendu_avant_h"] or 0) <= h * 1.25]
        res["elig_" + nom] = len(elig)
        res["taux_" + nom] = round(100 * len(ok) / len(elig), 1) if len(elig) >= N_MIN else None
    res["delai_h"] = mediane([((a["vendu_apres_h"] or 0) + (a["vendu_avant_h"] or 0)) / 2 for a in vendues])
    res["prix_vendu"] = mediane([a["prix_final"] or a["prix"] for a in vendues])
    res["prix"] = mediane([a["prix"] for a in utiles])
    favs = [a["favoris"] for a in utiles if a["favoris"] is not None]
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
    return res


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    maintenant = int(time.time())
    suivies = [dict(r) for r in con.execute("SELECT * FROM annonces WHERE suivi = 1")]

    # ---------- segments
    groupes = {"categorie": defaultdict(list), "marque": defaultdict(list),
               "categorie_marque": defaultdict(list), "categorie_prix": defaultdict(list)}
    for a in suivies:
        cat, marque, tr = a["categorie"], a["marque"] or "(sans marque)", tranche(a["prix"])
        groupes["marque"][marque].append(a)
        if cat:
            groupes["categorie"][cat].append(a)
            groupes["categorie_marque"][f"{cat} · {marque}"].append(a)
            if tr:
                groupes["categorie_prix"][f"{cat} · {tr}"].append(a)
    segments = {}
    for nom, g in groupes.items():
        lignes = []
        for cle, annonces in g.items():
            s = segment_stats(annonces, maintenant)
            if s["n"] >= N_MIN:
                lignes.append({"nom": cle, **s})
        lignes.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0), -x["n"]))
        segments[nom] = lignes[:400]

    # ---------- offre : nouvelles annonces par jour et par marque (7 derniers jours)
    offre = defaultdict(int)
    for marque, n in con.execute("""SELECT COALESCE(marque, '(sans marque)'), COUNT(*) FROM annonces
                                    WHERE vu_le > ? GROUP BY 1""", (maintenant - 7 * 86400,)):
        offre[marque] += n
    for marque, n in con.execute("""SELECT marque, SUM(n) FROM offre_jour
                                    WHERE jour >= date(?, 'unixepoch') GROUP BY 1""", (maintenant - 7 * 86400,)):
        offre[marque] += n
    for ligne in segments["marque"]:
        ligne["offre_7j"] = offre.get(ligne["nom"], 0)

    def carte(a):
        return {k: a.get(k) for k in ("id", "titre", "marque", "taille", "etat", "prix", "prix_final",
                                      "categorie", "favoris", "miniature", "url", "vu_le",
                                      "vendu_apres_h", "vendu_avant_h", "vendu_detecte", "pro")}

    # ---------- flux : dernières ventes détectées, annonces qui plaisent
    ventes = sorted((a for a in suivies if a["statut"] == "vendu"), key=lambda a: -(a["vendu_detecte"] or 0))
    chaudes = sorted((a for a in suivies if a["vu_le"] > maintenant - 48 * 3600 and a["favoris"]
                      and a["statut"] in ("en_vente", "reserve")),
                     key=lambda a: -(a["favoris"] / max(1, (a["dernier_controle"] - a["vu_le"]) / 3600)))

    # ---------- état du relevé
    releves = [dict(r) for r in con.execute("SELECT * FROM releves ORDER BY debut DESC LIMIT 30")]
    statuts = dict(con.execute("SELECT statut, COUNT(*) FROM annonces WHERE suivi = 1 GROUP BY 1").fetchall())
    combos = defaultdict(int)
    for a in suivies:
        if a["brut"]:
            b = json.loads(a["brut"])
            cle = ", ".join(f"{k}={b[k]}" for k in sorted(b) if k not in ("favourite_count", "catalog_id"))
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
                   "statuts": statuts, "controles_en_retard": en_retard},
        "segments": segments,
        "ventes": [carte(a) for a in ventes[:60]],
        "chaudes": [carte(a) for a in chaudes[:40]],
        "releves": releves,
        "indicateurs_bruts": sorted(([k, v] for k, v in combos.items()), key=lambda x: -x[1])[:15],
        "reglages": {"n_min": N_MIN, "fenetres": list(FENETRES)},
    }
    os.makedirs(os.path.dirname(SORTIE) or ".", exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, separators=(",", ":"))
    print(f"stats.json : {len(suivies)} suivies, {len(ventes)} ventes, "
          f"{sum(len(v) for v in segments.values())} segments affichables")


if __name__ == "__main__":
    main()
