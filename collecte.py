"""Niches Vinted — relevé automatique (lancé toutes les 20 min par GitHub Actions).

Principe :
  1. On lit les dernières annonces publiées (catalogue « plus récents », quelques pages).
     Chaque annonce est enregistrée (titre, marque, taille, état, prix, favoris...).
  2. Une partie d'entre elles (échantillon au hasard) est « suivie » : on revient voir
     sa page après 6 h, 24 h, 3 j et 7 j pour savoir si elle est vendue.
  3. Les statistiques (stats.py) en déduisent quelles catégories / marques / prix
     se vendent le plus vite -> les niches.

Règles de politesse : visiteur anonyme (aucun compte Vinted), une requête toutes les
quelques secondes, arrêt immédiat du relevé si Vinted répond « 403 / 429 » plusieurs fois.

Base de données : SQLite (vinted.db), conservée entre deux relevés dans une Release GitHub.
"""
from html import unescape
import json
import os
import random
import re
import sqlite3
import sys
import time

from curl_cffi import requests as cr

BASE = "https://www.vinted.fr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
ENTETES = {"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"}

# Réglages (modifiables par variables d'environnement dans le workflow)
DB = os.environ.get("NICHES_DB", "vinted.db")
PAGES = int(os.environ.get("NICHES_PAGES", "6"))            # pages de catalogue par relevé (96 annonces/page)
TAUX_SUIVI = float(os.environ.get("NICHES_TAUX", "0.07"))    # part des nouvelles annonces suivies
PAUSE = float(os.environ.get("NICHES_PAUSE", "4"))           # secondes entre deux requêtes
DUREE_MAX = float(os.environ.get("NICHES_DUREE", "780"))     # durée max d'un relevé (s)
CONTROLES_H = [6, 24, 72, 168]                               # quand revenir voir une annonce suivie
RATTRAPAGE_MAX = int(os.environ.get("NICHES_RATTRAPAGE", "120"))  # anciennes annonces vues par relevé (max)
RATTRAPAGE_AGES_H = [24, 72, 168]                            # âges visés : 1 j, 3 j, 7 j
DEBIT_DEFAUT = 75.0                                          # numéros d'annonce créés par seconde (mesuré la nuit)

DEBUT = time.time()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- base de données

def ouvrir_base():
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS annonces (
        id INTEGER PRIMARY KEY,
        vu_le INTEGER,             -- 1re fois vue (horodatage Unix)
        titre TEXT, marque TEXT, taille TEXT, etat TEXT,
        prix REAL, prix_total REAL, devise TEXT,
        favoris0 INTEGER,          -- favoris au moment où on l'a vue
        pro INTEGER,               -- vendeur professionnel ?
        miniature TEXT, url TEXT,
        suivi INTEGER DEFAULT 0,   -- 1 = on revient voir si elle se vend
        n_controles INTEGER DEFAULT 0,
        prochain_controle INTEGER, -- horodatage du prochain passage (NULL = plus de suivi)
        dernier_controle INTEGER,
        statut TEXT DEFAULT 'nouveau',  -- nouveau / en_vente / reserve / vendu / supprime / masque / inconnu
        categorie_id INTEGER, categorie TEXT,   -- ex. « Femmes > Vêtements > Robes »
        favoris INTEGER,           -- favoris au dernier contrôle
        prix_final REAL,
        vendu_apres_h INTEGER,     -- vendue entre vendu_apres_h et vendu_avant_h heures
        vendu_avant_h INTEGER,
        vendu_detecte INTEGER,     -- horodatage de la détection de la vente
        brut TEXT                  -- indicateurs bruts lus sur la page (pour vérifier)
    );
    CREATE INDEX IF NOT EXISTS i_controle ON annonces(prochain_controle) WHERE prochain_controle IS NOT NULL;
    CREATE INDEX IF NOT EXISTS i_vu ON annonces(vu_le);
    -- Annonces NON suivies de plus de 2 jours : résumées par jour et par marque puis effacées
    -- (sert à mesurer l'offre : combien d'annonces nouvelles par marque).
    CREATE TABLE IF NOT EXISTS offre_jour (
        jour TEXT, marque TEXT, n INTEGER, somme_prix REAL, PRIMARY KEY (jour, marque)
    );
    -- Rattrapage : anciennes annonces (1 j, 3 j, 7 j) vues UNE fois pour savoir tout de suite
    -- si elles sont vendues (sans attendre une semaine de suivi).
    CREATE TABLE IF NOT EXISTS rattrapage (
        id INTEGER PRIMARY KEY,
        verifie_le INTEGER, age_vise_h INTEGER, age_h REAL,
        statut TEXT,               -- en_vente / reserve / vendu / masque / supprime / inconnu
        titre TEXT, marque TEXT, taille TEXT, etat TEXT, prix REAL,
        categorie_id INTEGER, categorie TEXT, favoris INTEGER, miniature TEXT, url TEXT, brut TEXT
    );
    -- Repères numéro d'annonce <-> heure (pour estimer l'âge d'une annonce d'après son numéro)
    CREATE TABLE IF NOT EXISTS reperes (ts INTEGER PRIMARY KEY, id INTEGER);
    CREATE TABLE IF NOT EXISTS releves (
        debut INTEGER, duree REAL, nouvelles INTEGER, vues INTEGER,
        controles INTEGER, ventes INTEGER, blocages INTEGER, note TEXT
    );
    """)
    cols_annonces = [c[1] for c in con.execute("PRAGMA table_info(annonces)")]
    if "badge" not in cols_annonces:
        con.execute("ALTER TABLE annonces ADD COLUMN badge TEXT")
    if "recherche" not in cols_annonces:
        con.execute("ALTER TABLE annonces ADD COLUMN recherche INTEGER")   # n° de la recherche ciblée
    colonnes = [c[1] for c in con.execute("PRAGMA table_info(rattrapage)")]
    if "devise" not in colonnes:
        # avant le 2026-11-08 midi la devise n'était pas lue : prix inutilisables (forints, zlotys...)
        con.execute("ALTER TABLE rattrapage ADD COLUMN devise TEXT")
        con.execute("UPDATE rattrapage SET prix = NULL")
        con.commit()
    return con


# ---------------------------------------------------------------- lecture des pages Vinted

def flux_next(html):
    """Le site est en Next.js : les données sont dans des morceaux self.__next_f.push([1,"..."]).
    On les recolle et on les déséchappe pour obtenir un texte où chercher du JSON."""
    morceaux = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html):
        try:
            morceaux.append(json.loads(m.group(1)))
        except ValueError:
            pass
    return "".join(morceaux)


def objets_apres(texte, cle):
    """Tous les objets JSON qui suivent "cle": dans le texte."""
    dec, res = json.JSONDecoder(), []
    for m in re.finditer('"' + re.escape(cle) + '":', texte):
        i = m.end()
        while i < len(texte) and texte[i] in " \n\t":
            i += 1
        try:
            obj, _ = dec.raw_decode(texte, i)
            res.append(obj)
        except ValueError:
            continue
    return res


def montant(p):
    try:
        return float((p or {}).get("amount"))
    except (TypeError, ValueError):
        return None


def texte_badge(b):
    """Badge affiché sur l'annonce (ex. « En demande ») -> texte, sinon None."""
    if isinstance(b, str):
        return None if b.startswith("$") else b
    if isinstance(b, dict):
        for k in ("title", "text", "label", "body", "content", "type"):
            if isinstance(b.get(k), str):
                return b[k]
        return json.dumps(b, ensure_ascii=False)[:80]
    return None


def lire_cartes(html):
    """Annonces d'une page de catalogue -> liste de dict."""
    vues, res = set(), []
    for c in objets_apres(flux_next(html), "productItem"):
        if not isinstance(c, dict) or not c.get("id") or c["id"] in vues:
            continue
        vues.add(c["id"])
        box = c.get("itemBox") or {}
        ligne2 = str(box.get("secondLine") or "")
        taille, etat = "", ligne2
        if " · " in ligne2:
            taille, etat = ligne2.split(" · ", 1)
        marque = box.get("firstLine")
        res.append({
            "id": int(c["id"]),
            "titre": c.get("title"),
            "marque": marque if isinstance(marque, str) and marque != "$undefined" else None,
            "taille": taille or None,
            "etat": etat or None,
            "prix": montant(c.get("price")),
            "prix_total": montant(c.get("totalItemPrice")),
            "devise": (c.get("price") or {}).get("currencyCode"),
            "favoris0": c.get("favouriteCount") or 0,
            "badge": texte_badge(box.get("badge")),
            "pro": 1 if (c.get("user") or {}).get("isBusiness") else 0,
            "miniature": c.get("thumbnailUrl"),
            "url": c.get("url"),
        })
    return res


def lire_annonce(html):
    """Page d'une annonce -> état (en vente / vendue...), catégorie, favoris, prix."""
    f = flux_next(html)
    brut = {}
    for cle in ["can_buy", "instant_buy", "is_hidden", "is_reserved", "is_closed", "is_sold",
                "favourite_count", "catalog_id", "availability", "item_closing_action"]:
        m = re.search(r'"' + cle + r'":\s*("[^"]*"|true|false|null|-?\d+(?:\.\d+)?)', f)
        if m:
            brut[cle] = json.loads(m.group(1))
    for m in re.finditer(r'"(\w*(?:[cC]ountry|[cC]ity)\w*)":\s*("[^"]{1,40}"|\d+)', f):
        if m.group(1) not in brut and len(brut) < 25:
            brut[m.group(1)] = json.loads(m.group(2))
    m = re.search(r'"(?:badge|itemBadge|item_badge)":\s*(\{[^{}]{0,200}\}|"[^"$][^"]{0,40}")', f)
    if m:
        try:
            brut["badge"] = texte_badge(json.loads(m.group(1)))
        except ValueError:
            pass
    for m in re.finditer(r'"([a-z_]*(?:sold|closed|closing|transaction)[a-z_]*)":\s*(true|false|"[^"]{0,40}"|\d+)', f):
        if m.group(1) not in brut and len(brut) < 25:
            brut[m.group(1)] = json.loads(m.group(2))
    # Fil d'Ariane : Femmes > Vêtements > Hauts et t-shirts > T-shirts
    chemin = []
    for b in objets_apres(f, "breadcrumbs"):
        if isinstance(b, list) and b and isinstance(b[0], dict):
            chemin = [x.get("title") for x in b if x.get("url", "").startswith("/catalog/")
                      and "/brand/" not in x.get("url", "")]
            break
    prix, devise = None, None
    m = re.search(r'"offers":\{[^{}]*?"price":\s*([\d.]+)', f)
    if m:
        prix = float(m.group(1))
        m2 = re.search(r'"offers":\{[^{}]*?"priceCurrency":\s*"(\w+)"', f)
        devise = m2.group(1) if m2 else None
    d = details_annonce(f)
    if prix is None:
        # bloc « faire une offre » de CETTE annonce : "price":{"amount":"26","currency_code":"EUR"},"seller_id":..,"title":..
        m = re.search(r'"price":\{"amount":"([\d.]+)","currency_code":"(\w+)"\},"seller_id":"\d+","title":', f)
        if m:
            prix, devise = float(m.group(1)), m.group(2)
    d["devise"] = devise
    d["prix_eur"] = en_euros(prix, devise)
    if not d["titre"]:
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', html) or \
            re.search(r'<title>([^<]*)</title>', html)
        if m:
            d["titre"] = unescape(m.group(1)).replace(" | Vinted", "").strip() or None
    if not d["miniature"]:
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"', html)
        if m:
            d["miniature"] = unescape(m.group(1))
    return brut, " > ".join(t for t in chemin if t), prix, d


# Taux approximatifs (1 unité = x €) : le rattrapage tombe sur des annonces de tous les pays Vinted.
TAUX_EUR = {"EUR": 1.0, "PLN": 0.235, "CZK": 0.04, "HUF": 0.0025, "RON": 0.2, "SEK": 0.088, "DKK": 0.134,
            "GBP": 1.17, "USD": 0.92, "CHF": 1.05, "NOK": 0.085, "BGN": 0.51, "HRK": 0.133}


def en_euros(prix, devise):
    if prix is None or devise not in TAUX_EUR:
        return None
    return round(prix * TAUX_EUR[devise], 2)


def produit_jsonld(f):
    """Objet JSON-LD « Product » de la page (nom, marque, image, état, prix)."""
    dec = json.JSONDecoder()
    for m in re.finditer(r'"@type":\s*"Product"', f):
        # on remonte jusqu'à l'accolade qui ouvre l'objet
        debut = m.start()
        for _ in range(40):
            debut = f.rfind("{", 0, debut)
            if debut < 0:
                break
            try:
                obj, _ = dec.raw_decode(f, debut)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("@type") == "Product":
                return obj
    return {}


def details_annonce(f):
    """Titre, marque, taille, état, miniature lus sur la page d'une annonce."""
    p = produit_jsonld(f)
    marque = p.get("brand")
    image = p.get("image")
    d = {"titre": p.get("name"),
         "marque": marque.get("name") if isinstance(marque, dict) else marque,
         "miniature": image[0] if isinstance(image, list) and image else image,
         "taille": None, "etat": None}
    # Attributs affichés sur la page : [{"code": "brand", "data": {"value": ...}}, {"code": "size"...}]
    for liste in objets_apres(f, "attributes"):
        if isinstance(liste, list) and liste and isinstance(liste[0], dict) and "code" in liste[0]:
            for a in liste:
                code, val = str(a.get("code", "")), (a.get("data") or {}).get("value")
                if "size" in code and not d["taille"]:
                    d["taille"] = val
                elif code in ("status", "condition") and not d["etat"]:
                    d["etat"] = val
                elif code == "brand" and not d["marque"]:
                    d["marque"] = val
            break
    return d


def statut_depuis(brut):
    """Déduit le statut à partir des indicateurs de la page."""
    dispo = str(brut.get("availability", "")).lower()
    if brut.get("is_closed") is True or brut.get("is_sold") is True or "sold" in dispo or "outofstock" in dispo:
        return "vendu"
    if brut.get("is_reserved") is True:
        return "reserve"
    if brut.get("is_hidden") is True:
        return "masque"
    # Constaté le 2026-11-08 : une annonce vendue a can_buy=false, instant_buy=false, n'est ni réservée
    # ni masquée, et sa fiche produit JSON-LD (prix, disponibilité) a disparu de la page.
    if brut.get("can_buy") is False and "instock" not in dispo:
        return "vendu"
    return "en_vente"


class Vinted:
    """Session anonyme + pause entre requêtes + détection de blocage."""

    def __init__(self):
        self.s = cr.Session(impersonate="chrome")
        self.blocages = 0
        self.dernier = 0.0

    def get(self, chemin):
        attente = PAUSE * random.uniform(0.8, 1.3) - (time.time() - self.dernier)
        if attente > 0:
            time.sleep(attente)
        self.dernier = time.time()
        try:
            r = self.s.get(BASE + chemin, headers=ENTETES, timeout=40)
        except Exception as e:
            log("  erreur réseau", chemin, e)
            return None
        if r.status_code in (403, 429):
            self.blocages += 1
            log(f"  {r.status_code} sur {chemin} (blocage n°{self.blocages})")
        return r

    def bloque(self):
        return self.blocages >= 3


# ---------------------------------------------------------------- étapes du relevé

def temps_restant():
    return DUREE_MAX - (time.time() - DEBUT)


def nouvelles_annonces(v, con):
    maintenant = int(time.time())
    vues = nouvelles = 0
    for page in range(1, PAGES + 1):
        if v.bloque() or temps_restant() < 60:
            break
        r = v.get(f"/catalog?order=newest_first&page={page}")
        if r is None or r.status_code != 200:
            continue
        cartes = lire_cartes(r.text)
        vues += len(cartes)
        if page == 1 and cartes:
            # repère numéro <-> heure (sert à estimer l'âge des annonces pour le rattrapage)
            con.execute("INSERT OR REPLACE INTO reperes VALUES (?, ?)", (maintenant, max(c["id"] for c in cartes)))
        for c in cartes:
            suivi = 1 if random.random() < TAUX_SUIVI else 0
            cur = con.execute(
                """INSERT OR IGNORE INTO annonces (id, vu_le, titre, marque, taille, etat, prix, prix_total,
                   devise, favoris0, pro, miniature, url, suivi, prochain_controle, badge)
                   VALUES (:id, :vu_le, :titre, :marque, :taille, :etat, :prix, :prix_total, :devise,
                   :favoris0, :pro, :miniature, :url, :suivi, :prochain, :badge)""",
                {**c, "vu_le": maintenant, "suivi": suivi,
                 "prochain": maintenant + CONTROLES_H[0] * 3600 if suivi else None})
            nouvelles += cur.rowcount
        con.commit()
        badges = [c["badge"] for c in cartes if c["badge"]]
        log(f"Catalogue page {page} : {len(cartes)} annonces" + (f" | badges : {badges[:5]}" if badges else ""))
    return vues, nouvelles


def controles(v, con):
    """Revient voir les annonces suivies dont c'est l'heure."""
    faits = ventes = 0
    while not v.bloque() and temps_restant() > 30:
        maintenant = int(time.time())
        lot = con.execute(
            """SELECT id, vu_le, n_controles, url, statut FROM annonces
               WHERE prochain_controle IS NOT NULL AND prochain_controle <= ?
               ORDER BY prochain_controle LIMIT 20""", (maintenant,)).fetchall()
        if not lot:
            break
        for iid, vu_le, n, url, ancien in lot:
            if v.bloque() or temps_restant() < 30:
                break
            r = v.get(url or f"/items/{iid}")
            if r is None or r.status_code not in (200, 404, 410):
                # erreur réseau / 403 / 429 / 500 : on reporte ce contrôle de 30 min
                con.execute("UPDATE annonces SET prochain_controle = ? WHERE id = ?",
                            (int(time.time()) + 1800, iid))
                continue
            maintenant = int(time.time())
            age_h = (maintenant - vu_le) / 3600
            h_prec = CONTROLES_H[n - 1] if n > 0 else 0
            h_now = CONTROLES_H[min(n, len(CONTROLES_H) - 1)]
            n2 = n + 1
            prochain = vu_le + CONTROLES_H[n2] * 3600 if n2 < len(CONTROLES_H) else None
            redirige = "/items/" not in str(getattr(r, "url", "") or "/items/")
            if r.status_code in (404, 410) or redirige:   # supprimée (ou redirigée ailleurs)
                con.execute("""UPDATE annonces SET statut='supprime', n_controles=?, prochain_controle=NULL,
                               dernier_controle=? WHERE id=?""", (n2, maintenant, iid))
            elif r.status_code == 200:
                brut, categorie, _, d = lire_annonce(r.text)
                prix = d["prix_eur"]
                # page sans aucun indicateur lisible -> « inconnu » (exclu des stats)
                statut = statut_depuis(brut) if ("can_buy" in brut or "availability" in brut) else "inconnu"
                cat_id = brut.get("catalog_id")
                valeurs = {"id": iid, "n": n2, "t": maintenant, "statut": statut,
                           "cat_id": int(cat_id) if str(cat_id or "").isdigit() else None,
                           "cat": categorie or None, "fav": brut.get("favourite_count"),
                           "prix": prix, "brut": json.dumps(brut, ensure_ascii=False),
                           "prochain": prochain}
                if statut == "vendu":
                    ventes += 1
                    valeurs.update(prochain=None, apres=h_prec, avant=max(h_now, int(age_h)))
                    con.execute("""UPDATE annonces SET statut=:statut, n_controles=:n, dernier_controle=:t,
                        prochain_controle=:prochain, categorie_id=COALESCE(:cat_id, categorie_id),
                        categorie=COALESCE(:cat, categorie), favoris=:fav, prix_final=:prix, brut=:brut,
                        vendu_apres_h=:apres, vendu_avant_h=:avant, vendu_detecte=:t WHERE id=:id""", valeurs)
                else:
                    con.execute("""UPDATE annonces SET statut=:statut, n_controles=:n, dernier_controle=:t,
                        prochain_controle=:prochain, categorie_id=COALESCE(:cat_id, categorie_id),
                        categorie=COALESCE(:cat, categorie), favoris=:fav, prix_final=:prix, brut=:brut
                        WHERE id=:id""", valeurs)
            faits += 1
        con.commit()
    log(f"Contrôles : {faits} annonces revues, {ventes} ventes détectées")
    return faits, ventes


def debit_ids(con):
    """Numéros d'annonce créés par seconde, mesurés sur nos propres relevés."""
    a = con.execute("SELECT ts, id FROM reperes ORDER BY ts LIMIT 1").fetchone()
    b = con.execute("SELECT ts, id FROM reperes ORDER BY ts DESC LIMIT 1").fetchone()
    if a and b and b[0] - a[0] >= 1800 and b[1] > a[1]:
        return (b[1] - a[1]) / (b[0] - a[0])
    return DEBIT_DEFAUT


def id_vers_age(con, iid, maintenant):
    """Âge approximatif (heures) d'une annonce d'après son numéro."""
    # 1) entre deux repères connus : interpolation
    av = con.execute("SELECT ts, id FROM reperes WHERE id <= ? ORDER BY id DESC LIMIT 1", (iid,)).fetchone()
    ap = con.execute("SELECT ts, id FROM reperes WHERE id >= ? ORDER BY id LIMIT 1", (iid,)).fetchone()
    if av and ap and ap[1] > av[1]:
        ts = av[0] + (iid - av[1]) * (ap[0] - av[0]) / (ap[1] - av[1])
        return (maintenant - ts) / 3600
    # 2) sinon : à partir du plus ancien repère, avec le débit moyen
    ref = av or ap or con.execute("SELECT ts, id FROM reperes ORDER BY ts LIMIT 1").fetchone()
    ts = ref[0] - (ref[1] - iid) / debit_ids(con)
    return (maintenant - ts) / 3600


def id_pour_age(con, age_h, maintenant):
    """Numéro d'annonce approximatif publiée il y a age_h heures."""
    cible = maintenant - age_h * 3600
    av = con.execute("SELECT ts, id FROM reperes WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (cible,)).fetchone()
    ap = con.execute("SELECT ts, id FROM reperes WHERE ts >= ? ORDER BY ts LIMIT 1", (cible,)).fetchone()
    if av and ap and ap[0] > av[0]:
        return int(av[1] + (cible - av[0]) * (ap[1] - av[1]) / (ap[0] - av[0]))
    ref = ap or av
    return int(ref[1] - (ref[0] - cible) * debit_ids(con))


def rattrapage(v, con):
    """Va voir des annonces publiées il y a ~1 j, ~3 j, ~7 j (numéros tirés au hasard)
    et note tout de suite si elles sont vendues. Utilise le temps restant du relevé."""
    if not con.execute("SELECT 1 FROM reperes LIMIT 1").fetchone():
        return 0, 0
    faits = ventes = absentes = erreurs = 0
    montres = 0
    while faits + absentes < RATTRAPAGE_MAX and not v.bloque() and temps_restant() > 30:
        maintenant = int(time.time())
        age_vise = RATTRAPAGE_AGES_H[(faits + absentes) % len(RATTRAPAGE_AGES_H)]
        iid = id_pour_age(con, age_vise * random.uniform(0.85, 1.15), maintenant)
        if con.execute("SELECT 1 FROM rattrapage WHERE id = ?", (iid,)).fetchone():
            continue
        r = v.get(f"/items/{iid}")
        if r is None or r.status_code not in (200, 404, 410):
            erreurs += 1
            if erreurs >= 5:
                break
            continue
        age_h = round(id_vers_age(con, iid, maintenant), 1)
        if r.status_code != 200 or "/items/" not in str(r.url):
            # numéro jamais publié (brouillon), annonce supprimée, ou autre pays : inutilisable
            con.execute("INSERT OR IGNORE INTO rattrapage (id, verifie_le, age_vise_h, age_h, statut) "
                        "VALUES (?,?,?,?, 'supprime')", (iid, maintenant, age_vise, age_h))
            absentes += 1
            continue
        brut, categorie, prix, d = lire_annonce(r.text)
        if montres == 0:
            f = flux_next(r.text)
            for mot in ("ountry", "badge", "Badge", "location", "demand"):
                extraits = [f[max(0, x.start() - 80): x.end() + 80] for x in re.finditer(mot, f)][:3]
                log(f"  [diagnostic] « {mot} » : {extraits}")
        statut = statut_depuis(brut) if ("can_buy" in brut or "availability" in brut) else "inconnu"
        cat_id = brut.get("catalog_id")
        con.execute("""INSERT OR IGNORE INTO rattrapage (id, verifie_le, age_vise_h, age_h, statut, titre, marque,
            taille, etat, prix, categorie_id, categorie, favoris, miniature, url, brut, devise)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (iid, maintenant, age_vise, age_h, statut, d["titre"], d["marque"], d["taille"], d["etat"], d["prix_eur"],
             int(cat_id) if str(cat_id or "").isdigit() else None, categorie or None,
             brut.get("favourite_count"), d["miniature"], f"/items/{iid}", json.dumps(brut, ensure_ascii=False),
             d["devise"]))
        faits += 1
        ventes += statut == "vendu"
        # Journal détaillé des premières pages lues (pour vérifier la détection des ventes)
        if montres < 6 or (statut != "en_vente" and montres < 15):
            montres += 1
            log(f"  [{iid} ~{age_h:.0f} h] {statut} | {d['titre']} | {d['marque']} | {d['taille']} | {d['etat']} | "
                f"{prix} {d['devise']} | {categorie} | {brut}")
        if faits % 10 == 0:
            con.commit()
    con.commit()
    log(f"Rattrapage : {faits} anciennes annonces lues ({ventes} vendues), {absentes} absentes")
    return faits, ventes


def suivre_recherches(con):
    """Ajoute au suivi les annonces fraîches trouvées par les recherches ciblées (recherches/*.json)."""
    if not os.path.isdir("recherches"):
        return
    n = 0
    for nom in os.listdir("recherches"):
        if not nom.endswith(".json"):
            continue
        with open(os.path.join("recherches", nom), encoding="utf-8") as f:
            r = json.load(f)
        for c in r.get("a_suivre", []):
            # vue au moment de la recherche, publiée age_h heures avant
            vu_le = int(r["cree_le"] - (c.get("age_h") or 0) * 3600)
            cur = con.execute(
                """INSERT OR IGNORE INTO annonces (id, vu_le, titre, marque, taille, etat, prix, prix_total, devise,
                   favoris0, pro, miniature, url, suivi, prochain_controle, badge, recherche)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                (c["id"], vu_le, c.get("titre"), c.get("marque"), c.get("taille"), c.get("etat"), c.get("prix"),
                 c.get("prix_total"), c.get("devise"), c.get("favoris0"), c.get("pro"), c.get("miniature"),
                 c.get("url"), vu_le + CONTROLES_H[0] * 3600, c.get("badge"), r["numero"]))
            if cur.rowcount == 0:   # déjà vue par le relevé : on la rattache à la recherche et on la suit
                con.execute("""UPDATE annonces SET recherche = ?, suivi = 1, prochain_controle = COALESCE(
                    prochain_controle, CASE WHEN n_controles = 0 THEN vu_le + ? END) WHERE id = ? AND recherche IS NULL""",
                            (r["numero"], CONTROLES_H[0] * 3600, c["id"]))
            n += cur.rowcount
    con.commit()
    if n:
        log(f"Recherches ciblées : {n} nouvelles annonces ajoutées au suivi")


def reclasser(con):
    """Réapplique statut_depuis() aux annonces déjà lues (utile quand la règle de détection change)."""
    n = 0
    for table in ("rattrapage", "annonces"):
        nc = "n_controles" if table == "annonces" else "0"
        for iid, statut, brut, n_ctrl in con.execute(
                f"SELECT id, statut, brut, {nc} FROM {table} WHERE statut IN ('en_vente', 'vendu') "
                "AND brut IS NOT NULL").fetchall():
            b = json.loads(brut)
            nouveau = statut_depuis(b) if ("can_buy" in b or "availability" in b) else statut
            if nouveau != statut:
                n += 1
                if table == "rattrapage":
                    con.execute("UPDATE rattrapage SET statut = ? WHERE id = ?", (nouveau, iid))
                else:
                    # vendue entre l'avant-dernier et le dernier contrôle
                    avant = CONTROLES_H[max(0, n_ctrl - 1)] if nouveau == "vendu" else None
                    apres = (CONTROLES_H[n_ctrl - 2] if n_ctrl >= 2 else 0) if nouveau == "vendu" else None
                    con.execute("""UPDATE annonces SET statut = ?, prochain_controle = CASE WHEN ? = 'vendu' THEN NULL
                        ELSE prochain_controle END, vendu_apres_h = ?, vendu_avant_h = ?,
                        vendu_detecte = CASE WHEN ? = 'vendu' THEN COALESCE(vendu_detecte, dernier_controle) END
                        WHERE id = ?""", (nouveau, nouveau, apres, avant, nouveau, iid))
    con.commit()
    if n:
        log(f"Reclassement : {n} annonces changent de statut avec la règle actuelle")


def compacter(con):
    """Résume puis efface les annonces non suivies de plus de 48 h (garde la base légère)."""
    limite = int(time.time()) - 48 * 3600
    con.execute("""INSERT INTO offre_jour (jour, marque, n, somme_prix)
        SELECT date(vu_le, 'unixepoch'), COALESCE(marque, '(sans marque)'), COUNT(*), SUM(COALESCE(prix, 0))
        FROM annonces WHERE suivi = 0 AND vu_le < ? GROUP BY 1, 2
        ON CONFLICT (jour, marque) DO UPDATE SET n = n + excluded.n, somme_prix = somme_prix + excluded.somme_prix""",
                (limite,))
    n = con.execute("DELETE FROM annonces WHERE suivi = 0 AND vu_le < ?", (limite,)).rowcount
    con.commit()
    if n:
        log(f"Compactage : {n} anciennes annonces non suivies résumées")


def main():
    con = ouvrir_base()
    reclasser(con)
    suivre_recherches(con)
    v = Vinted()
    r = v.get("/")
    log("Accueil :", r.status_code if r is not None else "erreur")
    vues, nouvelles = nouvelles_annonces(v, con)
    log(f"Nouvelles annonces : {nouvelles} (sur {vues} vues)")
    faits, ventes = controles(v, con)
    r_faits, r_ventes = rattrapage(v, con)
    faits, ventes = faits + r_faits, ventes + r_ventes
    note = "bloqué par Vinted" if v.bloque() else ""
    con.execute("INSERT INTO releves VALUES (?,?,?,?,?,?,?,?)",
                (int(DEBUT), round(time.time() - DEBUT, 1), nouvelles, vues, faits, ventes, v.blocages, note))
    con.commit()
    en_attente = con.execute("SELECT COUNT(*) FROM annonces WHERE prochain_controle IS NOT NULL "
                             "AND prochain_controle <= ?", (int(time.time()),)).fetchone()[0]
    log(f"Contrôles en retard : {en_attente}")
    compacter(con)
    con.close()
    if vues == 0:
        log("ÉCHEC : aucune annonce lue (Vinted a peut-être changé son site ou bloque).")
        sys.exit(1)


if __name__ == "__main__":
    main()
