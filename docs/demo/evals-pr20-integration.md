# Intégration de la PR 20 aux évaluations et à Logfire

État exécutable au 25 septembre 2026. La [matrice de 42 critères](evaluation-matrix.md) est un plan de tests, pas un résultat d'évaluation. Les [corrigés G1–G3](corriges-videos-g1-g2-g3.md) et les manifestes de provenance G2/G3 sont des références de correction réservées à l'évaluateur.

| Suite | Source | Ce qui est réellement vérifié | Logfire |
| --- | --- | --- | --- |
| `claims-analysis-golden-v1` | `apps/api/evals/golden_cases_v1.json` | 5 cas synthétiques sur le port `ClaimsAnalyzer` : schéma, sources, incertitude, route, gates et brouillon. Analyseur simulé par défaut. | Upload explicite via le runner existant. |
| `claims-insurance-probes-v2` | `apps/api/fixtures/mock-insurance/eval_cases.json` de la PR 20 | 20 appels exacts à `insurance_lookup`, dont 6 références G1–G3, plaque G2 partielle et alternative absente ; correspondant fictif quand une plaque UK a un match. | Nouveau runner Pydantic Evals, upload explicite. |
| Vision, responsabilité, voix, SMS, dossier, UX, approbation | `evaluation-matrix.md` et corrigés | Critères décrits, **aucun score automatisé** par ces deux runners. | Aucun verdict envoyé tant qu'une tâche réelle n'est pas branchée. |

Le runner assurance exporte l'ID de probe synthétique, les assertions nommées, le statut, la raison, la validité de couverture et des empreintes de noms fictifs. Il ajoute `fixture_version=mock-insurance-v2`, SHA-256 du fichier de probes, version de l'évaluateur, `run_id` et capacité mesurée aux métadonnées de l'expérience. Aucun média, plaque, référence de police, nom d'assureur, récit ou corrigé n'est un attribut de trace. L'upload requiert un jeton d'écriture du projet Logfire EU ; un lancement local reste entièrement hors ligne.

Le match exact `RK18 LXP` et l'absence de `RK18 LYP` ne permettent pas de choisir la lecture visuelle de la vidéo G2. `RK18 L?P` doit retourner `ambiguous`. La couverture de la Toyota G3 ne prouve ni son mouvement ni sa responsabilité. Un `record_id` de fixture n'est pas un UUID de source persistée dans le dossier.

## Branchement des évaluateurs Vision et parcours

Pour chaque ligne automatisable de la matrice, créer un cas versionné avec `eval_id`, `scene_group` (`G1`, `G2` ou `G3`), IDs et SHA-256 des médias réellement donnés à l'agent, versions du prompt, du modèle et du pipeline, entrée autorisée, attendu privé et critères de verdict. Les variantes floutées ou découpées de G1 gardent `scene_group=G1` pour éviter de compter la même scène plusieurs fois dans un découpage entraînement/test. Vérifier les octets contre les manifestes G2/G3 au chargement. Les quatre photos générées sont des reconstructions et ne doivent pas être comptées comme quatre observations indépendantes de la vidéo.

La tâche d'évaluation ne reçoit que les médias et le récit autorisés. Le correcteur compare ensuite une sortie structurée aux corrigés privés, en exigeant des sources réelles par affirmation et l'abstention quand l'image ne suffit pas. Stocker pour chaque exécution le verdict, la raison et la référence de trace ; n'envoyer à Logfire que des métadonnées et scores compacts. Les médias privés restent dans le stockage autorisé et ne transitent pas par les attributs de spans.

Le port d'analyse actuel reçoit un `AnalysisInputV1` construit à partir d'observations et de résultats fournisseur ; il ne lit pas les MP4. Brancher une extraction Vision réelle et persister ses observations sourcées avant de scorer I01–I06 ou L01–L03. Puis adapter les données `mock-insurance-v2` au contrat `provider_results` du dossier (`source_id` UUID, `source_version`, `mode=mock`, schéma de correspondant attendu par les gates) avant une eval de bout en bout. Tant que ces chemins ne sont pas exécutés, les 20 probes restent une preuve du seul adaptateur de lookup.

Commandes depuis `apps/api` :

```sh
uv run --locked python -m evals.runner
uv run --locked python -m evals.insurance_runner
uv run --locked pytest tests/test_evals.py tests/test_insurance_evals.py tests/test_mock_insurance.py
```
