#!/usr/bin/env bash
# Crée sauvegarde-AAAA-MM-JJ.v2.gcm (date UTC) à partir de la base de production vinted.v2.gcm,
# seulement si elle n'existe pas encore (jamais écrasée) et seulement si la base est authentique et intègre.
set -euo pipefail
ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$ICI/base.sh" cle_presente
du_jour="sauvegarde-$(date -u +%F).v2.gcm"
if gh release view donnees --json assets --jq '.assets[].name' | grep -qx "$du_jour"; then
  echo "La sauvegarde du jour existe déjà : $du_jour (rien à faire)."
  exit 0
fi
mkdir -p jour && cd jour
# la base peut être en cours de remplacement par un relevé : 3 essais espacés d'une minute
for essai in 1 2 3; do
  if gh release download donnees -p vinted.v2.gcm --clobber; then break; fi
  [ "$essai" = 3 ] && { echo "ERREUR : vinted.v2.gcm introuvable." >&2; exit 1; }
  sleep 60
done
bash "$ICI/base.sh" dechiffrer vinted.v2.gcm verifiee.db     # authentique + intègre, sinon arrêt
cp vinted.v2.gcm "$du_jour"
gh release upload donnees "$du_jour"                          # sans --clobber : n'écrase jamais
mkdir -p controle && (cd controle && gh release download donnees -p "$du_jour")
cmp "$du_jour" "controle/$du_jour"
bash "$ICI/base.sh" dechiffrer "controle/$du_jour" controle.db > /dev/null
cmp verifiee.db controle.db
echo "Sauvegarde du jour créée et vérifiée : $du_jour"
