"""Annonces « cibles » de l'écran Opportunités : NEUVES et SANS MARQUE (revendables depuis la Chine).

Utilisé par collecte.py (suivi en priorité) et stats.py (calcul des opportunités). Aucune dépendance.
"""
import unicodedata

# Valeurs de « marque » qui veulent dire « pas de marque »
MARQUES_VIDES = {"(sans marque)", "no label", "sans marque", "none", "autre", "other", "handmade",
                 "fait main", "inconnu", "unbranded", "no brand", "generique", "generic", "sans"}


def simplifier(t):
    """minuscules, sans accents ni espaces superflus (pour comparer marque et titre)."""
    t = unicodedata.normalize("NFD", str(t or "").lower().replace("ß", "ss"))
    return " ".join("".join(c for c in t if unicodedata.category(c) != "Mn").split())


def sans_marque(marque, titre):
    """Pas de vraie marque ? Sur Vinted, sans marque la carte affiche le TITRE à la place de la marque."""
    m = simplifier(marque)
    return not m or m in MARQUES_VIDES or m == simplifier(titre)


# Catégories sans intérêt pour l'achat en Chine : livres, CD, films… n'ont jamais de « marque » sur Vinted,
# ils passeraient tous pour « sans marque » (constaté le 2026-11-08 : les 3 catégories cibles les plus vues).
# Idem jeux vidéo (constaté le 2026-11-09 : « Call of Duty », « Mario » passaient pour « sans marque »).
CATEGORIES_EXCLUES = ("Livres et médias", "Électronique > Jeux vidéo et consoles")


# Le pays du vendeur n'est lisible nulle part (vérifié le 08/11 et le 19/11, même depuis la France) et le robot,
# hébergé aux États-Unis, voit les annonces de tous les pays Vinted. On garde donc les titres écrits en français.
# shortcut: un titre d'un seul mot inconnu (« Stanley ») est écarté ; à revoir si Vinted affiche un jour le pays.
MOTS_FR = set("""taille femme homme enfant fille garcon bebe neuf neuve robe pull veste pantalon chaussure botte bottine sac lot
jupe manteau blouson chemise haut ensemble basket jouet noir noire blanc blanche bleu bleue rose rouge vert verte gris grise marron
doudoune gilet sweat debardeur collant chaussette bonnet echarpe ceinture bague collier boucle oreille bracelet montre lunette coque
etui housse coussin tapis bougie decoration cuisine poupee peluche livre jeu masque anneau bretelle guirlande sapin noel
du des les et avec pour aux au sur sans tres etat jamais porte portee ans mois paire doudou pyjama short maillot bain combinaison
salopette tablier plaid couverture parure drap serviette vaisselle tasse verre assiette""".split())
MOTS_ETRANGERS = set("""neu neue neuer neues mit und fur grosse groesse gr damen herren kinder schuhe stiefel jacke hose kleid madchen
nuovo nuova nuovi nuove donna uomo bambino bambina scarpe stivali stivaletti giacca taglia con nero bianco maglia vestito come
nuevo nueva nuevos mujer hombre nino nina zapatos botas chaqueta talla vestido camiseta zapatillas novo novos sapatilha sapatos
tamanho mulher nieuw nieuwe dames heren maat schoenen laarzen jas nowy nowa nowe damskie meskie buty rozmiar kurtka sukienka
novy damske boty velikost new size women womens men mens kids girls boys shoes boots jacket dress with the and of for""".split())


def titre_francais(titre):
    """Titre écrit en français ? Plus de mots français que de mots étrangers, et pas de lettre étrangère (ß, ł, ñ…)."""
    import re
    if re.search(r"[ßąćęłńśźżőűřěčšžůåøæãõñáíóú]", str(titre or "").lower()):
        return False
    mots = [m[:-1] if len(m) > 3 and m[-1] in "sx" else m for m in re.findall(r"[a-z]+", simplifier(titre))]
    return sum(m in MOTS_FR for m in mots) > sum(m in MOTS_ETRANGERS for m in mots)


def est_cible(a):
    """Neuf (avec ou sans étiquette) ET sans marque, titre en français, hors livres / médias (si la catégorie est connue)."""
    if str(a.get("categorie") or "").startswith(CATEGORIES_EXCLUES):
        return False
    return (simplifier(a.get("etat")).startswith("neuf") and sans_marque(a.get("marque"), a.get("titre"))
            and titre_francais(a.get("titre")))
