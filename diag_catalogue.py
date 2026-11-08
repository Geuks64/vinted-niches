"""TEST (lecture seule) : peut-on suivre les annonces depuis les listes filtrées du catalogue ?

Questions :
  1. Le filtre « Neuf » marche-t-il dans l'adresse du catalogue ? (paramètre à trouver)
  2. Combien d'heures / de jours d'annonces une liste filtrée (Neuf + une catégorie) remonte-t-elle ?
     (le catalogue s'arrête après ~10-14 pages de 96 annonces)
Règles : même rythme que le relevé (PAUSE entre requêtes), ~50 requêtes maximum, et JAMAIS en même temps
qu'un relevé (attend la fin de sa collecte, s'arrête si un nouveau relevé commence).
La base n'est lue que pour les repères (âge des annonces) et les catégories ; rien n'est écrit.
"""
import json
import re
import sqlite3
import subprocess
import time
from collections import Counter

import collecte
from cible import est_cible

DUREE = 300            # secondes de test au maximum (la collecte d'un relevé reprend ~6 min après la précédente)
PAGES_MAX = 14
NEUF_ESSAIS = {        # paramètres possibles pour « Neuf avec étiquette » (6) + « Neuf sans étiquette » (1)
    "status_ids": "status_ids[]=6&status_ids[]=1",
    "status": "status[]=6&status[]=1",
}
log = collecte.log


def gh_json(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    return json.loads(r.stdout or "null") if r.returncode == 0 else None


def releve_en_collecte():
    """Vrai si un relevé est en file d'attente ou en train de lire Vinted (étape « Relevé » pas finie)."""
    for statut in ("queued", "in_progress"):
        for run in gh_json("run", "list", "--workflow", "releve.yml", "--status", statut,
                           "--json", "databaseId") or []:
            if statut == "queued":
                return True
            jobs = (gh_json("run", "view", str(run["databaseId"]), "--json", "jobs") or {}).get("jobs", [])
            etapes = [e for j in jobs for e in j.get("steps", []) if e.get("name") == "Relevé"]
            if not etapes or etapes[0].get("status") != "completed":
                return True
    return False


def attendre_fenetre():
    debut = time.time()
    while releve_en_collecte():
        if time.time() - debut > 25 * 60:
            raise SystemExit("Un relevé occupe Vinted depuis 25 min : test annulé (rien n'a été lu).")
        log("Relevé en cours de collecte : on attend sa fin (pas de requêtes en parallèle)…")
        time.sleep(20)
    log("Aucun relevé ne lit Vinted : début du test.")


def main():
    con = sqlite3.connect(collecte.DB)
    maintenant = int(time.time())
    age = lambda iid: collecte.id_vers_age(con, iid, maintenant)

    # Catégories à tester : les plus fréquentes parmi les annonces neuves sans marque déjà vues
    lignes = con.execute("""SELECT categorie_id, categorie, titre, marque, etat FROM annonces WHERE categorie_id IS NOT NULL
                            UNION ALL SELECT categorie_id, categorie, titre, marque, etat FROM rattrapage
                            WHERE categorie_id IS NOT NULL""").fetchall()
    compte = Counter((c, n) for c, n, t, m, e in lignes if est_cible({"titre": t, "marque": m, "etat": e}))
    categories = [c for c, _ in compte.most_common(3)]
    log("Catégories testées :", categories)

    attendre_fenetre()
    fin = time.time() + DUREE
    v = collecte.Vinted()
    v.get("/")
    resultats = {}
    lues = [0]

    def page(params, n):
        if time.time() > fin or v.bloque():
            return None
        if n % 4 == 0 and releve_en_collecte():
            log("Un relevé vient de commencer : arrêt du test.")
            return None
        r = v.get(f"/catalog?order=newest_first&{params}&page={n}")
        lues[0] += 1
        if r is None or r.status_code != 200:
            log(f"  page {n} : HTTP {getattr(r, 'status_code', '—')}")
            return None
        cartes = collecte.lire_cartes(r.text)
        m = re.search(r'"total_entries":\s*(\d+)', collecte.flux_next(r.text))
        return cartes, (int(m.group(1)) if m else None)

    def profondeur(nom, params):
        """Parcourt la liste jusqu'à sa fin : nombre d'annonces et âge de la plus ancienne."""
        total, cartes_tout, n = None, [], 0
        for n in range(1, PAGES_MAX + 1):
            res = page(params, n)
            if res is None:
                break
            cartes, t = res
            total = total or t
            cartes_tout += cartes
            if cartes:
                log(f"  {nom} p.{n} : {len(cartes)} annonces, la plus ancienne a {age(min(c['id'] for c in cartes)):.1f} h")
            if len(cartes) < 90:
                break
        if not cartes_tout:
            return {"pages": n, "annonces": 0}
        neuves = sum(1 for c in cartes_tout if str(c["etat"] or "").startswith("Neuf"))
        return {"pages": n, "annonces": len(cartes_tout), "total_annonce": total,
                "plus_ancienne_h": round(age(min(c["id"] for c in cartes_tout)), 1),
                "plus_recente_h": round(age(max(c["id"] for c in cartes_tout)), 2),
                "part_neuf_%": round(100 * neuves / len(cartes_tout)),
                "part_neuf_sans_marque_%": round(100 * sum(map(est_cible, cartes_tout)) / len(cartes_tout))}

    # 1. Quel paramètre filtre « Neuf » ?
    filtre = None
    for nom, params in NEUF_ESSAIS.items():
        res = page(params, 1)
        if res is None:
            break
        cartes = res[0]
        part = round(100 * sum(1 for c in cartes if str(c["etat"] or "").startswith("Neuf")) / max(1, len(cartes)))
        log(f"Filtre « {nom} » : {len(cartes)} annonces, {part} % neuves")
        resultats[f"filtre_{nom}"] = {"annonces": len(cartes), "part_neuf_%": part}
        if part >= 90:
            filtre = params
            break
    resultats["filtre_neuf"] = filtre

    # 2. Profondeur des listes filtrées (Neuf + catégorie), puis Neuf seul
    for cat_id, cat_nom in categories:
        log(f"Catégorie {cat_id} ({cat_nom})")
        params = f"catalog[]={cat_id}" + (f"&{filtre}" if filtre else "")
        resultats[f"{cat_nom} (+neuf)" if filtre else cat_nom] = profondeur(cat_nom, params)
    if filtre:
        log("Neuf, toutes catégories")
        resultats["Neuf, toutes catégories"] = profondeur("neuf", filtre)

    log(f"Pages de catalogue lues : {lues[0]} | blocages : {v.blocages}")
    print("\n===== RÉSULTAT DU TEST =====")
    print(json.dumps(resultats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
