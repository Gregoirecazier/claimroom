# Revue du dossier côté assureur

L’interface G1 est l’application principale à `/`. Elle reprend la présentation de la maquette du collègue et utilise les dossiers authentifiés de l’API. La liste, la déclaration, les pièces, l’analyse, les révisions de brouillon, l’approbation et les reçus simulés persistent côté serveur.

## Parcours

1. Connectez-vous avec un compte invité du projet Supabase.
2. Ouvrez un dossier existant en cliquant sur sa référence soulignée. Les boutons de création de fixtures ont été retirés de la liste. Pour préparer les données de test de l’application connectée, utilisez `POST /v1/cases` avec `scenario_id: "g1"`, puis `POST /v1/cases/{id}/demo-events/g1-media` avec la version courante. L’API copie les médias G1 dans le bucket privé `claim-evidence`. L’action **Charger les photos et la vidéo** reste disponible dans un G1 non initialisé.
3. Ouvrez les images et la vidéo à partir de liens de lecture signés pour cinq minutes. Les médias ne sont plus servis par Vite, ni copiés dans un export HTML public.
4. Modifiez la déclaration, ajoutez une image ou un PDF de 5 MiB maximum, puis lancez l’analyse. Les modifications changent la révision de contenu et invalident une approbation antérieure.
5. Si les contrôles déterministes passent, revoyez et amendez le brouillon versionné. L’approbation porte sur son empreinte exacte. L’enregistrement et l’envoi restent des actions simulées avec reçus persistants et clés d’idempotence.

Le scénario G1 ne fournit aucune plaque tierce ou correspondance d’assurance vérifiée. Ses images et sa vidéo ne déclenchent pas de reconnaissance automatique. Le contrôle « contrepartie » doit rester bloqué jusqu’à ce qu’une source fiable et un parcours de vérification soient ajoutés. Le scénario **complet** permet de tester les étapes de validation et d’envoi avec les résultats de lookup fictifs existants.

## Lecture et actions de revue

Les pièces précèdent le compte rendu et l’estimation. Le panneau de décision donne accès aux pièces, au devis et aux points à résoudre. Le traitement vidéo, les observations Vision, la transcription et l’historique restent accessibles dans des sections repliables. Les cinq étapes sont des liens rectangulaires espacés qui mènent à leurs sections. Les empreintes et codes internes ne sont plus affichés dans la validation ; une confirmation apparaît après une transmission simulée.

Les statuts et les contrôles sont présentés en français. Dans la revue, une action principale correspond à l’état du dossier : enregistrer les modifications, valider, préparer la transmission simulée, puis confirmer l’envoi simulé. Les reçus restent consultables. Les points bloquants, le caractère fictif de l’estimation et la simulation de l’envoi demeurent visibles.

La progression ne marque pas une analyse échouée ou obsolète comme terminée ; une approbation remplacée ou portant sur un autre contenu n’est pas affichée comme actuelle. Les règles serveur restent applicables et un blocage de validation retourné par l’API désactive aussi le bouton côté interface.

## Fichiers

- `apps/web/src/G1App.tsx` : interface principale et actions authentifiées.
- `apps/web/src/features/insurer-review/insurer-review.css` et `apps/web/src/g1-app.css` : présentation G1.
- `apps/web/src/components/ReviewPanel.tsx` : édition du brouillon, approbation et actions simulées via l’API.
- `apps/api/claim_api/fixture_media/g1/` : deux photos et le clip original, avec provenance et empreinte du clip dans le README du dossier.
- `apps/api/claim_api/g1_media.py` et `evidence_service.py` : manifeste et semis privé idempotent.

## Vérification

`npm run build` compile le parcours web. `uv run --locked pytest` couvre le semis G1, son caractère privé, l’empreinte des fichiers, l’idempotence, les droits d’accès, et les autres contrats API. Un essai de bout en bout contre Supabase nécessite les variables d’environnement et le bucket décrits dans le [guide de déploiement](deployment.md).

## Aperçu de présentation G1 / G2 / G3

Avec Node 24 ou supérieur, depuis `apps/web`, lancez `npm run demo`, puis ouvrez `http://127.0.0.1:5180/`.
Cet aperçu remplace l’ancien montage temporaire hors du dépôt. Il utilise le même front,
avec un adaptateur en mémoire : aucun Supabase, aucun agent et aucune transmission réelle.
Un rechargement remet les trois dossiers à leur état initial. Le bandeau inférieur rappelle ce mode.

- G1 : Peugeot FR-482-KL, médias G1 et devis fictif de 1 240 € pour parcourir la validation.
- G2 : Renault GH-271-RM, médias G2, plaque Opel RK18 LXP / LYP non tranchée.
- G3 : Citroën GT-638-VN assurée ; les photos montrent la Toyota LM21 RZT tierce. Aucune estimation de la Citroën ni proposition de recours n’est préremplie.

Les noms, lieu, récit et repères de présentation sont fictifs et saisis manuellement.
Ils ne constituent pas une sortie des agents ni un nouveau corrigé d’évaluation.
G2 et G3 restent à examiner. Ils sont initialisés uniquement dans cet aperçu ; leur création
persistante dans l’application connectée reste à intégrer côté API.

La réassociation du devis, les modifications de l’estimation, l’invalidation de l’approbation
et les transmissions simulées fonctionnent en mémoire. Les actions qui nécessitent le réseau
(upload, Vision, traitement vidéo, audio) indiquent qu’elles nécessitent l’application connectée.
Les médias sont servis uniquement par le serveur de développement, depuis une liste explicite de fichiers synthétiques.
Cette configuration refuse les builds et n’est pas utilisée par `npm run dev` ou `npm run build`.

L’association réelle passe par `PUT /v1/cases/{id}/quote` : PDF finalisé appartenant au dossier,
montant en centimes et version courante obligatoires. Une modification du devis invalide la validation
précédente. Les tests `test_persisted_report_estimate_and_quote_versions` et
`test_quote_total_mismatch_blocks_approval_even_when_other_gates_pass` couvrent le stockage PostgreSQL,
les versions, les écarts de montant et les restrictions d’accès. Une vérification complète du transfert
Storage et de la connexion Supabase nécessite toujours l’environnement de déploiement.
