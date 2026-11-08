# Règles du projet vinted-niches

- Travailler directement sur `main` (les tâches planifiées GitHub ne tournent que sur la branche par défaut).
- Code commenté en français, simple à lire.
- L'utilisateur n'a pas de PC : tout tourne dans GitHub Actions, le tableau de bord se consulte sur iPhone.
- Le conteneur de développement Claude ne peut PAS joindre vinted.fr : tester via le workflow
  (`releve.yml`, lancé à la main) et lire ses logs.
- Rester poli avec Vinted : pas de rafales (un blocage 403 est apparu après ~80 requêtes en quelques
  secondes ; à 4-5 s entre requêtes, aucun blocage constaté).
- Réponses à l'utilisateur : courtes, en listes, avec les liens directs.

## Ce qu'on sait du site Vinted (diagnostic du 2026-11-08)

- Accès anonyme OK depuis GitHub Actions avec `curl_cffi` (profil Chrome) ; cookies via la page d'accueil.
- L'ancienne API `/api/v2/catalog/items` et `/api/v2/items/{id}` renvoient **404** (supprimées).
  `/api/v2/brands` et `/api/v2/item_upload/catalogs` (arbre des catégories) marchent encore.
- Le site est en Next.js : les données sont dans `self.__next_f.push([1,"..."])` (voir `flux_next`).
- Page catalogue `/catalog?order=newest_first&page=N` : 96 annonces/page, ~10-14 pages max
  (≈ les 20 dernières minutes). Filtre catégorie : `/catalog?catalog[]=ID`, marque : `brand_ids[]=ID`.
  Chaque annonce = objet `productItem` (id, title, price, favouriteCount, itemBox.firstLine = marque,
  itemBox.secondLine = « taille · état », user.isBusiness, thumbnailUrl).
- Page annonce `/items/ID` (~1,5 Mo) : `can_buy`, `is_reserved`, `is_hidden`, `favourite_count`,
  `catalog_id`, `breadcrumbs` (chemin de catégorie), JSON-LD `offers.availability` (`InStock` si en vente).
- **Annonce vendue (constaté le 2026-11-08 via le rattrapage)** : `can_buy=false`, `instant_buy=false`, ni
  `is_reserved` ni `is_hidden`, et plus de fiche JSON-LD (pas d'`availability`, pas de prix ni de titre JSON-LD →
  titre via `og:title`, prix via le bloc `"price":{"amount":...}`). `statut_depuis()` classe ce cas en « vendu ».
  Hypothèse probable mais à confirmer (le mode vacances d'un vendeur pourrait donner la même chose) : les clés
  contenant sold/closed/transaction sont maintenant gardées dans `brut` pour vérifier. `reclasser()` réapplique
  la règle à toutes les lignes à chaque relevé.

## État en cours

- 2026-11-08 : outil en ligne sur `main`. 1er relevé réel OK (run #1) : 572 annonces lues, 561 nouvelles,
  37 suivies, base sauvegardée dans la Release `donnees`. Seul échec : déploiement GitHub Pages (pas encore
  activé par l'utilisateur → https://github.com/Geuks64/vinted-niches/settings/pages, Source « GitHub Actions »).
- À faire après le 1er jour : vérifier la détection des ventes (onglet « Relevé » → indicateurs bruts : à quoi
  ressemble une annonce vendue ?), la charge (contrôles en retard), et ajuster `NICHES_TAUX` si besoin.
- **Rattrapage** (ajouté le 2026-11-08) : `rattrapage()` lit à chaque relevé jusqu'à 120 annonces publiées il y a
  ~1 j / 3 j / 7 j (numéro tiré au hasard ; âge estimé via la table `reperes` = numéro max vu à chaque relevé,
  débit mesuré, ~75-80 numéros/s). Résultats dans la table `rattrapage`, fusionnés dans les stats par tranche d'âge
  (`TRANCHES_AGE` dans stats.py). Les numéros absents (404 : brouillons, supprimées) sont écartés.
- **Devises** : le rattrapage tombe sur des annonces de tous les pays (HUF, PLN, CZK...). La devise est lue
  (`priceCurrency` JSON-LD ou `currency_code` du bloc prix de l'annonce) et le prix converti en € (`TAUX_EUR`,
  taux approximatifs). Les ~360 lignes de rattrapage d'avant ce correctif ont leur prix effacé.
- **Planification** : le cron GitHub (`7,27,47 * * * *`) ne s'était toujours pas déclenché 1 h après la création
  du dépôt. Étape « Relance de secours » : si aucun relevé planifié dans les 45 dernières min et aucun relevé en
  attente, le workflow attend que 20 min soient écoulées depuis son début puis relance `releve.yml`
  (s'arrête tout seul dès que le cron marche).
- **Top / Plus aimées / Recherche / France** (2026-11-08) : `stats.py` calcule 2 vues (`vues.tous`, `vues.fr` via
  `pays_de()` qui lit une clé *country* dans `brut` — nom exact de la clé à vérifier dans les logs), `top`
  (indice = 50 × (0,65 × vente/moyenne + 0,35 × j'aime/moyenne), petits échantillons ramenés vers la moyenne),
  `aimees`. Badge « en demande » lu sur les cartes (`itemBox.badge` → colonne `annonces.badge`) et les pages.
  Recherche : issue « Recherche : X » ouverte par le propriétaire → `recherche.yml` → `recherches/<n>.json`
  (commité) + commentaire ; `collecte.suivre_recherches()` ajoute ses annonces fraîches au suivi (colonne
  `annonces.recherche`).
- Le workflow pousse un commit `.maintien` 1 fois/mois : faire `git pull` avant de pousser.
- Ancien diagnostic (étape 0) : dépôt `vinted-auto-annonce`, branche `claude/confident-mayer-okdotd`,
  `niches/diag.py` + workflow `diag-niches.yml` (et copie de l'outil dans `niches/outil/`).
