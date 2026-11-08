# Niches Vinted

Outil qui relève en continu les nouvelles annonces Vinted, repère celles qui se vendent,
et calcule quelles **catégories / marques / tranches de prix** se vendent le plus vite → les niches.

👉 **Tableau de bord** : https://geuks64.github.io/vinted-niches/

## Comment ça marche

1. Toutes les 20 min, GitHub Actions (`.github/workflows/releve.yml`) lance `collecte.py` :
   - lit les ~570 dernières annonces publiées (catalogue « plus récents » de vinted.fr) ;
   - en garde ~7 % au hasard pour les **suivre** : l'outil revient voir chaque annonce suivie
     après 6 h, 24 h, 3 j et 7 j pour savoir si elle est **vendue** (ou réservée, supprimée...).
   - **rattrapage** : à chaque relevé, va aussi voir jusqu'à 120 annonces publiées il y a ~1 j, ~3 j
     et ~7 j (au hasard) pour savoir tout de suite si elles sont vendues → premiers chiffres en quelques heures.
2. `stats.py` calcule les statistiques (`site/stats.json`) :
   - % vendu en 24 h / 3 j / 7 j, délai médian de vente, prix de vente médian ;
   - par catégorie, marque, catégorie + marque, catégorie + tranche de prix ;
   - **score niche** = meilleur % de vente disponible, réduit quand l'échantillon est petit.
3. Le tableau de bord (`site/index.html`) est publié sur GitHub Pages à chaque relevé.

La base de données (SQLite) est gardée dans la Release **`donnees`** (fichier `vinted.db.gz`,
+ `sauvegarde.db.gz` une fois par jour). **Ne pas supprimer cette Release.**

## Mise en route (une seule fois)

1. Activer GitHub Pages : https://github.com/Geuks64/vinted-niches/settings/pages
   → *Source* : **GitHub Actions**.
2. Lancer un premier relevé : https://github.com/Geuks64/vinted-niches/actions/workflows/releve.yml
   → **Run workflow**. Ensuite il tourne tout seul toutes les 20 min.

## Quand regarder

- Après quelques relevés : premières ventes et premiers % de vente (grâce au rattrapage).
- Après ~24 h : les % se précisent avec le suivi des nouvelles annonces.
- Après ~7 jours : chiffres fiables pour choisir une niche.

## Réglages (variables `NICHES_*` dans `collecte.py` / `stats.py`)

| Variable | Défaut | Rôle |
|---|---|---|
| `NICHES_PAGES` | 6 | pages de catalogue lues par relevé (96 annonces/page) |
| `NICHES_TAUX` | 0.07 | part des nouvelles annonces suivies |
| `NICHES_PAUSE` | 4 | secondes entre deux requêtes vers Vinted |
| `NICHES_DUREE` | 780 | durée max d'un relevé (secondes) |
| `NICHES_N_MIN` | 8 | annonces minimum pour afficher un groupe |

## Limites à connaître

- Ce n'est **pas un flux de toutes les ventes** : Vinted ne publie pas ses ventes. L'outil suit un
  échantillon et en déduit des taux de vente — fiable pour comparer des niches, pas pour compter.
- Vente détectée = l'annonce n'est plus « en stock » sur sa page. Une annonce supprimée par le
  vendeur n'est pas comptée comme vendue.
- Le catalogue de vinted.fr montre aussi des annonces d'autres pays (titres en allemand, espagnol...).
- Les conditions d'utilisation de Vinted interdisent la collecte automatique : l'outil reste lent
  (une requête toutes les ~4 s, visiteur anonyme, aucun compte) et s'arrête s'il est refusé.
