#!/usr/bin/env bash
# Restauration d'une sauvegarde v2 (appelé par le workflow « Restaurer une sauvegarde », voir restaurer.yml).
# Variables : SOURCE (precedente | avant-lot1 | quotidienne), DATE_SAUVEGARDE (AAAA-MM-JJ),
#             CIBLE (test-temporaire | production), CONFIRMATION (RESTAURER pour la production), NICHES_CLE.
# Toute vérification qui échoue arrête le script AVANT d'écrire la base de production.
set -euo pipefail
ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$ICI/base.sh" cle_presente

compter() {   # nombre d'annonces / observations d'une base (rien d'autre n'est affiché)
  python3 - "$1" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
def n(t):
    try:
        return con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except sqlite3.Error:
        return "—"
print(f"{n('annonces')} annonces, {n('observations')} observations, {n('rattrapage')} lectures de rattrapage, "
      f"{n('releves')} relevés")
PY
}

releve_en_ecriture() {   # 0 si un relevé est en train de modifier la base (avant sa sauvegarde)
  for id in $(gh run list --workflow releve.yml --status in_progress --json databaseId --jq '.[].databaseId'); do
    etat=$(gh run view "$id" --json jobs \
      --jq '[.jobs[].steps[] | select(.name | startswith("Sauvegarder la base")) | .status] | first // "inconnu"')
    if [ "$etat" != "completed" ]; then
      echo "Relevé n°$id en cours (étape de sauvegarde : $etat)."
      return 0
    fi
  done
  return 1
}

# ---------- 1. Quelle sauvegarde ?
case "$SOURCE" in
  precedente)  fichier="precedente.v2.gcm" ;;
  avant-lot1)  fichier="avant-lot1.v2.gcm" ;;
  quotidienne)
    if ! [[ "$DATE_SAUVEGARDE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
      echo "ERREUR : pour « quotidienne », indiquer la date au format AAAA-MM-JJ (reçu : « $DATE_SAUVEGARDE »)." >&2
      exit 1
    fi
    fichier="sauvegarde-$DATE_SAUVEGARDE.v2.gcm" ;;
  *) echo "ERREUR : source inconnue « $SOURCE »." >&2; exit 1 ;;
esac

# ---------- 2. Où ? (production, ou copie temporaire sans aucun risque)
if [ "$CIBLE" = "production" ]; then
  if [ "$CONFIRMATION" != "RESTAURER" ]; then
    echo "ERREUR : restauration en production refusée : taper RESTAURER dans « confirmation »." >&2
    exit 1
  fi
  R="donnees"
  echo "### CIBLE : PRODUCTION (Release « donnees »)"
elif [ "$CIBLE" = "test-temporaire" ]; then
  R="restauration-test"
  echo "### CIBLE : TEST sur une copie temporaire (Release « $R ») — la production n'est pas modifiée"
  gh release view "$R" >/dev/null 2>&1 || gh release create "$R" --prerelease --title "Test de restauration (copie temporaire)" \
    --notes "Copies de travail pour tester le workflow « Restaurer une sauvegarde ». Ne contient pas la production."
  mkdir -p copie-test
  for f in "$fichier" vinted.v2.gcm; do
    if gh release view donnees --json assets --jq '.assets[].name' | grep -qx "$f"; then
      (cd copie-test && gh release download donnees -p "$f" --clobber)
      gh release upload "$R" "copie-test/$f" --clobber   # dans la Release de TEST uniquement
    fi
  done
else
  echo "ERREUR : cible inconnue « $CIBLE »." >&2
  exit 1
fi

mkdir -p travail && cd travail
actifs=$(gh release view "$R" --json assets --jq '.assets[].name')
if ! echo "$actifs" | grep -qx "$fichier"; then
  echo "ERREUR : $fichier introuvable dans la Release « $R ». Rien n'a été modifié." >&2
  exit 1
fi

# ---------- 3. Vérification complète de la sauvegarde (AES-256-GCM + intégrité SQLite)
echo "== 1. Vérification de la sauvegarde choisie : $fichier"
gh release download "$R" -p "$fichier" --clobber
bash "$ICI/base.sh" dechiffrer "$fichier" restauree.db     # échec = arrêt (code 3), rien n'est écrit
echo "   authentique (AES-256-GCM) et intègre (SQLite) : $(compter restauree.db)"
echo "   SHA-256 fichier : $(sha256sum "$fichier" | cut -d' ' -f1)"

# ---------- 4. Aucun relevé ne doit être en train d'écrire la base (production)
echo "== 2. Relevé en cours d'écriture ?"
if [ "$R" = "donnees" ] && releve_en_ecriture; then
  echo "ERREUR : un relevé est en train d'écrire la base. Réessaie dans quelques minutes. Rien n'a été modifié." >&2
  exit 1
fi
echo "   non"

# ---------- 5. Copie de sécurité de la base actuelle (jamais écrasée)
echo "== 3. Copie de sécurité de la base actuelle"
horodatage=$(date -u +%Y%m%dT%H%M%SZ)
if echo "$actifs" | grep -qx vinted.v2.gcm; then
  mkdir -p actuelle
  (cd actuelle && gh release download "$R" -p vinted.v2.gcm --clobber)
  if bash "$ICI/base.sh" dechiffrer actuelle/vinted.v2.gcm actuelle.db > /dev/null 2>&1; then
    nom="avant-restauration-$horodatage.v2.gcm"
    echo "   base actuelle lisible : $(compter actuelle.db)"
  else
    nom="avant-restauration-$horodatage-illisible.v2.gcm"
    echo "   base actuelle ILLISIBLE : gardée telle quelle pour analyse"
  fi
  mkdir -p securite && cp actuelle/vinted.v2.gcm "securite/$nom"
  gh release upload "$R" "securite/$nom"                    # sans --clobber : n'écrase jamais
  mkdir -p controle-securite && (cd controle-securite && gh release download "$R" -p "$nom")
  cmp "securite/$nom" "controle-securite/$nom"
  echo "   copie de sécurité publiée et vérifiée : $nom"
else
  echo "   aucune base actuelle (vinted.v2.gcm absent) : rien à copier"
fi

# ---------- 6. Mise en place
echo "== 4. Mise en place de $fichier comme vinted.v2.gcm"
if [ "$R" = "donnees" ] && releve_en_ecriture; then
  echo "ERREUR : un relevé vient de démarrer son écriture. Arrêt avant remplacement (copie de sécurité gardée)." >&2
  exit 1
fi
mkdir -p envoi && cp "$fichier" envoi/vinted.v2.gcm
gh release upload "$R" envoi/vinted.v2.gcm --clobber

# ---------- 7. Contre-vérification depuis la Release
echo "== 5. Contre-vérification"
mkdir -p controle && (cd controle && gh release download "$R" -p vinted.v2.gcm --clobber)
cmp "$fichier" controle/vinted.v2.gcm
bash "$ICI/base.sh" dechiffrer controle/vinted.v2.gcm controle.db > /dev/null
cmp restauree.db controle.db
echo "RESTAURATION TERMINÉE ($R) : $fichier -> vinted.v2.gcm | $(compter controle.db)"
