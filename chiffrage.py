"""Chiffrement AUTHENTIFIÉ de la base (format v2) : AES-256-GCM.

Utilisé par base.sh (ne pas appeler directement dans les workflows) :
    python3 chiffrage.py chiffrer   vinted.db  vinted.v2.gcm      (compresse puis chiffre)
    python3 chiffrage.py dechiffrer vinted.v2.gcm vinted.db       (vérifie l'authenticité, déchiffre, décompresse)

Format d'un fichier v2 (tout l'en-tête est AUTHENTIFIÉ : il sert de « données associées » à GCM) :
    octets 0-7    « NICHESv2 »                       identifiant du format
    octets 8-11   nombre d'itérations PBKDF2 (entier, gros-boutiste)
    octets 12-27  sel aléatoire (16 octets, nouveau à CHAQUE chiffrement)
    octets 28-39  nonce aléatoire (12 octets, nouveau à CHAQUE chiffrement)
    octets 40-…   données chiffrées (gzip de la base) + étiquette d'authentification GCM (16 derniers octets)

Clé : PBKDF2-HMAC-SHA256(secret NICHES_CLE, sel, itérations) -> 32 octets (AES-256).
Comme le sel est nouveau à chaque fichier, la clé elle-même change à chaque chiffrement : un même
couple (clé, nonce) ne peut jamais servir deux fois.
Toute modification (en-tête, sel, nonce, données, étiquette) ou troncature fait échouer le déchiffrement
(InvalidTag) : rien n'est écrit, code de sortie 3.
"""
import gzip
import os
import struct
import sys

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"NICHESv2"
ITERATIONS = 600_000          # recommandation OWASP pour PBKDF2-HMAC-SHA256
ITERATIONS_MIN = 600_000      # refuse un fichier qui annoncerait moins (pas de rétrogradation)
ENTETE = struct.Struct(">8sI16s12s")   # magic, itérations, sel, nonce -> 40 octets


def cle(secret, sel, iterations):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sel, iterations=iterations)
    return kdf.derive(secret.encode("utf-8"))


def secret_env():
    s = os.environ.get("NICHES_CLE", "")
    if not s:
        print("ERREUR : le secret NICHES_CLE est absent. Rien n'est écrit.", file=sys.stderr)
        sys.exit(2)
    return s


def chiffrer(entree, sortie):
    secret = secret_env()
    with open(entree, "rb") as f:
        donnees = gzip.compress(f.read(), mtime=0)
    sel, nonce = os.urandom(16), os.urandom(12)
    entete = ENTETE.pack(MAGIC, ITERATIONS, sel, nonce)
    chiffre = AESGCM(cle(secret, sel, ITERATIONS)).encrypt(nonce, donnees, entete)
    with open(sortie + ".tmp", "wb") as f:
        f.write(entete + chiffre)
    os.replace(sortie + ".tmp", sortie)


def dechiffrer(entree, sortie):
    secret = secret_env()
    with open(entree, "rb") as f:
        contenu = f.read()
    if len(contenu) < ENTETE.size + 16 or not contenu.startswith(MAGIC):
        print(f"ERREUR : {entree} n'est pas un fichier v2 complet (en-tête absent ou fichier tronqué).",
              file=sys.stderr)
        sys.exit(3)
    entete = contenu[:ENTETE.size]
    _, iterations, sel, nonce = ENTETE.unpack(entete)
    if iterations < ITERATIONS_MIN:
        print(f"ERREUR : en-tête refusé ({iterations} itérations < {ITERATIONS_MIN}).", file=sys.stderr)
        sys.exit(3)
    try:
        donnees = AESGCM(cle(secret, sel, iterations)).decrypt(nonce, contenu[ENTETE.size:], entete)
    except InvalidTag:
        print(f"ERREUR D'AUTHENTIFICATION : {entree} a été modifié, est abîmé, ou la clé est fausse. "
              "Rien n'est écrit.", file=sys.stderr)
        sys.exit(3)
    with open(sortie, "wb") as f:
        f.write(gzip.decompress(donnees))


def info(entree):
    """Affiche l'en-tête (sans rien déchiffrer) : sert aux tests (nonce / sel uniques)."""
    with open(entree, "rb") as f:
        magic, iterations, sel, nonce = ENTETE.unpack(f.read(ENTETE.size))
    print(magic.decode(), iterations, sel.hex(), nonce.hex())


if __name__ == "__main__":
    action, *fichiers = sys.argv[1:]
    {"chiffrer": chiffrer, "dechiffrer": dechiffrer, "info": info}[action](*fichiers)
