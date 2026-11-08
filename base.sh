#!/usr/bin/env bash
# Outils de la base SQLite (utilisés par les workflows) :
#   bash base.sh verifier  vinted.db                    -> PRAGMA integrity_check (échoue si la base est abîmée)
#   bash base.sh chiffrer  vinted.db  vinted.db.gz.enc  -> compresse + chiffre (AES-256, clé = secret NICHES_CLE)
#   bash base.sh dechiffrer vinted.db.gz.enc vinted.db  -> déchiffre + décompresse + vérifie
# La clé est lue dans la variable d'environnement NICHES_CLE (jamais écrite dans les journaux).
set -euo pipefail

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

chiffrer() {   # $1 = base SQLite en clair, $2 = fichier chiffré produit
  cle_presente
  gzip -c "$1" | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:NICHES_CLE -out "$2"
}

dechiffrer() {  # $1 = fichier chiffré, $2 = base SQLite en clair produite
  cle_presente
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:NICHES_CLE -in "$1" | gunzip -c > "$2.tmp"
  verifier "$2.tmp"
  mv "$2.tmp" "$2"
}

"$@"
