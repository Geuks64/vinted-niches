"""DIAGNOSTIC (lecture seule, AUCUNE requête Vinted) : le volume actuel suffit-il pour des produits précis ?

Regroupe les annonces neuves sans marque déjà observées (produits.py) et affiche :
  - combien d'annonces ont un résultat connu (vendue / encore en vente) et combien de produits en sortent,
  - la taille des produits (nombre d'annonces observées par produit),
  - des exemples de produits, de produits « similaires » et d'annonces « incertaines » pour juger la qualité,
  - le rythme d'accumulation (annonces ciblées par jour) pour estimer quand un produit atteint 15 annonces.
"""
import sqlite3
import time
from collections import Counter

import produits
import stats
from cible import est_cible


def main():
    con = sqlite3.connect(stats.DB)
    con.row_factory = sqlite3.Row
    maintenant = int(time.time())
    suivies = [dict(r) for r in con.execute("SELECT * FROM annonces WHERE suivi = 1")]
    ratt = [dict(r) for r in con.execute("SELECT * FROM rattrapage WHERE statut != 'supprime'")]
    s_c = [a for a in suivies if est_cible(a)]
    r_c = [a for a in ratt if est_cible(a)]
    utiles_s = [a for a in s_c if a["n_controles"] > 0 and a["statut"] not in stats.EXCLUS]
    utiles_r = [a for a in r_c if a["statut"] in ("en_vente", "reserve") + stats.ETATS_VENDU]
    for a in utiles_r:
        a["_ratt"] = 1
    print(f"Annonces neuves sans marque : {len(s_c)} suivies ({len(utiles_s)} déjà contrôlées, catégorie connue), "
          f"{len(r_c)} rattrapage ({len(utiles_r)} avec un état exploitable)")
    tous = utiles_s + utiles_r
    liste, bilan, idf = produits.regrouper(tous)
    print("Bilan du regroupement :", dict(bilan))
    tailles = Counter(len(p.annonces) for p in liste)
    print(f"Produits : {len(liste)} | catégories : {len({p.categorie for p in liste})}")
    for seuil in (1, 2, 3, 5, 8, 15, 30):
        print(f"  produits avec ≥ {seuil:>2} annonces : {sum(1 for p in liste if len(p.annonces) >= seuil)}")
    print("  répartition (taille: nombre de produits) :", dict(sorted(tailles.items())[:12]))

    gros = sorted(liste, key=lambda p: -len(p.annonces))[:15]
    print("\n=== 15 plus gros produits (annonces / ventes probables+confirmées / prix médian)")
    for p in gros:
        st = stats.segment_stats([a for a in p.annonces if "_ratt" not in a], maintenant,
                                 [a for a in p.annonces if "_ratt" in a])
        print(f"- {len(p.annonces):>3} ann. | {st['vendues']:>2} ventes | {p.prix} € | {p.categorie.split(' > ')[-1]} | "
              f"« {produits.nom(p, idf)[:60]} » | cœur {sorted(p.coeur)[:6]} | incertaines {p.incertains}")
        for a in p.annonces[:4]:
            print(f"      · {str(a['titre'])[:70]} ({a.get('prix')} €)")
        for s, q in p.similaires[:2]:
            print(f"      ~ similaire ({s}) : « {produits.nom(q, idf)[:60]} » ({len(q.annonces)} ann.)")

    # Rythme d'accumulation des annonces ciblées (suivies, par jour de première observation)
    jours = Counter(time.strftime("%Y-%m-%d", time.gmtime(a["vu_le"])) for a in s_c)
    print("\nAnnonces ciblées suivies par jour :", dict(sorted(jours.items())))


if __name__ == "__main__":
    main()
