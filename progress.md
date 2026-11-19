# Suivi — Niches Vinted

## 2026-11-19
- **Vérification (demandée)** : le robot tourne bien (relevé toutes les ~20 min, tous réussis ce matin) ;
  tableau de bord à jour (stats.json du 19/11 12 h 43) : 62 512 annonces, 17 512 ventes repérées.
- Panneau « Trouver en Chine » testé sur PC : s'ouvre, Google Lens reçoit la photo, AliExpress trouve le produit
  (ex. « Jeu boxe musical »). Pas testé sur iPhone. Limite : si la photo de l'annonce est mauvaise (carton), Lens ne trouve rien.
- **Défauts repérés, pas corrigés** : (1) annonces de tous les pays Vinted mélangées (titres allemands/espagnols,
  marques US/UK : George, F&F, Member's Mark) car le serveur GitHub voit la version « internationale » ;
  (2) certains taux dépassent 100 % (« 112,8 % vendus en 7 j ») ; (3) beaucoup de lignes « À confirmer ».
- **Bouton Shein ajouté** au panneau Chine (recherche par mots), **vérifié sur iPhone** (« Baskets Saucony » → résultats Shein). Shein peut demander une vérification anti-robot la 1re fois. Pas de recherche par photo par lien chez Shein.
- **Corrigé** : l'onglet Opportunités (neuf sans marque) comptait des marques quand le titre = la marque (« Saucony » seul) : ces annonces sont écartées si le titre est une marque vue ailleurs (stats.py). Reste : des titres comme « UGGS in braun » passent encore.
- **France seulement (demandé)** : le pays du vendeur n'est lisible nulle part (revérifié depuis le PC en France :
  ni la page annonce ni la page membre ne le donnent). À la place, `cible.py` garde les annonces dont le **titre est
  en français** (`titre_francais` : mots français > mots étrangers, pas de lettres ß/ł/ñ…). Effets : Opportunités ne
  suit plus que les annonces neuves sans marque en français ; la bascule « France » des autres onglets se remplit.
  Limite : un titre d'un seul mot inconnu (« Stanley ») est écarté. Test : `python test_cible.py`.
  Sur 8 522 titres du tableau de bord, ~1 466 sont reconnus français : moins de données, donc plus de « À confirmer » au début.
- **Vérifié (relevé de 16 h 25)** : les 10 opportunités ne montrent presque plus que des titres français
  (2 965 annonces suivies, 113 types) ; la vue « France » se remplit (150 lignes dans Top). Restait de l'espagnol
  (« Bota alta marrón ») : lettres á í ó ú ajoutées aux lettres étrangères.
- **Taux > 100 % corrigé** : venait du Top (`taux_7j_estime`), qui ramenait un taux 24 h / 3 j à 7 j en le
  multipliant par le ratio moyen (87,5 % × 1,3 = 113,5 %). Nouvelle fonction `taux_7j_estime` (stats.py) : projette
  la part INVENDUE, ne dépasse jamais 100 %. Test : `python test_stats.py`. Vérifié sur le relevé de 17 h 06 :
  plus aucun taux au-dessus de 100 % (max 100).
- **Prix AliExpress automatiques (projet, demandé le 19/11)** : Shein et Temu bloquent les robots (case « Je suis
  humain ») ; AliExpress a un accès officiel gratuit (programme partenaire + Open Platform, recherche
  `aliexpress.affiliate.product.query`). Étape 1 faite le 19/11 à 21 h 37 : inscription « Portals » envoyée
  (Non-network > Third-party tools > Others, site Niches Vinted, France), **en cours de validation** (1 h à 2 jours, réponse par e-mail).
  Ensuite : compte développeur sur openservice.aliexpress.com, créer une appli « Affiliates » -> App Key + App Secret
  (+ tracking ID), les mettre en secrets GitHub (jamais dans le chat), puis brancher la recherche de prix au robot.
- **Prochaine étape** : voir si les titres avec marque (« Bottes UGG ») doivent être écartés des Opportunités.

- Onglet 🔎 Recherche : bouton « 📷 Chercher par photo sur Vinted » (19/11 soir). Vinted n'a pas de recherche photo sur le web. Google Lens refuse les envois venant d'un autre site (erreur 403) : la photo (réduite à 1200 px) part donc chez Bing, puis ajouter « vinted » dans sa barre. Testé de bout en bout sur PC ; à essayer sur iPhone.

## 2026-11-18
- **Fait (11-12/11)** : panneau « Trouver en Chine » amélioré, en ligne.
  - Bouton DHgate ajouté (1688 non : demande des mots en chinois et un compte).
  - Mots de recherche nettoyés : ponctuation, émojis, « neuf / nuove / made in… », tailles retirés, 6 mots max.
  - Recherche par photo : Google Lens + Bing + Yandex (testés, ils retrouvent le produit).
  - AliExpress, Temu, Alibaba n'acceptent pas une photo par lien (testé) : bouton « 📥 Ouvrir la photo » pour
    l'enregistrer puis la chercher avec l'icône 📷 dans leur appli. La photo reste la miniature (Vinted bloque le grand format).
- **À savoir** : l'utilisateur trouve que ça « ne marche pas trop » ; on ne sait pas encore quel bouton ni sur quel appareil.
  Rien n'a été testé sur iPhone.
- **Prochaine étape** : savoir quel bouton marche mal (sur iPhone ?) et corriger.

## 2026-11-09
- **Où on en est** : le robot tourne tout seul (relevés toutes les ~20 min) et le tableau de bord est en ligne : https://geuks64.github.io/vinted-niches/
- Top du 09/11 (articles neufs sans marque) : déco maison, vêtements homme, vêtements femme, hauts femme, jouets enfants. Gain estimé 1 à 5 € par pièce.
- **Décidé** : tous les résultats sont encore « À confirmer » (3 à 12 ventes chacun). Ne pas acheter de stock avant ~1 semaine de données.
- **Prochaine étape** : relire le tableau de bord pour voir si les « À confirmer » sont devenus fiables.

## 2026-11-11 (d'après CLAUDE.md)
- Pilote « zoom automatique » **arrêté** : pas assez de volume pour repérer des produits précis. L'écran « Opportunités » (par type d'article) reste l'écran principal.
