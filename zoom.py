"""P2 — PILOTE « zoom automatique » (autorisé par l'utilisateur le 2026-11-09, quota 10 requêtes par relevé).

But : obtenir assez d'annonces d'un MÊME produit pour mesurer ses ventes, sans requête supplémentaire.

Fonctionnement (à chaque relevé, AVANT le rattrapage, qui perd autant de lectures que P2 en a utilisé) :
  1. Candidats (0 requête) : annonces neuves sans marque déjà vues VENDUES (relevé ou rattrapage), titre précis
     (≥ 2 mots informatifs), prix ≥ PRIX_MIN. Requête = 2 à 4 mots les plus caractéristiques du titre.
  2. Sondage (1 requête) : 1 page de recherche Vinted « Neuf ». On garde seulement les annonces IDENTIQUES au
     produit (score ≥ 0,60, ≥ 2 mots informatifs communs, prix compatible, sans marque). Abandon si moins de 30 %
     de résultats identiques, moins de 10 annonces identiques, ou prix médian trop bas.
  3. Passages à 24 h, 3 j, 7 j : la recherche est relue (≤ 3 pages). Une annonce suivie ABSENTE n'est JAMAIS
     comptée vendue : elle passe « disparue » et sa page est lue individuellement -> vendue (probable),
     supprimée, masquée, réservée, encore en vente (fausse disparition) ou indéterminée.
     Produit avec plus de 288 annonces (liste impossible à relire en entier) : mode « échantillon »
     (15 annonces tirées au hasard, lues directement à chaque passage).
  4. Vente probable -> lecture de confirmation 12 h plus tard (vendue confirmée / annulée).
Sécurité :
  - jamais plus de QUOTA requêtes par relevé (compteur), quota fixe : aucun passage automatique à 20 ou 30 ;
  - 403, 429, erreur réseau ou réponse inhabituelle -> P2 s'arrête pour ce relevé ; 403/429 -> pause de 6 h ;
  - découverte de nouveaux produits pendant 3 jours seulement ; les produits déjà suivis terminent leurs 7 jours.
NICHES_ZOOM=0 coupe P2 : la collecte redevient exactement celle d'avant (le rattrapage récupère tout son budget).
"""
import json
import math
import os
import random
import re
import time
from collections import Counter
from urllib.parse import quote_plus

from itertools import combinations

import collecte
import produits
from cible import CATEGORIES_EXCLUES, est_cible, simplifier

QUOTA = int(os.environ.get("NICHES_ZOOM", "10"))
DECOUVERTE_J = 3                  # jours pendant lesquels de nouveaux produits sont sondés
PASSAGES_H = [24, 72, 168]        # relectures de la recherche après le sondage
PRIX_MIN = 8.0                    # prix de vente minimum d'un candidat (sinon marge impossible)
PART_IDENTIQUE_MIN = 0.30
N_IDENTIQUES_MIN = 10
PAGES_MAX = 3                     # au-delà de 3 pages (288 annonces) : mode « échantillon »
ECHANTILLON = 15
VERIF_PAR_PRODUIT = 5             # vérifications d'annonces disparues par produit et par relevé (au plus)
PART_RACINE_MAX = 0.25            # une catégorie principale : au plus 25 % des sondages sur 24 h
OUBLI_J = 14                      # une requête (ou une requête presque identique) n'est pas refaite avant 14 j
PAUSE_BLOCAGE_H = 6
CONFIRMATION_H = 12
VENDEUR_MAX = 3                   # annonces d'un même vendeur dans un produit (au plus)
log = collecte.log


def creer_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS zoom_etat (cle TEXT PRIMARY KEY, valeur TEXT);
    CREATE TABLE IF NOT EXISTS zoom_produits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requete TEXT, coeur TEXT, categorie TEXT, racine TEXT,
        graine INTEGER, graine_titre TEXT, graine_prix REAL,
        cree_le INTEGER, statut TEXT,          -- suivi / abandonne / termine
        raison TEXT, mode TEXT,                -- liste / echantillon
        total INTEGER, n_cartes INTEGER, n_identiques INTEGER, part_identique REAL, prix_median REAL,
        n_passages INTEGER DEFAULT 0, prochain_passage INTEGER
    );
    CREATE TABLE IF NOT EXISTS zoom_annonces (
        produit_id INTEGER, annonce_id INTEGER,
        titre TEXT, prix REAL, vendeur TEXT, score REAL, miniature TEXT, url TEXT,
        premiere_vue INTEGER, derniere_vue INTEGER, age_h_initial REAL,
        statut TEXT,          -- en_vente / disparue / a_verifier / vendu_probable / vendu / supprime / masque /
                              -- reserve / indisponible / indetermine / autre_categorie
        disparue_le INTEGER, verifie_le INTEGER, prix_vente REAL, categorie TEXT,
        confirmation_prevue INTEGER, fausse_disparition INTEGER DEFAULT 0,
        PRIMARY KEY (produit_id, annonce_id)
    );
    CREATE TABLE IF NOT EXISTS zoom_journal (
        ts INTEGER, produit_id INTEGER, type TEXT, http INTEGER, note TEXT
    );
    """)


def etat(con, cle, defaut=None):
    r = con.execute("SELECT valeur FROM zoom_etat WHERE cle = ?", (cle,)).fetchone()
    return r[0] if r else defaut


def fixer(con, cle, valeur):
    con.execute("INSERT OR REPLACE INTO zoom_etat VALUES (?, ?)", (cle, str(valeur)))


# ---------------------------------------------------------------- budget et sécurité
class Budget:
    """Toutes les requêtes P2 passent par ici : quota strict, arrêt propre au moindre signal anormal."""

    def __init__(self, v, con, quota):
        self.v, self.con, self.quota = v, con, quota
        self.depart = v.requetes
        self.arret = None

    def utilise(self):
        return self.v.requetes - self.depart

    def reste(self):
        return 0 if self.arret else self.quota - self.utilise()

    def stop(self, raison, pause=False):
        if not self.arret:
            self.arret = raison
            log(f"  P2 arrêté pour ce relevé : {raison}" + (f" (pause de {PAUSE_BLOCAGE_H} h)" if pause else ""))
            if pause:
                fixer(self.con, "pause_jusqua", int(time.time()) + PAUSE_BLOCAGE_H * 3600)

    def get(self, chemin):
        if self.reste() <= 0 or self.v.bloque() or collecte.temps_restant() < 60:
            return None
        r = self.v.get(chemin)
        if r is None:
            self.stop("erreur réseau")
            return None
        if r.status_code in (403, 429):
            self.stop(f"HTTP {r.status_code}", pause=True)
            return None
        if r.status_code not in (200, 404, 410):
            self.stop(f"réponse inhabituelle HTTP {r.status_code}")
            return None
        return r


# ---------------------------------------------------------------- mots, similarité
def mots_ordonnes(titre):
    """Mots informatifs dans l'ordre du titre (mêmes règles que produits.mots)."""
    vus, res = set(), []
    for brut in re.findall(r"[a-z0-9]+", simplifier(titre)):
        for m in produits.mots(brut):
            if m not in vus:
                vus.add(m)
                res.append(m)
    return res


def calculer_idf(con):
    """Rareté des mots (IDF, sur tous les titres) + index mot -> titres NEUFS SANS MARQUE où il apparaît (pour
    trouver les mots qui reviennent parmi les produits de type « Chine », pas parmi les vêtements de marque)."""
    lignes = con.execute("""SELECT titre, marque, etat, categorie FROM annonces WHERE suivi = 1 UNION ALL
                            SELECT titre, marque, etat, categorie FROM rattrapage WHERE titre IS NOT NULL""").fetchall()
    titres = [r[0] for r in lignes]
    index, tous = {}, {}
    for i, (t, marque, etat_, cat) in enumerate(lignes):
        cible_ok = est_cible({"titre": t, "marque": marque, "etat": etat_, "categorie": cat})
        for m in produits.mots(t):
            tous.setdefault(m, set()).add(i)
            if cible_ok:
                index.setdefault(m, set()).add(i)
    total = max(1, len(titres))
    idf = {m: math.log(1 + total / len(ids)) for m, ids in tous.items()}
    return idf, math.log(1 + total), index


def score(jeu, coeur, idf, idf_max):
    """Jaccard pondéré ; un mot inconnu compte comme très rare (une annonce avec des mots en plus est pénalisée)."""
    if not jeu or not coeur:
        return 0.0
    poids = lambda m: idf.get(m, idf_max)
    union = sum(poids(m) for m in jeu | coeur)
    return sum(poids(m) for m in jeu & coeur) / union if union else 0.0


SEUIL_INFO = math.log(1 + 20)    # mot présent dans moins de ~5 % des titres


def identique(carte, coeur, prix_ref, idf, idf_max, raisons=None):
    """(est identique, part des mots du produit présents) — prudent : au moindre doute, NON.
    Identique = neuf + sans marque + TOUS les mots caractéristiques du produit dans le titre + prix compatible.
    (Corrigé le 2026-11-09 : le score de Jaccard pénalisait les mots inconnus — couleur, taille… — et rejetait
    des annonces qui contenaient pourtant tous les mots du produit.)"""
    raisons = raisons if raisons is not None else Counter()
    if not est_cible(carte):                       # neuf + sans marque
        raisons["marque ou état"] += 1
        return False, 0.0
    jeu = produits.mots(carte.get("titre"))
    part = len(jeu & coeur) / len(coeur) if coeur else 0.0
    if part < 1:
        raisons["mots manquants"] += 1
        return False, round(part, 3)
    if carte.get("prix") and prix_ref:
        r = carte["prix"] / prix_ref
        if not 1 / produits.ECART_PRIX_MAX <= r <= produits.ECART_PRIX_MAX:
            raisons["prix trop différent"] += 1
            return False, 1.0
    return True, 1.0


# ---------------------------------------------------------------- lecture d'une page de recherche
def lire_resultats(html):
    """(cartes, vendeurs {annonce: vendeur ou None}, total annoncé par Vinted)."""
    cartes = collecte.lire_cartes(html)
    f = collecte.flux_next(html)
    vendeurs = {}
    for c in collecte.objets_apres(f, "productItem"):
        if isinstance(c, dict) and c.get("id"):
            u = c.get("user") if isinstance(c.get("user"), dict) else {}
            vid = u.get("id") or u.get("userId") or u.get("login")
            vendeurs[int(c["id"])] = None if vid in (None, "", "$undefined") else str(vid)
    m = re.search(r'"total_entries":\s*(\d+)', f)
    return cartes, vendeurs, (int(m.group(1)) if m else None)


def chemin_recherche(requete, page):
    return f"/catalog?search_text={quote_plus(requete)}&order=newest_first&{collecte.FILTRE_NEUF}&page={page}"


def journal(con, produit_id, type_, http, note=""):
    con.execute("INSERT INTO zoom_journal VALUES (?,?,?,?,?)", (int(time.time()), produit_id, type_, http, note))


# ---------------------------------------------------------------- 1. candidats (aucune requête)
def candidats(con, maintenant, idf, idf_max, index):
    recents = con.execute("SELECT requete, coeur FROM zoom_produits WHERE cree_le > ?",
                          (maintenant - OUBLI_J * 86400,)).fetchall()
    deja = {r[0] for r in recents}
    coeurs_deja = [set(json.loads(r[1] or "[]")) for r in recents]
    graines = []
    for r in con.execute("""SELECT id, titre, marque, etat, categorie, COALESCE(prix_final, prix) AS prix,
                                   vendu_avant_h AS vitesse_h, favoris, 'suivi' AS source
                            FROM annonces WHERE suivi = 1 AND statut IN ('vendu', 'vendu_probable')
                            AND categorie IS NOT NULL
                            UNION ALL
                            SELECT id, titre, marque, etat, categorie, prix, age_h, favoris, 'rattrapage'
                            FROM rattrapage WHERE statut IN ('vendu', 'vendu_probable') AND categorie IS NOT NULL"""):
        a = dict(zip(("id", "titre", "marque", "etat", "categorie", "prix", "vitesse_h", "favoris", "source"), r))
        if not est_cible(a) or (a["prix"] or 0) < PRIX_MIN:
            continue
        # mots caractéristiques (rares) mais qui REVIENNENT dans au moins 2 titres (pas les mots uniques à cette
        # annonce, qui donnaient des recherches sans résultat) ; corrigé le 2026-11-09
        ordre = mots_ordonnes(a["titre"])
        info = [m for m in ordre if idf.get(m, idf_max) >= SEUIL_INFO and len(index.get(m, ())) >= 2]
        meilleurs = sorted(info, key=lambda m: -idf.get(m, idf_max))[:5]
        garder = None
        for k in (3, 2):                           # combinaison de 3 (sinon 2) mots présents ENSEMBLE dans ≥ 2 titres
            for combi in combinations(meilleurs, k):
                if len(set.intersection(*(index[m] for m in combi))) >= 2:
                    garder = set(combi)
                    break
            if garder:
                break
        if not garder:
            continue
        requete = " ".join(m for m in ordre if m in garder)
        # cœur du produit = les mots de la requête (corrigé le 2026-11-09 : tout le titre de l'annonce d'origine
        # rendait la comparaison trop stricte, 0 à 3 % de résultats « identiques » au 1er relevé du pilote)
        coeur = set(garder)
        if requete in deja or any(score(coeur, c, idf, idf_max) >= produits.SEUIL_IDENTIQUE for c in coeurs_deja):
            continue
        v = a["vitesse_h"]
        priorite = (3 if v is not None and v <= 24 else 2 if v is not None and v <= 72 else 1) + \
            min(2.0, (a["favoris"] or 0) / 10)
        graines.append({**a, "requete": requete, "coeur": sorted(coeur), "priorite": priorite,
                        "racine": a["categorie"].split(" > ")[0]})
        deja.add(requete)
        coeurs_deja.append(coeur)
    return graines


def choisir(con, graines, maintenant):
    """Tour de rôle : la catégorie principale la moins sondée sur 24 h passe d'abord ; une catégorie au-delà de
    25 % des sondages est écartée TANT QU'une autre catégorie a des candidats (corrigé le 2026-11-09 : la règle
    stricte bloquait tout dès 5 sondages)."""
    par_racine = Counter(r[0] for r in con.execute("SELECT racine FROM zoom_produits WHERE cree_le > ?",
                                                   (maintenant - 86400,)))
    total = sum(par_racine.values())
    ok = [g for g in graines if (par_racine[g["racine"]] + 1) / (total + 1) <= PART_RACINE_MAX]
    if not ok:                                    # toutes au-dessus : la moins sondée (tour de rôle)
        ok = graines
    random.shuffle(ok)
    ok.sort(key=lambda g: (par_racine[g["racine"]], -g["priorite"]))
    return ok[0] if ok else None


# ---------------------------------------------------------------- 2. sondage (1 requête)
def ajouter_annonces(con, pid, cartes, vendeurs, coeur, prix_ref, idf, idf_max, maintenant, age_de, limite=None):
    """Ajoute les annonces IDENTIQUES (déjà suivies : ignorées). Même vendeur : au plus VENDEUR_MAX ; même vendeur
    + même titre + même prix : une seule (republication)."""
    deja = con.execute("SELECT annonce_id, vendeur, titre, prix FROM zoom_annonces WHERE produit_id = ?",
                       (pid,)).fetchall()
    ids = {r[0] for r in deja}
    par_vendeur = Counter(r[1] for r in deja if r[1])
    signatures = {(r[1], simplifier(r[2]), r[3]) for r in deja if r[1]}
    ajout = 0
    for c in cartes:
        ok, s = identique(c, coeur, prix_ref, idf, idf_max)
        if not ok or c["id"] in ids:
            continue
        vend = vendeurs.get(c["id"])
        if vend:
            sig = (vend, simplifier(c["titre"]), c["prix"])
            if par_vendeur[vend] >= VENDEUR_MAX or sig in signatures:
                continue
            par_vendeur[vend] += 1
            signatures.add(sig)
        con.execute("""INSERT OR IGNORE INTO zoom_annonces (produit_id, annonce_id, titre, prix, vendeur, score,
            miniature, url, premiere_vue, derniere_vue, age_h_initial, statut)
            VALUES (?,?,?,?,?,?,?,?,?,?,?, 'en_vente')""",
            (pid, c["id"], c["titre"], c["prix"], vend, s, c["miniature"], c["url"], maintenant, maintenant,
             age_de(c["id"])))
        ids.add(c["id"])
        ajout += 1
        if limite and ajout >= limite:
            break
    return ajout


def sonder(b, con, g, idf, idf_max):
    maintenant = int(time.time())
    r = b.get(chemin_recherche(g["requete"], 1))
    if r is None:
        return False
    cartes, vendeurs, total = lire_resultats(r.text) if r.status_code == 200 else ([], {}, 0)
    coeur = set(g["coeur"])
    raisons = Counter()
    idents = [c for c in cartes if identique(c, coeur, g["prix"], idf, idf_max, raisons)[0]]
    part = len(idents) / len(cartes) if cartes else 0.0
    prix_med = produits.mediane([c["prix"] for c in idents])
    total = total if total is not None else len(cartes)
    raison = ("aucun résultat" if not cartes else
              f"résultats trop variés ({round(100 * part)} % identiques)" if part < PART_IDENTIQUE_MIN else
              f"trop rare ({len(idents)} annonces identiques)" if len(idents) < N_IDENTIQUES_MIN else
              f"prix trop bas ({prix_med} €)" if (prix_med or 0) < PRIX_MIN else None)
    mode = "liste" if total <= 96 * PAGES_MAX else "echantillon"
    cur = con.execute("""INSERT INTO zoom_produits (requete, coeur, categorie, racine, graine, graine_titre,
        graine_prix, cree_le, statut, raison, mode, total, n_cartes, n_identiques, part_identique, prix_median,
        prochain_passage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (g["requete"], json.dumps(sorted(coeur)), g["categorie"], g["racine"], g["id"], g["titre"], g["prix"],
         maintenant, "abandonne" if raison else "suivi", raison, mode, total, len(cartes), len(idents),
         round(part, 3), prix_med, None if raison else maintenant + PASSAGES_H[0] * 3600))
    pid = cur.lastrowid
    journal(con, pid, "sondage", r.status_code, raison or "suivi")
    if not raison:
        age_de = lambda iid: round(collecte.id_vers_age(con, iid, maintenant), 1)
        if mode == "echantillon":
            random.shuffle(idents)
        ajout = ajouter_annonces(con, pid, idents, vendeurs, coeur, g["prix"], idf, idf_max, maintenant, age_de,
                                 limite=ECHANTILLON if mode == "echantillon" else None)
        vus = sum(1 for c in cartes if vendeurs.get(c["id"]))
        log(f"  P2 sondage « {g['requete']} » ({g['categorie']}) : {total} en ligne, {len(idents)}/{len(cartes)} "
            f"identiques, prix médian {prix_med} € -> SUIVI ({mode}, {ajout} annonces ; vendeur lisible sur "
            f"{vus}/{len(cartes)} cartes)")
    else:
        log(f"  P2 sondage « {g['requete']} » ({g['categorie']}) : {total} en ligne -> abandonné : {raison}"
            + (f" | écartées : {dict(raisons)}" if raisons else ""))
    con.commit()
    return True


# ---------------------------------------------------------------- 3. passages et vérifications
def passer(b, con, p, idf, idf_max):
    """Relit la recherche (mode liste) ; une annonce absente devient « disparue » (à vérifier, PAS vendue)."""
    pid, requete, coeur, prix_ref, total, mode, n = p
    maintenant = int(time.time())
    coeur = set(json.loads(coeur))
    if mode == "liste":
        pages = max(1, min(PAGES_MAX, math.ceil((total or 1) / 96)))
        presents, cartes_tout, vendeurs_tout = set(), [], {}
        for page in range(1, pages + 1):
            r = b.get(chemin_recherche(requete, page))
            if r is None:
                return False                         # liste incomplète : on ne conclut rien, passage reporté
            cartes, vendeurs, _ = lire_resultats(r.text) if r.status_code == 200 else ([], {}, 0)
            presents |= {c["id"] for c in cartes}
            cartes_tout += cartes
            vendeurs_tout.update(vendeurs)
            if len(cartes) < 90:
                break
        suivies = con.execute("SELECT annonce_id FROM zoom_annonces WHERE produit_id = ? AND statut = 'en_vente'",
                              (pid,)).fetchall()
        absentes = [a for (a,) in suivies if a not in presents]
        con.executemany("UPDATE zoom_annonces SET statut = 'disparue', disparue_le = ? WHERE produit_id = ? "
                        "AND annonce_id = ?", [(maintenant, pid, a) for a in absentes])
        con.execute(f"""UPDATE zoom_annonces SET derniere_vue = ? WHERE produit_id = ? AND annonce_id IN
                        ({",".join("?" * len(presents)) or "NULL"})""", (maintenant, pid, *presents))
        # nouvelles annonces identiques publiées depuis (concurrence) : suivies elles aussi
        age_de = lambda iid: round(collecte.id_vers_age(con, iid, maintenant), 1)
        nouvelles = ajouter_annonces(con, pid, cartes_tout, vendeurs_tout, coeur, prix_ref, idf, idf_max,
                                     maintenant, age_de)
        note = f"{len(presents)} présentes, {len(absentes)} disparues (à vérifier), {nouvelles} nouvelles"
    else:
        # échantillon : chaque annonce encore en vente sera lue directement
        n_a = con.execute("UPDATE zoom_annonces SET statut = 'a_verifier' WHERE produit_id = ? AND statut = 'en_vente'",
                          (pid,)).rowcount
        note = f"échantillon : {n_a} annonces à lire"
    n += 1
    prochain = con.execute("SELECT cree_le FROM zoom_produits WHERE id = ?", (pid,)).fetchone()[0] + \
        PASSAGES_H[n] * 3600 if n < len(PASSAGES_H) else None
    con.execute("UPDATE zoom_produits SET n_passages = ?, prochain_passage = ?, statut = ? WHERE id = ?",
                (n, prochain, "suivi" if prochain else "termine", pid))
    journal(con, pid, "passage", 200, note)
    log(f"  P2 passage {n} « {requete} » : {note}")
    con.commit()
    return True


def verifier(b, con, pid, aid, ancien, categorie_produit):
    """Lit la page d'UNE annonce et note son état réel (jamais « vendue » sans signature de vente)."""
    maintenant = int(time.time())
    r = b.get(f"/items/{aid}")
    if r is None:
        return False
    if r.status_code in (404, 410) or "/items/" not in str(getattr(r, "url", "") or "/items/"):
        statut, cat, prix = "supprime", None, None
        if ancien == "vendu_probable":
            statut = "vendu_probable"                # supprimée après une vente probable : reste probable
        collecte.observer(con, aid, "zoom", r.status_code, "supprime")
    else:
        brut, cat, _, d = collecte.lire_annonce(r.text)
        prix = d["prix_eur"]
        lisible = "can_buy" in brut or "availability" in brut
        s = collecte.statut_depuis(brut) if lisible else "indetermine"
        collecte.observer(con, aid, "zoom", r.status_code, s, None, brut, brut.get("favourite_count"), prix, d["devise"])
        if ancien == "vendu_probable":               # lecture de confirmation
            statut = "vendu" if s in collecte.ETATS_VENDU else ("en_vente" if s in ("en_vente", "reserve") else ancien)
        else:
            statut = s
        if cat and categorie_produit and cat != categorie_produit and not cat.startswith(CATEGORIES_EXCLUES):
            if statut not in ("supprime",):
                statut = "autre_categorie"           # prudence : une autre catégorie = pas le même produit
    fausse = 1 if ancien == "disparue" and statut in ("en_vente", "reserve") else 0
    con.execute("""UPDATE zoom_annonces SET statut = ?, verifie_le = ?, categorie = COALESCE(?, categorie),
        prix_vente = CASE WHEN ? IN ('vendu', 'vendu_probable') THEN COALESCE(?, prix_vente, prix) ELSE prix_vente END,
        confirmation_prevue = CASE WHEN ? = 'vendu_probable' AND ? != 'vendu_probable' THEN ? ELSE NULL END,
        fausse_disparition = fausse_disparition + ?,
        derniere_vue = CASE WHEN ? IN ('en_vente', 'reserve') THEN ? ELSE derniere_vue END
        WHERE produit_id = ? AND annonce_id = ?""",
        (statut, maintenant, cat, statut, prix, statut, ancien, maintenant + CONFIRMATION_H * 3600, fausse,
         statut, maintenant, pid, aid))
    journal(con, pid, "confirmation" if ancien == "vendu_probable" else "verification", r.status_code,
            f"{aid}: {ancien} -> {statut}")
    return True


# ---------------------------------------------------------------- relevé
def executer(v, con):
    """Appelé par collecte.main() avant le rattrapage. Renvoie le nombre de requêtes utilisées."""
    creer_tables(con)
    if QUOTA <= 0:
        return 0
    maintenant = int(time.time())
    if etat(con, "debut") is None:
        fixer(con, "debut", maintenant)
        con.commit()
    debut = int(etat(con, "debut"))
    pause = int(etat(con, "pause_jusqua", 0) or 0)
    if pause > maintenant:
        log(f"P2 en pause (blocage récent) jusqu'à {time.strftime('%H:%M', time.gmtime(pause))} UTC : 0 requête")
        return 0
    # produits d'une catégorie exclue après coup (ex. jeux vidéo, 2026-11-09) : suivi arrêté, aucune requête
    for prefixe in CATEGORIES_EXCLUES:
        con.execute("""UPDATE zoom_produits SET statut = 'abandonne', raison = 'catégorie exclue', prochain_passage = NULL
                       WHERE statut = 'suivi' AND categorie LIKE ?""", (prefixe + "%",))
    b = Budget(v, con, QUOTA)
    idf, idf_max, index = calculer_idf(con)
    cats = dict(con.execute("SELECT id, categorie FROM zoom_produits"))

    # a) confirmations dues (ventes probables vues il y a 12 h)
    for pid, aid in con.execute("""SELECT produit_id, annonce_id FROM zoom_annonces WHERE statut = 'vendu_probable'
            AND confirmation_prevue IS NOT NULL AND confirmation_prevue <= ? ORDER BY confirmation_prevue""",
                                (maintenant,)).fetchall():
        if b.reste() <= 0 or not verifier(b, con, pid, aid, "vendu_probable", cats.get(pid)):
            break
    # b) annonces disparues / à lire (au plus VERIF_PAR_PRODUIT par produit et par relevé)
    par_produit = Counter()
    for pid, aid, st in con.execute("""SELECT produit_id, annonce_id, statut FROM zoom_annonces
            WHERE statut IN ('disparue', 'a_verifier') ORDER BY COALESCE(disparue_le, premiere_vue)""").fetchall():
        if b.reste() <= 0:
            break
        if par_produit[pid] >= VERIF_PAR_PRODUIT:
            continue
        par_produit[pid] += 1
        if not verifier(b, con, pid, aid, st, cats.get(pid)):
            break
    con.commit()
    # c) passages dus
    for p in con.execute("""SELECT id, requete, coeur, graine_prix, total, mode, n_passages FROM zoom_produits
            WHERE statut = 'suivi' AND prochain_passage <= ? ORDER BY prochain_passage""", (maintenant,)).fetchall():
        besoin = 1 if p[5] == "echantillon" else max(1, min(PAGES_MAX, math.ceil((p[4] or 1) / 96)))
        if b.reste() < besoin or not passer(b, con, p, idf, idf_max):
            break
    # d) nouveaux sondages (pendant les 3 jours de découverte seulement)
    graines = []
    if maintenant < debut + DECOUVERTE_J * 86400:
        graines = candidats(con, maintenant, idf, idf_max, index)
        while b.reste() > 0 and graines:
            g = choisir(con, graines, int(time.time()))
            if g is None:
                break
            graines.remove(g)
            if not sonder(b, con, g, idf, idf_max):
                break
    con.commit()
    utilise = b.utilise()
    if utilise > QUOTA:                               # ne doit jamais arriver : alarme + pause
        log(f"ALERTE P2 : {utilise} requêtes > quota {QUOTA} -> pause")
        fixer(con, "pause_jusqua", int(time.time()) + PAUSE_BLOCAGE_H * 3600)
    log(f"P2 : {utilise}/{QUOTA} requêtes utilisées"
        + (f" | {len(graines)} candidats en attente" if graines else "")
        + (f" | arrêt : {b.arret}" if b.arret else ""))
    return utilise


# ---------------------------------------------------------------- bilan du pilote (lecture seule, 0 requête)
def bilan(con):
    creer_tables(con)
    if not con.execute("SELECT 1 FROM zoom_produits LIMIT 1").fetchone():
        return
    maintenant = int(time.time())
    st = Counter(dict(con.execute("SELECT statut, COUNT(*) FROM zoom_produits GROUP BY 1")))
    raisons = Counter(r[0].split(" (")[0] for r in con.execute(
        "SELECT raison FROM zoom_produits WHERE statut = 'abandonne'"))
    part = con.execute("SELECT AVG(part_identique), SUM(n_identiques), SUM(n_cartes) FROM zoom_produits").fetchone()
    a = Counter(dict(con.execute("SELECT statut, COUNT(*) FROM zoom_annonces GROUP BY 1")))
    fausses = con.execute("SELECT SUM(fausse_disparition) FROM zoom_annonces").fetchone()[0] or 0
    verifs = con.execute("SELECT COUNT(*) FROM zoom_journal WHERE type = 'verification'").fetchone()[0]
    req_zoom = con.execute("SELECT COALESCE(SUM(req_zoom), 0) FROM releves").fetchone()[0]
    # annonces au résultat connu : vendue (probable / confirmée) ou encore en vente ≥ 24 h après la 1re vue
    connues = con.execute("""SELECT COUNT(*) FROM zoom_annonces WHERE statut IN ('vendu', 'vendu_probable')
        OR (statut IN ('en_vente', 'reserve') AND derniere_vue - premiere_vue >= 86400)""").fetchone()[0]
    documentes = con.execute("""SELECT COUNT(*) FROM (SELECT produit_id,
        SUM(statut IN ('vendu', 'vendu_probable')) AS v,
        SUM(statut IN ('vendu', 'vendu_probable') OR (statut IN ('en_vente', 'reserve')
            AND derniere_vue - premiere_vue >= 86400)) AS c
        FROM zoom_annonces GROUP BY produit_id) WHERE c >= 15 AND v >= 3""").fetchone()[0]
    debut = int(etat(con, "debut", maintenant))
    # comparaison avec le rattrapage sur la même période : annonces neuves sans marque utilisables par lecture
    ratt = [dict(zip(("titre", "marque", "etat", "statut", "categorie"), r)) for r in con.execute(
        "SELECT titre, marque, etat, statut, categorie FROM rattrapage WHERE verifie_le >= ?", (debut,))]
    ratt_cibles = sum(1 for r in ratt if r["categorie"] and est_cible(r) and
                      r["statut"] in ("en_vente", "reserve", "vendu", "vendu_probable"))
    ratt_lectures = con.execute("SELECT COUNT(*) FROM observations WHERE source = 'rattrapage' AND ts >= ?",
                                (debut,)).fetchone()[0]
    log("===== BILAN DU PILOTE P2 (depuis le "
        f"{time.strftime('%d/%m %H:%M', time.gmtime(debut))} UTC, découverte jusqu'au "
        f"{time.strftime('%d/%m %H:%M', time.gmtime(debut + DECOUVERTE_J * 86400))})")
    log(f"  Produits sondés : {sum(st.values())} | suivis : {st['suivi']} | terminés : {st['termine']} | "
        f"abandonnés : {st['abandonne']} {dict(raisons)}")
    log(f"  Résultats identiques : {round(100 * (part[0] or 0))} % en moyenne ({part[1] or 0} / {part[2] or 0} cartes)")
    log(f"  Annonces suivies : {sum(a.values())} {dict(a)}")
    log(f"  Ventes : {a['vendu']} confirmées, {a['vendu_probable']} probables | fausses disparitions : "
        f"{fausses}/{verifs} vérifications")
    log(f"  Annonces au résultat connu : {connues} | produits suffisamment documentés (≥ 15 + ≥ 3 ventes) : "
        f"{documentes}")
    log(f"  Coût P2 : {req_zoom} requêtes -> {round(connues / req_zoom, 2) if req_zoom else '—'} annonce(s) au "
        f"résultat connu par requête | rattrapage même période : {ratt_cibles} neuves sans marque utilisables "
        f"pour {ratt_lectures} lectures -> {round(ratt_cibles / ratt_lectures, 3) if ratt_lectures else '—'} par requête")
