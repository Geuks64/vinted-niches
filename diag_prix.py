"""DIAGNOSTIC (lecture seule) : où se trouve le prix sur la page d'une annonce VENDUE ?

Le prix des annonces vendues n'est pas relevé (le bloc « faire une offre » disparaît à la vente).
Lit 5 annonces vendues du rattrapage sans prix (5 requêtes, jamais pendant la collecte d'un relevé) et
affiche les extraits de page qui parlent de prix. Rien n'est écrit dans la base.
"""
import re
import sqlite3

import collecte
from diag_catalogue import attendre_fenetre

MOTIFS = [r'"price"', r'"amount"', r'price:amount', r'"total_item_price"', r'"priceCurrency"', r'currency_code',
          r'"service_fee"', r'"original_price', r'"price_numeric"']


def main():
    con = sqlite3.connect(collecte.DB)
    lignes = con.execute("""SELECT id, url, statut FROM rattrapage WHERE statut IN ('vendu', 'vendu_probable')
                            AND prix IS NULL ORDER BY verifie_le DESC LIMIT 5""").fetchall()
    n = lambda sql: con.execute(sql).fetchone()[0]
    vendu = "statut IN ('vendu', 'vendu_probable')"
    print(f"Rattrapage : {n(f'SELECT COUNT(*) FROM rattrapage WHERE {vendu} AND prix IS NULL')} annonces vendues "
          f"sans prix sur {n(f'SELECT COUNT(*) FROM rattrapage WHERE {vendu}')}")
    print(f"Suivi : {n(f'SELECT COUNT(*) FROM annonces WHERE {vendu} AND COALESCE(prix_final, prix) IS NULL')} "
          f"annonces vendues sans prix sur {n(f'SELECT COUNT(*) FROM annonces WHERE {vendu}')}")
    attendre_fenetre()
    v = collecte.Vinted()
    v.get("/")
    for iid, url, statut in lignes:
        r = v.get(url or f"/items/{iid}")
        print(f"\n===== annonce {iid} ({statut}) : HTTP {getattr(r, 'status_code', '—')}")
        if r is None or r.status_code != 200:
            continue
        html, f = r.text, collecte.flux_next(r.text)
        for nom, texte in (("HTML", html), ("FLUX", f)):
            vus = 0
            for motif in MOTIFS:
                for m in re.finditer(motif, texte):
                    print(f"  [{nom}] …{texte[max(0, m.start() - 60):m.end() + 110]!r}")
                    vus += 1
                    if vus >= 14:
                        break
                if vus >= 14:
                    break
        for m in re.finditer(r'<meta[^>]+(price|amount)[^>]*>', html):
            print("  [META]", m.group(0)[:200])


if __name__ == "__main__":
    main()
