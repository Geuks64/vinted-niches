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
  titre via `og:title`, prix via le bloc `"price":{"amount":"2","currency_code":"EUR"},"title":…` — SANS `seller_id`
  sur une annonce vendue ; jusqu'au 2026-11-08 20 h le motif exigeait `seller_id` -> 802 ventes du rattrapage sans
  prix ; secours `"originalAskingAmount":{"amount":…,"currencyCode":"EUR"}` = prix déjà converti en €).
- **États (lot 1)** : `vendu_probable` (signature de vente, 1 lecture), `vendu` (signal explicite ou signature revue
  12 h plus tard = contrôle de confirmation), `indisponible` (can_buy=false mais JSON-LD encore InStock : vacances ?
  jamais compté), `supprime`/`masque`/`inconnu` (exclus des stats). `reclasser()` ne rétrograde jamais un `vendu`.
- **Pays du vendeur : NON disponible** (vérifié le 2026-11-08) : les pages n'ont que `ipCountry` (= pays du
  VISITEUR, le serveur GitHub, « us ») et `countryCode` (« int », version du site) -> ignorés. Vue « France » vide.
- **Badge « En demande » : NON disponible** dans les données lisibles (cartes : `itemBox.badge` = `$undefined` ;
  pages : seulement des textes de traduction « Badge Pro », « Dressing au top »).
- **Vendeur pro** : clé `business` (true/false) sur la page d'une annonce ; `user.isBusiness` sur les cartes.
- **Limite de débit** : des réponses 429 sont apparues à 4 s entre requêtes avec relevés enchaînés -> 5 s et
  pause de 60 s après un 429. Ne pas augmenter la cadence.

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
  (depuis le 2026-11-08 17 h 40 : relance TOUJOURS, car le cron n'a tourné qu'une fois puis plus rien — la chaîne s'était arrêtée).
- **Top / Plus aimées / Recherche / France** (2026-11-08) : `stats.py` calcule 2 vues (`vues.tous`, `vues.fr` via
  `pays_de()` qui lit une clé *country* dans `brut` — nom exact de la clé à vérifier dans les logs), `top`
  (indice = 50 × (0,65 × vente/moyenne + 0,35 × j'aime/moyenne), petits échantillons ramenés vers la moyenne),
  `aimees`. Badge « en demande » lu sur les cartes (`itemBox.badge` → colonne `annonces.badge`) et les pages.
  Recherche : issue « Recherche : X » ouverte par le propriétaire → `recherche.yml` → `recherches/<n>.json`
  (commité) + commentaire ; `collecte.suivre_recherches()` ajoute ses annonces fraîches au suivi (colonne
  `annonces.recherche`).
- **Chiffrement : V2 UNIQUEMENT (AES-256-GCM authentifié)** — `chiffrage.py` (`cryptography==44.0.0`), appelé par
  `base.sh` (`chiffrer`, `dechiffrer`, `verifier`). Format : « NICHESv2 » + itérations PBKDF2 (600 000) + sel 16 o +
  nonce 12 o (nouveaux à chaque fichier) + données + étiquette 16 o ; en-tête authentifié. Secret `NICHES_CLE`.
  Le code ne sait PLUS lire l'ancien format V1 (AES-256-CBC) : `dechiffrer_v1` et les branches de secours V1 ont
  été retirés le 2026-11-08. Sans `vinted.v2.gcm` valide -> les workflows S'ARRÊTENT (aucun repli).
- **Sauvegardes (Release `donnees`)** : `vinted.v2.gcm` (production), `precedente.v2.gcm` (avant le dernier
  relevé), `avant-lot1.v2.gcm` (avant le lot 1), `sauvegarde-AAAA-MM-JJ.v2.gcm` (une par jour UTC, créée par le
  premier relevé du jour OU par le workflow « Sauvegarde du jour » à 00 h 17 / 12 h 17 UTC ; jamais écrasée),
  `avant-restauration-<horodatage>.v2.gcm` (copie de sécurité faite par chaque restauration).
  Les 3 fichiers V1 (`vinted.db.gz.enc`, `precedente.db.gz.enc`, `avant-lot1.db.gz.enc`) ont été SUPPRIMÉS le
  2026-11-09 (~01 h UTC) avec l'accord explicite de l'utilisateur (opération « A »), par le workflow à usage unique
  `supprimer-v1.yml` (run 42577706983, retiré ensuite) après vérification des 5 V2 ; SHA-256 notés dans le journal
  (05c443f1…, 26486e5b…, d543ee30…). Ne jamais supprimer de V2.
- **Workflows manuels** : `verifier-sauvegardes.yml` (restaure chaque *.v2.gcm, lecture seule),
  `restaurer.yml` + `restaurer.sh` (source precedente / avant-lot1 / quotidienne+date ; cible `test-temporaire`
  = Release séparée `restauration-test`, ou `production` avec confirmation RESTAURER ; vérifie GCM + SQLite,
  refuse si un relevé est en train d'écrire, copie de sécurité avant remplacement, contre-vérification),
  `sauvegarde-quotidienne.yml` + `sauvegarde_du_jour.sh`.
- **Publication de la page** : `publier-page.yml` republie GitHub Pages en ~2 min (push sur site/ ou stats.py,
  fin d'une recherche, ou à la main), en LECTURE SEULE sur la base. Les relevés continuent aussi de publier.
  L'onglet Recherche lit en plus les résultats directement dans `recherches/` du dépôt (API GitHub publique).
- **Tests de production du 2026-11-08 (16 h-17 h UTC)** : relevé n°19 (V2 seule) OK + création de
  `sauvegarde-2026-11-08.v2.gcm` ; « Vérifier les sauvegardes » : 4 V2 restaurables, 3 V1 ignorées ; restaurations
  en `test-temporaire` OK pour precedente, avant-lot1 et quotidienne 2026-11-08 ; production sans RESTAURER refusée.
  La Release de test `restauration-test` (pré-version, copies chiffrées) existe encore : ne la supprimer qu'avec
  l'accord de l'utilisateur.
- **Contrôle du 2026-11-09 00 h 50 UTC : OK** — `sauvegarde-2026-11-09.v2.gcm` créée à 00 h 02 UTC par le relevé
  42537050148 (le cron de « Sauvegarde du jour » de 00 h 17 ne s'est PAS déclenché) ; « Vérifier les sauvegardes »
  (run 42542374234) : 5 V2 restaurables, 3 V1 ignorées ; restauration test-temporaire de la quotidienne 2026-11-09
  (run 42542430660) OK (17 013 annonces, SHA-256 e4c56e4c…) ; Release `donnees` identique avant/après les tests ;
  `restauration-test` = 9 fichiers commençant tous par « NICHESv2 », utilisée seulement par restaurer.sh.
  (a) suppression des 3 fichiers V1 : FAITE (accord « A »). (b) suppression de la Release `restauration-test` :
  PAS autorisée (l'utilisateur n'a répondu que « A ») -> la garder ; ne la supprimer que sur accord explicite.
- **Lot 1 (2026-11-08)** : sauvegardes chiffrées dans la Release (voir ci-dessus).
  Schéma versionné (`PRAGMA user_version` = 2, `migrer()`), table `observations` (chaque lecture, jamais écrasée),
  stats : intervalles de Wilson (`ic_*`), `confiance` (0-100 = 100 − 2 × largeur de l'intervalle), `n_utilisees`,
  verdict `a_confirmer` sous 30 annonces, indice = 50 + confiance/100 × (brut − 50), vue « Particuliers » (pro = 0).
  NE PAS commencer le lot 2 (mots qui font vendre, prix d'achat max, tendances) sans accord de l'utilisateur.
- **Opportunités (2026-11-08)** : écran principal de l'appli (onglets : 💎 Opportunités, 🔎 Recherche, ☰ Plus = Top,
  Plus aimées, Ventes, Détails, Relevé + bascule Europe/France/Particuliers). `cible.py` = annonces NEUVES et SANS
  MARQUE (sans marque, la carte Vinted affiche le titre à la place de la marque). `collecte.probas_suivi()` donne
  `PART_CIBLE` (75 %) du budget de suivi aux cibles, budget total INCHANGÉ (TAUX_SUIVI × cartes : pas plus de requêtes).
  `stats.opportunites()` : par catégorie la plus fine, cibles seulement (suivi + rattrapage), ≥ N_MIN annonces et ≥ 2
  ventes ; prix de vente = médiane des ventes, achat max = V × (1 − 0,123 − 0,40) − 0,5, gain ≈ 0,40 × V ; classement
  = borne basse de Wilson × gain ; 10 types max, 12 vraies annonces vendues en exemple. Les prix chinois ne sont PAS
  lus automatiquement (bouton « Trouver en Chine »).
- **Test catalogue filtré (2026-11-08, `diag_catalogue.py` / `diag-catalogue.yml`, manuel, lecture seule)** : filtre
  « Neuf » = `status_ids[]=6&status_ids[]=1` (100 % neuves). Listes plafonnées à 960 annonces et NON classées par date
  (annonces de 10 min et de 3 semaines mélangées) -> suivre les ventes par « disparition de la liste » n'est PAS fiable.
  Utilisation retenue : relevé = 3 pages « tout » + 3 pages « Neuf » (`PAGES`, `PAGES_NEUF`), en ne gardant des pages
  Neuf que les annonces de moins de `FRAIS_H` (1 h, âge d'après le numéro ; vu_le = heure estimée de publication).
  Livres / CD / films (« Livres et médias ») : jamais « de marque » sur Vinted -> exclus des cibles (`cible.py`) et
  leur suivi s'arrête au 1er contrôle (`arreter_si_hors_cible`).
- **P1 vérifié (2026-11-09) : volume INSUFFISANT** — `produits.py` (regroupement prudent : identique ≥ 0,60 + ≥ 2 mots
  informatifs + même catégorie + prix ×2,5 ; similaire 0,35-0,60 jamais fusionné ; titres vagues écartés) +
  `diag_produits.py` (diagnostic workflow « Diagnostic », test=produits, 0 requête) : 654 annonces -> 544 produits dont
  542 à 1 annonce. L'échantillon aléatoire ne suffit pas -> P2.
- **PILOTE P2 « zoom automatique » (autorisé le 2026-11-09, conditions STRICTES de l'utilisateur)** : `zoom.py`, appelé
  par `collecte.main()` entre contrôles et rattrapage. Quota `NICHES_ZOOM` = 10 requêtes/relevé, FIXE (aucun passage
  à 20/30 sans nouvel accord ; 0 = désactivé). Le rattrapage perd exactement les requêtes utilisées par P2
  (`limite = RATTRAPAGE_MAX − req_zoom`). Compteur `Vinted.requetes` -> colonnes `releves.requetes` / `req_zoom` +
  `comparer_requetes()` (24 h exactes vs estimation d'avant). Découverte 3 jours (`zoom_etat.debut`), puis les produits
  terminent leurs passages (24 h / 3 j / 7 j) avant le bilan final (~10 jours). Une annonce absente d'une recherche =
  « disparue », JAMAIS vendue : page lue -> vendu_probable / vendu (confirmation 12 h) / supprime / masque / reserve /
  indisponible / indetermine / autre_categorie / en_vente (fausse disparition). 403/429 -> P2 s'arrête + pause 6 h ;
  erreur réseau / réponse inhabituelle -> arrêt pour le relevé. Tables `zoom_etat`, `zoom_produits`, `zoom_annonces`,
  `zoom_journal` (même base chiffrée, rien changé au chiffrement / sauvegardes / workflows). Bilan dans le journal de
  chaque relevé (`zoom.bilan`). PAS d'écran final avant validation des résultats par l'utilisateur.
  1er relevé du pilote (2026-11-09 09 h 35 UTC) : 5 sondages tous abandonnés (0-3 % identiques) + blocage de la règle
  25 % -> corrigés : cœur du produit = mots de la requête (pas tout le titre d'origine), règle 25 % appliquée seulement
  si une autre catégorie a des candidats, « ß » -> « ss ». Plafond STRICT de requêtes par relevé = maximum estimé des
  relevés des 3 jours d'avant le pilote (`plafond_requetes`, gardé dans `zoom_etat`) : le rattrapage s'arrête au plafond.
- Le workflow pousse un commit `.maintien` 1 fois/mois : faire `git pull` avant de pousser.
- Ancien diagnostic (étape 0) : dépôt `vinted-auto-annonce`, branche `claude/confident-mayer-okdotd`,
  `niches/diag.py` + workflow `diag-niches.yml` (et copie de l'outil dans `niches/outil/`).
