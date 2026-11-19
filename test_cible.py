"""Vérification rapide : python test_cible.py"""
from cible import est_cible, titre_francais

assert titre_francais("Bottes d’hiver femme taille/37") and titre_francais("Lot de 3 bougies")
assert not titre_francais("Stivaletti donna") and not titre_francais("Boots neu gr.40")
assert not titre_francais("Bota alta marrón") and not titre_francais("Cinturon piel marrón")
assert not titre_francais("Women’s mid calf boots") and not titre_francais("Zapatillas On Cloud Talla 43")
assert est_cible({"titre": "Bottes de neige femme", "marque": "Bottes de neige femme", "etat": "Neuf sans étiquette"})
assert not est_cible({"titre": "Scarpe nuove", "marque": "Scarpe nuove", "etat": "Neuf sans étiquette"})
print("OK")
