# S16 : harnais offline, première tranche

Depuis `apps/api`, lancer `uv run --locked python -m evals.matrix_runner --output-dir /tmp/s16-report`. Le rapport JSON et sa synthèse Markdown portent le même identifiant de run et un horodatage UTC. Le contenu est ordonné et diffable. Le code de sortie vaut 1 si l'une des deux sous-suites déterministes échoue, 2 si le dataset ou une empreinte média est invalide.

Un [exemple JSON](../../apps/api/evals/examples/offline-report-v1.json) et sa [synthèse Markdown](../../apps/api/evals/examples/offline-report-v1.md) figent un run au 25 septembre 2026 sur le commit de départ de cette tranche ; les runs ultérieurs portent leur propre commit et horodatage.

## Données et frontière du correcteur

- `matrix_cases_v1.json` déclare exactement les 26 IDs agents et 16 IDs UX, leurs scénarios, groupes de scène et références de médias. G1 et ses variantes futures partagent `g1`; G2 et G3 ont chacun leur propre groupe. L'extrait G1 après impact, nécessaire à L02, n'existe pas encore et est marqué manquant.
- `media_v1.json` versionne les chemins relatifs et SHA-256 des neuf médias G1–G3 présents. Le runner lit chaque fichier et refuse une empreinte différente. Les photos G2/G3 sont des reconstructions synthétiques ; leur présence n'en fait pas une preuve indépendante de la vidéo.
- `reference/answers_v1.json` contient les attendus repris des matrices. `task_inputs()` n'en expose ni le texte ni `expected_refs`. Un futur adaptateur agent ne doit recevoir que ce payload autorisé. Le correcteur pourra lire les références après exécution, jamais dans le contexte de l'agent évalué.
- Le rapport conserve seulement les IDs, versions/empreintes, scores codés et raisons. Aucun média binaire, URL signée, numéro, token ni texte d'appel réel n'y figure. Les appels réels S08 et les traces Logfire ne sont pas importés par cette commande.

## Lire le bilan

Les cinq cas `analysis_golden` appellent le port `ClaimsAnalyzer` avec `FixtureClaimsAnalyzer`. Les 20 cas `mock_insurance_probes` appellent l'adaptateur lookup métier. Ils testent des invariants locaux utiles, sans devenir des verdicts V/S/I/N/L/C/U/A. Par défaut, les 42 lignes portent `not_tested` et une raison explicite ; la commande ne facture aucun appel. `passed` n'est accordé à aucune ligne sur la seule base d'un mock. Le résumé sépare agents et UX.

## Seed, reset et parcours cible

Cette tranche utilise des fixtures de fichiers immuables ; son seed est le checkout au commit du rapport et `uv sync --locked --dev`. Son reset consiste à relancer la commande avec un nouveau répertoire de sortie. Elle ne modifie aucune base, ne crée aucun dossier utilisateur et n'envoie ni SMS ni demande de recours.

Le parcours jury S08 → S09 → S10 → S11–S15 → approbation/envoi simulés sera évalué dans une tranche ultérieure avec un namespace de test réinitialisable, un compte et une URL de départ explicitement documentés. Il devra faire une première analyse G1 en `allow_new`, la rejouer en `reuse_only`, puis montrer zéro appel Vision supplémentaire pour la même empreinte/pipeline. G2 devra bloquer la plaque Opel ambiguë et ne pas utiliser la plaque de la voiture bleue non impliquée ; G3 devra faire apparaître la contradiction. Les contrôles serveur de gate 4, d'idempotence, de version périmée et de droits d'accès restent `not_tested` dans ce rapport jusqu'à l'exécution des routes concernées sur une base jetable. Les jugements de formulation/causalité demanderont une revue humaine des annotations.
