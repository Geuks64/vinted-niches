"""Annonces « cibles » de l'écran Opportunités : NEUVES et SANS MARQUE (revendables depuis la Chine).

Utilisé par collecte.py (suivi en priorité) et stats.py (calcul des opportunités). Aucune dépendance.
"""
import unicodedata

# Valeurs de « marque » qui veulent dire « pas de marque »
MARQUES_VIDES = {"(sans marque)", "no label", "sans marque", "none", "autre", "other", "handmade",
                 "fait main", "inconnu", "unbranded", "no brand", "generique", "generic", "sans"}


def simplifier(t):
    """minuscules, sans accents ni espaces superflus (pour comparer marque et titre)."""
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return " ".join("".join(c for c in t if unicodedata.category(c) != "Mn").split())


def sans_marque(marque, titre):
    """Pas de vraie marque ? Sur Vinted, sans marque la carte affiche le TITRE à la place de la marque."""
    m = simplifier(marque)
    return not m or m in MARQUES_VIDES or m == simplifier(titre)


def est_cible(a):
    """Neuf (avec ou sans étiquette) ET sans marque."""
    return simplifier(a.get("etat")).startswith("neuf") and sans_marque(a.get("marque"), a.get("titre"))
