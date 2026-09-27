# Parcours automatique d’indemnisation

Le parcours actif est : appel → lien SMS privé → photos → comparaison aux vidéos du catalogue → estimation → validation ou modification par le gestionnaire → SMS à l’assuré et email à l’assureur du tiers. Le traitement ne fait appel ni à Camérci, ni à une ville, ni à Dust. Les anciennes routes restent disponibles pour les dossiers historiques.

## Analyse

La réception d’une pièce enregistre un travail durable, sans attendre le modèle. Tous les canaux raccordés à `EvidenceService` (dépôt privé, gestionnaire, WhatsApp) utilisent ce même traitement. Le SMS invite au dépôt privé ; un simple SMS texte ne transporte pas de photo.

`AstraAccidentAnalyzer` appelle explicitement `gpt-6-astra` avec les photos reçues et les images horodatées des **trois vidéos**, sans scénario, noms de fichiers ni réponses de la base d’assureurs. Une seule correspondance forte permet de retenir une vidéo. Le modèle propose les événements, plaques, dégâts, responsabilités par véhicule et une fourchette en centimes EUR pour le véhicule assuré. Il ne présume pas celui-ci innocent.

[Astra accepte les images en entrée](https://developers.openai.com/api/docs/models/gpt-6-astra), mais pas les vidéos natives. Le catalogue utilise 4 images par seconde, préextraites avec `scripts/build_video_catalogue.py`. Les clips envoyés par l’assuré sont décodés de la même manière, avec une limite de 20 secondes, 15 pièces et 100 Mio par analyse. Ces limites, les plaques illisibles et les dommages cachés restent explicites. L’estimation est indicative, pas un devis de garage.

Les conclusions doivent respecter le schéma et citer exclusivement les pièces reçues et les instants fournis de la vidéo retenue. Une conclusion mal formée ne devient pas un rapport. Une ambiguïté matérielle génère des questions dans le chat de l’assuré ; une correction confirmée ou une nouvelle photo invalide l’ancienne proposition et déclenche une nouvelle analyse.

Les plaques lisibles sont recherchées exactement dans le registre synthétique existant : 1 000 plaques françaises et 1 000 britanniques, enrichies de conducteurs fictifs et d’adresses d’assureurs en `.test`. Aucune identité n’est extraite des personnes visibles sur les images.

## Décision et notifications

Le gestionnaire conserve la seule décision obligatoire : valider le montant proposé ou saisir un montant modifié avec motif. Une proposition incomplète ou périmée ne peut pas être validée. L’approbation et les deux notifications sont enregistrées dans la même transaction. Une double validation ne crée aucun nouvel envoi.

Le worker reprend les notifications en attente après un redémarrage. Un timeout après une tentative d’envoi est marqué `unknown` et n’est pas automatiquement renvoyé, pour éviter les doubles SMS. `sent` signifie accepté par le prestataire, pas nécessairement livré sur le téléphone ou dans la boîte de réception. Les échecs sont visibles dans le dossier.

Par défaut, les envois sont explicitement **simulés**. En mode réel, puisque les assureurs du registre sont fictifs, l’email va à la boîte de démonstration configurée. Le SMS va au mobile auquel le lien de dépôt a été envoyé. Ne jamais utiliser les numéros de fixtures pour un test réel.

## Configuration et mise en service

1. Appliquer `uv run alembic upgrade head` (tête `20260926_merge_astra_voice`, incluant `20260926_astra_journey`). La migration Astra crée le catalogue, les propositions, la file de notifications et le déclencheur de réanalyse après correction.
2. Configurer `OPENAI_API_KEY` côté API, avec accès à `gpt-6-astra`. Aucun repli silencieux sur un autre modèle.
3. Publier le prompt vocal v15 et la configuration Vapi versionnée. La clôture annonce le SMS automatique et demande les vues du véhicule, de la plaque et des dommages.
4. Configurer `SMS_LINK_DELIVERY_MODE=twilio`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_SMS_MESSAGING_SERVICE_SID` et `TWILIO_SMS_WEBHOOK_BASE_URL`, ainsi que les paramètres existants du portail privé et du stockage Supabase.
5. Pour les notifications réelles : `CLAIM_NOTIFICATION_MODE=live`, `RESEND_API_KEY`, `CLAIMROOM_EMAIL_FROM`, `CLAIMROOM_EMAIL_REPLY_TO`, `CLAIM_DEMO_INSURER_EMAIL`. L’adresse expéditrice doit être autorisée dans Resend. Garder `simulated` pour les tests ordinaires.
6. Configurer le **même** `MEDIA_WORKER_TOKEN` aléatoire d’au moins 32 caractères sur l’API et le bridge Railway, et `MEDIA_WORKER_API_URL` sur Railway. Le worker existant appelle `/internal/media-workflow/tick` ; il assure la progression lorsque tous les navigateurs sont fermés. Le polling de l’interface n’est qu’une reprise supplémentaire. Le worker ne démarre pas sans cette configuration.
7. Déployer API, web et bridge. L’entrée Vercel `app.py` dispose de 300 secondes ; l’appel modèle est borné à 240 secondes, le worker à 290 secondes et le verrou de traitement à 6 minutes. Voir les [paramètres de durée Vercel](https://vercel.com/docs/functions/configuring-functions/duration).

Les clés restent dans les environnements serveur et ne sont jamais versionnées. Le pipeline de production existant impose un commit dont l’auteur est le propriétaire autorisé du projet Vercel ; cette refonte ne modifie pas cette règle de déploiement.

## Vérification reproductible

```sh
cd apps/api
uv sync --locked --dev
TEST_MIGRATION_DATABASE_URL='postgresql://postgres:testonly@127.0.0.1:5432/claims_test?sslmode=disable' \
APP_ENV=development DATABASE_ALLOW_INSECURE_LOCAL=true uv run pytest
```

`test_accident_journey.py` couvre l’appel, le SMS de dépôt idempotent, les photos déposées depuis la session privée, le déclenchement différé, la comparaison de toutes les vidéos, la validation amendée, les deux notifications, les corrections, les droits d’accès, les citations invalides et les envois incertains. Les prestataires sont remplacés dans ces tests déterministes.

Le test payant optionnel utilise les vraies photos et le vrai modèle, puis persiste la proposition et simule les notifications :

```sh
CLAIMROOM_LIVE_ASTRA_TEST=1 uv run pytest tests/test_accident_journey.py::test_live_astra_pipeline_through_persisted_review_and_notifications
```

Il nécessite `OPENAI_API_KEY` et la base de test ci-dessus. `CLAIMROOM_SMOKE_LEAVE_REVIEW=1` et `CLAIMROOM_SMOKE_RESULT=/tmp/claim-review.json` permettent de laisser la proposition pour une vérification manuelle dans l’interface. Le fichier contient uniquement les identifiants du dossier et du gestionnaire, aucun secret.

```sh
cd apps/web
npm test
npm run build
```

L’aperçu `npm run demo` reste sans réseau : l’analyse Astra et la validation automatique nécessitent l’API connectée et sont indiquées comme indisponibles dans cet aperçu.

## Vérification réelle du 26 septembre 2026

Les appels directs à Astra ont retrouvé les vidéos correspondantes pour les trois jeux de photos. Les cas ambigus restent soumis à des demandes de précision. Le parcours complet avec le scénario G1 a persisté une proposition issue du modèle réel (3 500–7 500 EUR), puis la validation dans l’interface locale a amendé le montant à 5 000 EUR. Les deux notifications réelles sont enregistrées une seule fois : SMS confirmé `delivered` par Twilio ; email accepté par Resend. Les coordonnées et secrets de test ne sont pas versionnés.

Les vérifications déterministes passent : 406 tests API (un test payant désactivé par défaut), 70 tests de composants web et 6 tests Node ; compilation web réussie. Le test Astra payant a également réussi séparément. Cela valide le parcours dans l’environnement de test ; la mise en service nécessite encore le déploiement et la configuration serveur ci-dessus.
