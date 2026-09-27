# Prochaines étapes — démo et evals

État au 25 septembre 2026, aligné sur l’application G1 authentifiée et les annotations corrigées par Greg.

## Déjà disponible

- Base fictive de **1 000 plaques FR et 1 000 plaques UK**, en JSON et CSV, avec export SQLite reproductible, lookup exact par plaque/pays/date et annuaire de correspondants fictifs. La version `mock-insurance-v2` inclut les six références de véhicules G1/G2/G3 et vingt probes de lookup, dont les contrôles d'incertitude G2.
- G1 (environ 5,04 secondes) et ses deux photos sont des fixtures API copiées dans le stockage privé de chaque dossier ; lecture par URLs signées temporaires.
- G2/G3 (environ 5,042 s chacun) et quatre photos approuvées sont disponibles dans les répertoires [G2](../../apps/api/claim_api/fixture_media/g2/README.md) et [G3](../../apps/api/claim_api/fixture_media/g3/README.md), avec empreintes et provenance. Photos de la Renault assurée en G2 et de la Toyota tierce en G3 ; semis applicatif encore à brancher.
- L’[UX assureur](../insurer-review-ux.md) utilise les dossiers authentifiés, brouillons versionnés, approbations et reçus simulés persistés côté serveur.
- Les [corrigés G1/G2/G3](corriges-videos-g1-g2-g3.md) reprennent les observations de Greg ; timecodes et certains détails restent à compléter. Les [26 tests d’agents](evaluation-agents.md) et les [16 tests UX/parcours applicatif](evaluation-ux.md) sont séparés ; la [vue d’ensemble](evaluation-matrix.md) explique leur périmètre.
- Un [runner d’analyse](../../apps/api/evals/README.md) exécute cinq cas synthétiques avec un analyseur simulé par défaut. L’extraction visuelle et la comparaison automatique des vidéos à ces corrigés restent à brancher.

Voir [la base et son mode d’emploi](../../apps/api/fixtures/mock-insurance/README.md). Le scénario applicatif G1 ne déduit pas encore les plaques depuis les médias ; son destinataire reste bloqué tant qu’aucune source vérifiée n’est ajoutée.

## Greg

| Ordre | Action | Livrable |
| --- | --- | --- |
| 1 | Relire l’application assureur et valider avec Ben les services réels/simulés. | Tester liste et ouverture des dossiers, pièces privées, revue, amendements et reçus ; fixer le périmètre voix/SMS/Vision/lookup/email. |
| 2 | Compléter les repères temporels G1. | Peugeot `FR-482-KL` et BMW `AB12 CDE` confirmées. BMW en marche arrière, arrière droit contre arrière gauche Peugeot, puis départ en marche avant. Garder l’incertitude sur les conducteurs. |
| 3 | Valider les annotations du fichier G2 et ses deux photos désormais versionnés. | Renault arrêtée, choc avant décalé avec Opel, déplacement à droite et légèrement en arrière. Plaque Opel `RK18…`, fin `LXP` / `LYP` incertaine ; plaque `XY34 ZTR` lisible sur la voiture bleue non impliquée dans le fichier reçu. |
| 4 | Valider les annotations du fichier G3 et ses deux photos Toyota désormais versionnés. | Citroën gris clair avançant, avant gauche contre milieu du côté passager Toyota ; faible déplacement de la Toyota puis retour près de sa position initiale. Récit de test volontairement contradictoire. |
| 5 | Finaliser les annotations à partir des rendus. | Relever timecodes, source de chaque plaque, occupants et côtés de contact ; confirmer les références G2/G3 conservées des scénarios. Corrigés réservés au correcteur. |
| 6 | Préparer un extrait après impact de G1 ; copie entièrement floutée optionnelle. | G2 couvre déjà l’incertitude de lecture. L’extrait ajoute la causalité non observable, sans génération payante ; ne pas joindre l’original au test dérivé. |
| 7 | Vérifier les photos G1 et préparer un devis fictif séparé. | Photos du bon véhicule, dégâts cohérents et origine du montant explicite. Les 1 240 € de démonstration ne sont pas un coût extrait de la vidéo. Les photos G2/G3 sont prêtes ; celles de G3 montrent la Toyota tierce, pas la Citroën assurée. |
| 8 | Utiliser les fixtures G1/G2/G3 après corroboration visuelle. | Les six références sont incluses dans `mock-insurance-v2` et ses exports, avec couverture active à la date de référence. Un match en base ne tranche pas `LXP` / `LYP`. Ne pas préremplir G1 avec la réponse du corrigé. |
| 9 | Préparer les récits vocaux et tester le SMS. | Récit complet/incomplet, accident récent/ancien, urgence ; résumé et demandes fidèles. |
| 10 | Préparer la banque jury et faire une répétition avec Ben. | Choix de médias prêts, SMS → dépôt → revue/amendement → validation/envoi simulé. Enregistrer attendu, obtenu et trace pour un parcours complet, un cas ambigu et un cas contradictoire. |

Les scènes supplémentaires restent optionnelles. Priorité à G1/G2/G3 et aux variantes par découpage ; plafond cinq scènes, avec une éventuelle sixième génération de correction. Les variantes d’assurance, devis et approbation réutilisent les mêmes médias.

## Ben / intégration

| Action | Résultat attendu |
| --- | --- |
| Relire les corrigés et les deux matrices. | Références versionnées, incertitudes conservées, aucune réponse attendue injectée dans le contexte de l’agent évalué ; résultats agents et UX séparés. |
| Relier G2/G3 à la banque jury et aux dossiers. | Utiliser les six médias versionnés, conserver les rôles assuré/tiers, semer les pièces dans le stockage privé puis servir des URLs signées, comme pour G1. |
| Brancher l’extraction visuelle des médias. | Plaques, véhicules, mouvements et dégâts sourcés par pièce/timecode ; lecture de vidéo distincte des métadonnées actuellement fournies à l’analyse. |
| Brancher le lookup après identification corroborée. | `provider_results` persistés avec `mode=mock`, source/version et UUID ; aucun accès à une vraie base assureur. |
| Brancher le lookup correspondant si pertinent. | Résultat positif ou manque explicite ; aucune route de recours inventée. |
| Relier voix, SMS, dépôt et analyse selon le périmètre retenu. | Dossier complet et suivi cohérent ; statut réel/simulé visible. |
| Compléter les evals d’agents. | Comparer Vision G1/G2/G3 aux corrigés ; couvrir voix, rédaction SMS, investigation, responsabilité et synthèse du dossier. Les cinq cas du runner actuel ne couvrent pas les 26 cas de la matrice agents. |
| Vérifier les 16 parcours UX avec des données figées. | Tester livraison SMS, dépôt, consultation, amendement, dossier final chiffré, export et validation ; vérifier les contrôles serveur et garder un bilan distinct des agents. |
| Vérifier les gates et l’idempotence. | Pas de recours sans approbation, modification matérielle invalidante, pas de doubles envois. |

Les deux mille enregistrements sont une base de simulation ; aucune migration Supabase n’est nécessaire pour la charger côté adaptateur. Leur présence dans le dépôt ne signifie pas que le lookup est déjà utilisé par l’application.

Depuis `apps/api`, après `uv sync --locked --dev`, lancer `uv run --locked pytest` pour les tests API et `uv run --locked python -m evals.runner` pour les cinq evals d’analyse simulée. Les tests d’intégration exigent une base PostgreSQL de test dédiée ; la CI la prépare. Pour le front, `npm run build` depuis `apps/web`. Ces commandes ne lancent pas une reconnaissance visuelle des vidéos.
