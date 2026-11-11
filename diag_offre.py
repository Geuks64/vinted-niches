"""DIAGNOSTIC (lecture seule, AUCUNE requête Vinted) : réglages actuels vs réglages proposés pour choisir les
produits candidats du pilote P2 (découverte par l'offre). Rien n'est modifié dans la base ni dans le pilote.

Réglages actuels : 2 ou 3 mots, ≥ 4 vendeurs différents.
Réglages proposés : 3 mots obligatoires, ≥ 6 vendeurs différents, titres contenant une marque connue écartés
(marques = valeurs « marque » des annonces DE MARQUE déjà vues, au moins 3 fois).
"""
import sqlite3
import time
from collections import Counter

import produits
import zoom
from cible import sans_marque, simplifier


def marques_connues(con):
    c = Counter()
    for marque, titre in con.execute("SELECT marque, titre FROM annonces WHERE marque IS NOT NULL"):
        if not sans_marque(marque, titre):
            for m in produits.mots(marque):
                if len(m) > 2:
                    c[m] += 1
    return {m for m, n in c.items() if n >= 3}


def afficher(titre, liste):
    print(f"\n=== {titre} : {len(liste)} candidats")
    for g in liste[:25]:
        print(f"  - « {g['requete']} » : {g['vendeurs']} vendeurs, {g['annonces']} annonces, {g['prix']} € "
              f"({g['categorie']}) | ex. « {g['titre'][:60]} »")


def main():
    con = sqlite3.connect(zoom.collecte.DB)
    maintenant = int(time.time())
    zoom.OUBLI_J = 0                          # ignorer l'historique des recherches : comparer sur toute l'offre
    idf, idf_max, index = zoom.calculer_idf(con)
    n = con.execute("SELECT COUNT(*), COUNT(DISTINCT vendeur) FROM zoom_offre WHERE vu_le > ?",
                    (maintenant - zoom.FENETRE_OFFRE_H * 3600,)).fetchone()
    print(f"Offre analysée : {n[0]} annonces neuves sans marque, {n[1]} vendeurs (72 h)")

    actuels = zoom.candidats(con, maintenant, idf, idf_max, index)
    afficher("RÉGLAGES ACTUELS (2-3 mots, ≥ 4 vendeurs)", actuels)

    marques = marques_connues(con)
    print(f"\nMarques connues (tirées des annonces de marque) : {len(marques)}")
    zoom.VENDEURS_MIN = 6
    proposes, ecartes = [], Counter()
    for g in zoom.candidats(con, maintenant, idf, idf_max, index):
        if len(g["coeur"]) < 3:
            ecartes["moins de 3 mots"] += 1
            continue
        tokens = set(g["coeur"]) | produits.mots(g["titre"])
        trouve = sorted(tokens & marques)
        if trouve:
            ecartes[f"marque ({', '.join(trouve[:2])})"] += 1
            continue
        proposes.append(g)
    afficher("RÉGLAGES PROPOSÉS (3 mots, ≥ 6 vendeurs, sans marque connue)", proposes)
    print("  écartés par les nouveaux réglages :", dict(ecartes))


if __name__ == "__main__":
    main()
