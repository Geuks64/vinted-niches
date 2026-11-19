"""Vérification rapide : python test_stats.py"""
from stats import taux_7j_estime

ref = {"taux_24h": 20.0, "taux_3j": 30.0, "taux_7j": 40.0}
seg = lambda **t: {"taux_7j": t.get("s"), "taux_3j": t.get("j"), "taux_24h": t.get("h")}
assert taux_7j_estime(seg(s=55.0), ref) == 55.0                      # vrai taux 7 j : inchangé
assert taux_7j_estime(seg(j=87.5), ref) <= 100                         # cas réel « 113,5 % » corrigé
assert taux_7j_estime(seg(h=100.0), ref) == 100                        # 100 % reste 100 %
assert abs(taux_7j_estime(seg(j=30.0), ref) - 40.0) < 1e-9             # segment moyen -> taux moyen
assert taux_7j_estime(seg(), ref) is None
print("OK", round(taux_7j_estime(seg(j=87.5), ref), 1))
