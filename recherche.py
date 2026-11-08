"""Niches Vinted — recherche ciblée « Est-ce que ça se vend ? ».

Lancée par le workflow « Recherche Vinted » quand tu ouvres une issue GitHub dont le titre commence
par « Recherche : » (le tableau de bord prépare cette page pour toi).

Tout de suite :
  - annonces en ligne pour ce produit (concurrence), nouvelles annonces par jour
  - prix pratiqués (bas / moyen / haut), favoris (« j'aime ») par jour, part « en demande »
  - ventes déjà repérées par l'outil pour ce produit (titres qui contiennent les mots cherchés)
Ensuite (1 à 7 jours) :
  - les annonces toutes fraîches de ce produit sont suivies par le relevé normal
    -> % vendu, délai et prix de vente réels (affichés sur le tableau de bord)

Écrit recherches/<numero>.json (lu par stats.py et collecte.py) et commentaire.md (réponse dans l'issue).
"""
import json
import os
import re
import sqlite3
import statistics
import sys
import time
import unicodedata
from collections import Counter
from urllib.parse import quote_plus

import collecte
import stats

PAGES_RECENTES = 3      # annonces les plus récentes (pour l'offre et le suivi)
PAGES_PERTINENTES = 2   # annonces « pertinentes » (souvent plus anciennes, pour les favoris)
TABLEAU = "https://geuks64.github.io/vinted-niches/"


def normaliser(t):
    """minuscules, sans accents (pour comparer les titres)."""
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def quantile(v, q):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    i = (len(v) - 1) * q
    a, b = int(i), min(int(i) + 1, len(v) - 1)
    return round(v[a] + (v[b] - v[a]) * (i - a), 2)


def euros(x):
    return "—" if x is None else f"{x:.2f} €".replace(".", ",")


def lire_recherche(v, texte, ordre, pages):
    cartes, total = [], None
    for page in range(1, pages + 1):
        r = v.get(f"/catalog?search_text={quote_plus(texte)}&order={ordre}&page={page}")
        if r is None or r.status_code != 200:
            break
        lot = collecte.lire_cartes(r.text)
        if total is None:
            m = re.search(r'"total_entries":\s*(\d+)', collecte.flux_next(r.text))
            total = int(m.group(1)) if m else None
        cartes += lot
        if len(lot) < 90:      # dernière page
            break
    return cartes, total


def main():
    titre = os.environ.get("RECHERCHE_TITRE", "").strip()
    numero = int(os.environ.get("NUMERO", "0") or 0)
    texte = re.sub(r"^\s*recherche\s*:?\s*", "", titre, flags=re.I).strip()
    if not texte:
        print("Titre vide : rien à chercher.")
        sys.exit(1)
    maintenant = int(time.time())
    con = sqlite3.connect(collecte.DB) if os.path.exists(collecte.DB) else None
    if con:
        con.row_factory = sqlite3.Row

    v = collecte.Vinted()
    v.get("/")
    recentes, total = lire_recherche(v, texte, "newest_first", PAGES_RECENTES)
    pertinentes, total2 = lire_recherche(v, texte, "relevance", PAGES_PERTINENTES)
    total = total or total2
    vues = {c["id"]: c for c in recentes + pertinentes}
    cartes = list(vues.values())
    collecte.log(f"« {texte} » : {len(recentes)} récentes, {len(pertinentes)} pertinentes, total annoncé {total}")

    # Âge des annonces d'après leur numéro (repères du relevé)
    def age_h(c):
        if not con or not con.execute("SELECT 1 FROM reperes LIMIT 1").fetchone():
            return None
        return max(0.1, collecte.id_vers_age(con, c["id"], maintenant))
    for c in recentes + pertinentes:   # les deux listes (une annonce peut être dans les deux)
        c["age_h"] = age_h(c)

    # Nouvelles annonces par jour : les plus récentes couvrent les X dernières heures
    par_jour = None
    ages = [c["age_h"] for c in recentes if c["age_h"] is not None]
    if ages:
        couverture = max(ages)
        if len(recentes) >= 90 * PAGES_RECENTES:
            par_jour = round(len(recentes) / max(couverture, 0.5) * 24)
        else:   # toutes les annonces tiennent dans la liste : on compte celles des dernières 24 h
            par_jour = sum(1 for a in ages if a <= 24)

    prix = [c["prix"] for c in cartes if c["prix"]]
    fav_jour = [c["favoris0"] / max(c["age_h"] / 24, 0.25) for c in cartes if c["age_h"] is not None]
    marques = Counter(c["marque"] or "(sans marque)" for c in cartes).most_common(5)
    part_demande = round(100 * sum(1 for c in cartes if c["badge"]) / len(cartes), 1) if cartes else None
    plus_aimees = sorted((c for c in cartes if c["age_h"] is not None),
                         key=lambda c: -c["favoris0"] / max(c["age_h"] / 24, 0.25))[:6]

    # Ventes déjà repérées par l'outil : titres contenant tous les mots cherchés
    mots = [m for m in re.findall(r"\w+", normaliser(texte)) if len(m) > 1]
    historique, ref_fav, ref_taux = None, None, None
    if con:
        suivies = [dict(r) for r in con.execute("SELECT * FROM annonces WHERE suivi = 1")]
        ratt = [dict(r) for r in con.execute("SELECT * FROM rattrapage WHERE statut != 'supprime'")]
        colle = lambda a: all(m in normaliser(a.get("titre")) + " " + normaliser(a.get("marque")) for m in mots)
        s_ok, r_ok = [a for a in suivies if colle(a)], [a for a in ratt if colle(a)]
        if s_ok or r_ok:
            historique = stats.segment_stats(s_ok, maintenant, r_ok)
        if historique and historique["n"] == 0:
            historique = None
        if historique:
            historique["exemples"] = [{"titre": a["titre"], "prix": a["prix"], "statut": a["statut"]}
                                      for a in (s_ok + r_ok)[:8]]
        glob = stats.segment_stats(suivies, maintenant, ratt)
        ref_taux = glob["taux_7j"] or glob["taux_3j"]
        fj = [x for x in (stats.favoris_jour(a, maintenant) for a in suivies + ratt) if x is not None]
        ref_fav = sum(fj) / len(fj) if fj else None

    fav_moy = round(sum(fav_jour) / len(fav_jour), 2) if fav_jour else None
    # Verdict simple
    if historique and historique.get("n", 0) >= 8 and ref_taux:
        t = historique["taux_7j"] or historique["taux_3j"] or historique["taux_24h"]
        verdict = None if t is None else ("🔥 Se vend très bien" if t >= 1.4 * ref_taux else
                                          "👍 Se vend bien" if t >= ref_taux else
                                          "😐 Se vend moyennement" if t >= 0.6 * ref_taux else "❄️ Se vend mal")
    else:
        verdict = None
    if verdict is None and fav_moy is not None and ref_fav:
        r = fav_moy / ref_fav
        verdict = ("🔥 Très recherché (beaucoup de « j'aime »)" if r >= 2 else
                   "👍 Intéresse les acheteurs" if r >= 1.2 else
                   "😐 Intérêt moyen" if r >= 0.7 else "❄️ Peu d'intérêt")
        verdict += " — à confirmer par le suivi des ventes"

    a_suivre = [c for c in recentes if c["age_h"] is not None and c["age_h"] <= 6][:60]
    resultat = {
        "numero": numero, "texte": texte, "cree_le": maintenant, "verdict": verdict,
        "en_ligne": total or len(cartes), "nouvelles_par_jour": par_jour,
        "prix_bas": quantile(prix, 0.25), "prix_median": quantile(prix, 0.5), "prix_haut": quantile(prix, 0.75),
        "favoris_jour": fav_moy, "favoris_jour_moyen_vinted": round(ref_fav, 2) if ref_fav else None,
        "part_demande": part_demande, "marques": marques,
        "historique": historique, "taux_7j_moyen_vinted": ref_taux,
        "plus_aimees": [{"titre": c["titre"], "prix": c["prix"], "favoris": c["favoris0"], "url": c["url"],
                         "miniature": c["miniature"], "age_h": round(c["age_h"], 1)} for c in plus_aimees],
        "a_suivre": [{**c, "age_h": round(c["age_h"], 2)} for c in a_suivre],
        "nb_suivies": len(a_suivre),
    }
    os.makedirs("recherches", exist_ok=True)
    with open(f"recherches/{numero:05d}.json", "w", encoding="utf-8") as f:
        json.dump(resultat, f, ensure_ascii=False, indent=1)

    # Réponse dans l'issue
    h = historique
    lignes = [f"## « {texte} »", "", f"**{verdict or 'Pas encore assez de données pour un avis'}**", "",
              "### En ce moment sur Vinted",
              f"- Annonces en ligne : **{'960 et plus (Vinted n’en compte pas plus)' if (resultat['en_ligne'] or 0) >= 960 else resultat['en_ligne']}**",
              f"- Nouvelles annonces par jour : **{par_jour if par_jour is not None else '—'}**",
              f"- Prix pratiqués : bas {euros(resultat['prix_bas'])} · moyen **{euros(resultat['prix_median'])}**"
              f" · haut {euros(resultat['prix_haut'])}",
              f"- « J'aime » par jour et par annonce : **{fav_moy if fav_moy is not None else '—'}**"
              + (f" (moyenne Vinted : {ref_fav:.2f})" if ref_fav else ""),
              f"- Annonces avec un badge de demande : {part_demande if part_demande is not None else '—'} %",
              f"- Marques les plus présentes : " + ", ".join(f"{m} ({n})" for m, n in marques), ""]
    if h:
        lignes += ["### Ventes déjà repérées par l'outil",
                   f"- Annonces correspondantes vues : {h['n']} · vendues : **{h['vendues']}**",
                   f"- % vendu : 24 h {h['taux_24h'] or '—'} % · 3 j {h['taux_3j'] or '—'} % · 7 j {h['taux_7j'] or '—'} %"
                   + (f" (moyenne Vinted 7 j : {ref_taux} %)" if ref_taux else ""),
                   f"- Prix de vente réel (médian) : **{euros(h['prix_vendu'])}**", ""]
    lignes += ["### Prix conseillé",
               f"- Pour vendre vite : **{euros(resultat['prix_bas'])}** · prix normal : {euros(h['prix_vendu'] if h and h.get('prix_vendu') else resultat['prix_median'])}",
               "", "### Annonces les plus aimées"]
    for c in plus_aimees:
        lignes.append(f"- [{c['titre']}](https://www.vinted.fr{c['url']}) — {euros(c['prix'])} — "
                      f"{c['favoris0']} ♥ en {c['age_h'] / 24:.1f} j")
    lignes += ["", f"📈 Les {len(a_suivre)} annonces les plus récentes de ce produit sont maintenant **suivies** : "
               f"% vendu, délai et prix de vente réels s'afficheront dans les prochains jours sur le "
               f"[tableau de bord]({TABLEAU}) (onglet « Recherche »)."]
    with open("commentaire.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lignes))
    print("\n".join(lignes))


if __name__ == "__main__":
    main()
