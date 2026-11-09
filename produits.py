"""Regroupement des annonces en PRODUITS (P1 « Meilleurs produits »), sans service payant ni IA externe.

Principe (tout est calculé en local, en Python) :
  1. Le titre de chaque annonce est nettoyé : minuscules, sans accents, sans mots vides (8 langues de Vinted),
     sans tailles ni mots de remplissage (« neuf », « taille », « femme »…), pluriels simplifiés.
  2. Les mots restants sont pondérés par leur rareté (IDF) : « papillon » compte plus que « bague ».
  3. Une annonce rejoint un produit si, DANS LA MÊME CATÉGORIE VINTED précise :
       - score de similarité ≥ SEUIL_IDENTIQUE (Jaccard pondéré entre ses mots et le « cœur » du produit),
       - au moins 2 mots informatifs en commun,
       - un prix compatible (au plus 2,5 fois plus cher ou moins cher que le prix médian du produit).
     Sinon elle crée un nouveau produit. Pas de chaînage : on compare toujours au cœur du produit
     (mots présents dans au moins la moitié de ses annonces), jamais d'annonce à annonce.
  4. Niveaux de ressemblance :
       - « identique »  : score ≥ SEUIL_IDENTIQUE + conditions ci-dessus -> même produit (fusion)
       - « similaire »  : SEUIL_SIMILAIRE ≤ score < SEUIL_IDENTIQUE -> produits voisins, JAMAIS fusionnés
       - « incertain »  : annonce proche d'un produit mais refusée (score limite, prix ou mots communs
                          insuffisants) -> comptée à part, jamais fusionnée
     Les titres trop vagues (moins de 2 mots informatifs, ex. « Bague ») ne forment aucun produit.
Aucune catégorie n'est favorisée : toutes sont traitées de la même façon (seuls livres / CD / films sont exclus
en amont, voir cible.py).
"""
import math
import re
from collections import Counter, defaultdict

from cible import simplifier

SEUIL_IDENTIQUE = 0.60
SEUIL_SIMILAIRE = 0.35
MOTS_COMMUNS_MIN = 2
ECART_PRIX_MAX = 2.5

# Mots vides et mots de remplissage des annonces (fr, en, es, it, de, nl, pl, pt), déjà sans accents
MOTS_VIDES = set("""
a au aux avec ce ces dans de des du en et la le les leur lot ma mes mon ou par pas plus pour sa sans se ses son
sur ta tes ton tres un une vos votre neuf neuve neufs neuves jamais porte portee portes etiquette etiquettes
taille unique tu femme femmes homme hommes fille garcon enfant enfants bebe ideal idee cadeau envoi rapide prix
etat bon tbe parfait super joli jolie belle beau magnifique vends vend vente vendu
the and for with of in on to by new brand never worn tag tags label size one women woman men man girl boy kids
kid baby gift price sale lot cute nice beautiful perfect condition item
el la los las de del con para por y o un una nuevo nueva nuevos nuevas etiqueta etiquetas talla mujer hombre
nina nino precio regalo sin estrenar usado perfecto bonito bonita
il lo gli le di da per e con un una nuovo nuova nuovi nuove mai usato usata taglia donna uomo bambina bambino
regalo prezzo cartellino etichetta
der die das und mit fur von ein eine neu neue neuer neues etikett grosse damen herren kinder geschenk preis
het een en voor met van nieuw nieuwe label maat dames heren kinderen cadeau prijs
i w z na do dla oraz o nowy nowa nowe metka rozmiar damski damska meski meska dziecko prezent cena
e o os as um uma com novo nova etiqueta tamanho mulher homem presente preco
xxs xs s m l xl xxl xxxl xxxxl
""".split())


def mots(titre):
    """Mots informatifs d'un titre (ensemble)."""
    res = set()
    for m in re.findall(r"[a-z0-9]+", simplifier(titre)):
        if m in MOTS_VIDES or len(m) < 2:
            continue
        if m.isdigit() and len(m) < 3:       # tailles / quantités courtes (38, 12…) : pas informatives
            continue
        if len(m) > 4 and m.endswith(("s", "x")) and not m.endswith("ss"):
            m = m[:-1]                         # pluriel simple : bagues -> bague, bijoux -> bijou
        res.add(m)
    return res


def similarite(a, b, idf):
    """Jaccard pondéré par la rareté des mots (0 = rien en commun, 1 = mêmes mots)."""
    if not a or not b:
        return 0.0
    inter = sum(idf.get(m, 1.0) for m in a & b)
    union = sum(idf.get(m, 1.0) for m in a | b)
    return inter / union if union else 0.0


def mediane(v):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


class Produit:
    def __init__(self, categorie, annonce, jeu):
        self.categorie = categorie
        self.annonces = []
        self.compte = Counter()
        self.incertains = 0
        self.ajouter(annonce, jeu)

    def ajouter(self, annonce, jeu):
        self.annonces.append(annonce)
        self.compte.update(jeu)
        n = len(self.annonces)
        self.coeur = {m for m, c in self.compte.items() if c >= n / 2}
        self.prix = mediane([a.get("prix") for a in self.annonces])


def regrouper(annonces, idf=None):
    """annonces : dict avec titre, categorie, prix (+ le reste). Renvoie (produits, bilan).
    Ordre déterministe (par numéro d'annonce) : même entrée -> mêmes produits."""
    jeux = {id(a): mots(a.get("titre")) for a in annonces}
    if idf is None:
        df = Counter(m for j in jeux.values() for m in j)
        total = max(1, len(annonces))
        idf = {m: math.log(1 + total / c) for m, c in df.items()}
    # un mot est « informatif » s'il n'apparaît pas dans plus de 5 % des titres
    seuil_info = math.log(1 + 20)
    bilan = Counter()
    par_categorie = defaultdict(list)
    for a in sorted(annonces, key=lambda a: a.get("id") or 0):
        if not a.get("categorie"):
            bilan["sans catégorie"] += 1
            continue
        if sum(1 for m in jeux[id(a)] if idf.get(m, 0) >= seuil_info) < MOTS_COMMUNS_MIN:
            bilan["titre trop vague"] += 1
            continue
        par_categorie[a["categorie"]].append(a)

    produits = []
    for cat, liste in par_categorie.items():
        locaux = []
        for a in liste:
            jeu = jeux[id(a)]
            meilleur, score = None, 0.0
            for p in locaux:
                s = similarite(jeu, p.coeur, idf)
                if s > score:
                    meilleur, score = p, s
            if meilleur is not None and score >= SEUIL_SIMILAIRE:
                communs = sum(1 for m in jeu & meilleur.coeur if idf.get(m, 0) >= seuil_info)
                prix_ok = True
                if a.get("prix") and meilleur.prix:
                    r = a["prix"] / meilleur.prix
                    prix_ok = 1 / ECART_PRIX_MAX <= r <= ECART_PRIX_MAX
                if score >= SEUIL_IDENTIQUE and communs >= MOTS_COMMUNS_MIN and prix_ok:
                    meilleur.ajouter(a, jeu)
                    bilan["identique (fusionnée)"] += 1
                    continue
                meilleur.incertains += 1          # proche mais refusée : jamais fusionnée
                bilan["incertaine (non fusionnée)"] += 1
            p = Produit(cat, a, jeu)
            locaux.append(p)
            bilan["nouveau produit"] += 1
        # produits « similaires » (voisins, jamais fusionnés)
        for i, p in enumerate(locaux):
            p.similaires = []
            for q in locaux[:i] + locaux[i + 1:]:
                s = similarite(p.coeur, q.coeur, idf)
                if SEUIL_SIMILAIRE <= s < SEUIL_IDENTIQUE:
                    p.similaires.append((round(s, 2), q))
            p.similaires.sort(key=lambda x: -x[0])
        produits += locaux
    return produits, bilan, idf


def nom(p, idf):
    """Nom affiché : le titre le plus représentatif du produit (le plus proche de son cœur)."""
    return max(p.annonces, key=lambda a: (similarite(mots(a.get("titre")), p.coeur, idf), -(a.get("id") or 0)))["titre"]
