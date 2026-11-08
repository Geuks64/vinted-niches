#!/usr/bin/env bash
# Outils de la base SQLite (utilisés par les workflows) :
#   bash base.sh verifier      vinted.db                     -> PRAGMA integrity_check (échoue si la base est abîmée)
#   bash base.sh chiffrer      vinted.db  vinted.v2.gcm      -> format v2 : AES-256-GCM AUTHENTIFIÉ (voir chiffrage.py)
#   bash base.sh dechiffrer    vinted.v2.gcm vinted.db       -> v2 : authentifie, déchiffre, vérifie l'intégrité
# La clé est lue dans la variable d'environnement NICHES_CLE (jamais écrite dans les journaux).
# Seul format accepté : v2 (AES-256-GCM). Un fichier absent, abîmé ou non authentique fait échouer la commande.
set -euo pipefail
ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cle_presente() {
  if [ -z "${NICHES_CLE:-}" ]; then
    echo "ERREUR : le secret NICHES_CLE est absent (GitHub > Settings > Secrets > Actions). Rien n'est publié." >&2
    exit 2
  fi
}

verifier() {
  python3 - "$1" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
r = con.execute("PRAGMA integrity_check").fetchone()[0]
v = con.execute("PRAGMA user_version").fetchone()[0]
tables = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
          for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
print(f"Intégrité : {r} | version du schéma : {v} | lignes : {tables}")
sys.exit(0 if r == "ok" else 1)
PY
}

chiffrer() {   # $1 = base SQLite en clair, $2 = fichier v2 produit
  cle_presente
  python3 "$ICI/chiffrage.py" chiffrer "$1" "$2"
}

dechiffrer() {  # $1 = fichier v2, $2 = base SQLite en clair produite (seulement si tout est valide)
  cle_presente
  rm -f "$2.tmp"
  if ! python3 "$ICI/chiffrage.py" dechiffrer "$1" "$2.tmp"; then
    rm -f "$2.tmp"
    exit 3
  fi
  verifier "$2.tmp"
  mv "$2.tmp" "$2"
}

"$@"
