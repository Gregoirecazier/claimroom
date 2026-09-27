# Évaluation de l’UX et du parcours applicatif — 16 cas

[Vue d’ensemble](evaluation-matrix.md) · [Évaluation des agents](evaluation-agents.md) · [Guide UX assureur](../insurer-review-ux.md)

Cette matrice mesure ce que l’utilisateur peut consulter et faire, et la façon dont l’application conserve et applique ses actions. Elle inclut les contrôles serveur nécessaires au parcours : autorisations, validation des montants, approbation, versions, stockage et prévention des doubles envois. La colonne « Vérification » distingue les contrôles d’interface de ceux à vérifier aussi par l’API ; un bouton masqué ne suffit pas à prouver un blocage serveur.

Les 16 cas couvrent livraison du SMS et dépôt S04–S05, consultation et amendement U01–U07, approbation / envoi / persistance A01–A07. Les IDs historiques sont conservés. La qualité du texte du SMS, de l’extraction et du raisonnement est évaluée dans la matrice agents.

Utiliser des dossiers et sorties d’agents figés pour isoler l’UX : connaître à l’avance le récit, les pièces, l’analyse, le montant et le destinataire attendus. Comparer ensuite l’affichage et les actions à ces données de référence. Une mauvaise analyse du modèle est un échec agent ; une mauvaise association ou restitution de cette analyse est un échec applicatif. Des tests de bout en bout peuvent compléter les deux suites en conservant des verdicts séparés.

**Périmètre actuel :** G1 est intégré dans l’application authentifiée ; G2/G3 sont versionnés mais restent à relier aux dossiers. Les critères de dossier final chiffré et d’export sont des objectifs produit. Le brouillon de demande actuel permet montant et devis facultatifs : ne pas déclarer ces objectifs déjà implémentés, ni les confondre avec les contrôles du brouillon existant.

## Matrice

| ID | Parcours utilisateur | Action / scénario | Résultat attendu de l’application | Vérification |
| --- | --- | --- | --- | --- |
| S04 | SMS | Retry ou livraison échouée. | Statut conservé, échec visible, même SMS non envoyé plusieurs fois par erreur. | Interface + API / service SMS |
| S05 | SMS / dépôt | Deux dossiers ou mauvais lien d’accès. | Dépôt rattaché uniquement au dossier autorisé ; pas de mélange de pièces. | Interface + API / stockage |
| U01 ⭐ | Compte rendu | Ouvrir le compte rendu ; utiliser un dossier complet de référence pour le parcours de préparation à la revue. | Récit, lieu/date, véhicules assuré/tiers, photos agrandissables et vidéo accessibles au bon dossier. Vérifier le passage serveur à `review_ready` selon ses conditions actuelles et son affichage cohérent. | Interface + API |
| U02 | UX assureur | Vidéo absente, illisible ou lien inaccessible. | Manque ou erreur visible ; ne pas présenter l’analyse vidéo comme réalisée. | Interface + API |
| U03 ⭐ | Coût — fixture de test | Fournir une estimation synthétique séparée : 480 + 180 + 260 + 240 + 80 €. | Total TTC 1 240 € ; origine fictive visible, jamais présenté comme un montant extrait des images. | Interface / calcul |
| U04 | Coût | Montant négatif, invalide, non fini ou trop de décimales. | Enregistrement refusé avec correction demandée ; calculs en centimes. | Interface + API |
| U05 | Devis — cible dossier final chiffré | Devis absent (C01) ou total différent de l’estimation. | Manque ou incohérence visible ; validation du dossier final chiffré bloquée jusqu’à résolution selon les règles à implémenter. Le brouillon de demande actuel peut rester possible sans devis : ne pas confondre les deux parcours. | Interface + API — cible produit |
| U06 | Amendement humain | Gestionnaire modifie montant, récit ou commentaire. | Nouvelle version, total et message recalculés, modification traçable. | Interface + API |
| U07 | UX / package — cible export | Préparer un package ou aperçu pour une version déterminée. | Bon compte rendu, montant sourcé, bonnes pièces et destinataire. Préserver les autorisations du stockage privé ; une URL signée temporaire ne constitue pas une pièce durable. Aucun lien blob local ni média substitué. L’ancien export HTML public n’est plus le parcours actuel. | Interface + export / stockage — cible produit |
| A01 ⭐ | Validation | Gestionnaire approuve une version complète puis confirme la transmission. | Message et package correspondant à cette version ; reçu explicite, statut simulé/réel sans ambiguïté. | Interface + API / transmission |
| A02 | Envoi sans validation | Aucune approbation humaine. | Envoi impossible ; règle appliquée côté serveur pour l’intégration. | API + interface |
| A03 | Validation | Montant, récit, destinataire ou pièce modifié après validation. | Ancienne approbation invalidée ; nouvelle revue exigée. | API + interface |
| A04 | Validation | Envoi d’une version ancienne après un amendement. | API refuse la version obsolète ; aucun package non approuvé transmis. | API |
| A05 | Validation | Double clic, retry ou réponse réseau tardive. | Un seul envoi par version / clé d’idempotence ; même reçu retourné. | API + interface |
| A06 | Validation | Mauvais rôle ou autre organisation. | API refuse approbation et envoi ; masquer un bouton seul est insuffisant. | API + interface |
| A07 | Persistance | Rechargement du navigateur puis réouverture autorisée du dossier. | Retrouver les pièces, le brouillon, l’approbation encore valide et les reçus persistés côté serveur ; renouveler les liens signés expirés sans perdre les pièces. | Interface + API / stockage |

## Résultats à enregistrer

| ID | Version application | Dossier / rôle / données de référence | Action effectuée | Affichage / réponse API / état conservé | Verdict | Preuve |
| --- | --- | --- | --- | --- | --- | --- |
| À renseigner | À renseigner | À renseigner | À renseigner | À renseigner | Non testé | Capture, réponse API ou trace |

Verdicts : réussi / échoué / non testé / non applicable. Indiquer les contrôles vérifiés en interface et ceux vérifiés côté serveur. Un contrôle requis mais non implémenté reste à couvrir ; ne pas le classer « non applicable » uniquement pour améliorer le score. Conserver ces résultats séparés de ceux des agents.

## Vérifications disponibles

Depuis `apps/api`, après `uv sync --locked --dev` :

```sh
uv run --locked pytest
```

La suite API couvre notamment les actions de revue et le stockage privé des médias. Les tests d’intégration PostgreSQL nécessitent une base de test dédiée ; ils peuvent être ignorés localement sans cette base, alors que la CI la configure. Cette suite ne constitue pas à elle seule une validation des 16 parcours UX.

Depuis `apps/web` :

```sh
npm run build
```

Le build vérifie la compilation du front ; il ne remplace pas les interactions de cette matrice. Tester les parcours dans l’application authentifiée et vérifier les réponses serveur pour les contrôles indiqués. Les neuf tests de l’ancienne maquette locale ne sont plus la suite actuelle.
