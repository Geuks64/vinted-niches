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
- **Prochaine étape** : décider s'il faut ne garder que la France, et corriger les taux > 100 %.

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
