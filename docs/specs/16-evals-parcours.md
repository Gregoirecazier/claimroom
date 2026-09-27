# S16 — Évaluations reproductibles et recette du parcours

**État et dépendances.** `apps/api/evals/runner.py` exécute cinq cas d'analyse synthétiques via le port `ClaimsAnalyzer`; `insurance_runner.py` couvre 20 sondes de lookup `mock-insurance-v2` et peut publier une expérience Pydantic Evals dans le projet Logfire EU sur demande. Ces résultats ne couvrent pas encore les 42 lignes des matrices agents/UX. S16 ajoute cette couverture progressivement et termine la recette après S07–S15 ; son harnais et ses fixtures peuvent être développés dès maintenant.

## Contrat de dataset et séparation des rôles

Chaque cas porte `{eval_id, matrix: agents|ux, scenario_id, scene_group_id, fixture_version, app_commit, pipeline_fingerprint, model_version, prompt_version, input_refs, expected_refs, status}`. Les médias sont référencés par ID et SHA-256 vérifié, pas par URL signée persistante. `scene_group_id` regroupe G1 et ses dérivés (floutage, extrait après impact), G2 et G3 séparément ; ne pas compter une variante G1 comme une scène indépendante. `expected_refs` et corrigés sont disponibles au **correcteur seulement**, jamais au port agent, à Pipelex ou au navigateur assuré. Versionner les fixtures et enregistrer leur empreinte dans le rapport.

Un résultat porte `{eval_id, run_id, mode, started_at, duration_ms, actual_summary, source_checks, gate_checks, verdict, reason_codes, trace_ref, cost_units}`. Verdicts autorisés : `passed`, `failed`, `not_tested`, `not_applicable` avec justification obligatoire pour les deux derniers. Ne jamais classer automatiquement une ligne absente `passed`. Le rapport est JSON diffable avec synthèse Markdown courte ; les sous-scores agents et UX restent distincts. Les traces Logfire excluent texte d'intake identifiable, numéro de téléphone, token, URL signée, audio et média binaire ; elles gardent identifiants de fixture non sensibles, versions et raisons codées.

## Trois niveaux d'exécution

1. **Offline par défaut, sans coût fournisseur.** Garder les deux runners existants. Ajouter des adaptateurs mock déterministes qui appellent les mêmes services/ports que l'API pour les règles métier. `uv run --locked python -m evals...` produit un rapport local, sans clé ni réseau externe. Le runner marque comme `not_tested` toute qualité d'agent nécessitant un modèle/vision réellement non exécuté.
2. **Expériences agents opt-in.** Un drapeau explicite active Pipelex/Vision ou publication Pydantic Evals/Logfire ; afficher avant exécution le nombre de nouveaux appels, le mode `reuse_only|allow_new` de S11 et un plafond configurable. Si `reuse_only` manque le cache, retourner un échec de précondition sans appel payant. Ne jamais relancer Vision pour une empreinte et un pipeline identiques. Enregistrer prompts/modèles, résultat brut et jugements humains séparément des gates.
3. **Parcours UX/API.** Fixtures d'agents figées pour isoler les comportements de l'application. Exécuter les transitions sur une base de test jetable ou un namespace démo réinitialisable ; ne jamais envoyer de SMS/claim réel par défaut. Capturer réponse API, état persistant, rôle, version et preuve visuelle pour les cas qui l'exigent. Un bouton masqué ne prouve pas l'autorisation : vérifier la route côté serveur.

## Couverture minimale et assertions

| Groupe | IDs à déclarer | Vérification automatique / humaine |
| --- | --- | --- |
| Voix | V01–V06 | P0, branche urgence, provenance heure et transcript/audio S08 ; conversation réelle qualifiée séparément d'un test mock |
| SMS et dépôt | S01–S05 | fidélité du résumé et P1 (revue), idempotence/statut/lien isolé entre dossiers (API) |
| Vision et investigation | I01–I06, N01–N06 | sources/timecodes, bonne association véhicule-plaque, abstention G2, lookup daté et correspondant ; qualité visuelle avec corrigé humain |
| Responsabilité et dossier | L01–L03, C01–C02 | contradiction G3, causalité non inventée, montant/devis sourcés et synthèse révisable ; formulation revue par humain |
| Parcours et approbation | U01–U07, A01–A07 | pièces privées, coût en centimes, révision, rôle, gate 4, idempotence, reçus, rechargement et URLs renouvelées |

Les cinq cas d'analyse existants et les 20 sondes de lookup restent des sous-suites nommées ; leur succès n'est pas réinterprété comme réussite de la matrice complète. Les assertions bloquantes communes sont : source absente ou d'un autre dossier, couverture positive sur plaque ambiguë/non impliquée, fuite des corrigés, approbation ou envoi sans gate 4, message/claim dupliqué, analyse ancienne qui écrase la version courante. Chaque assertion échouée indique `eval_id`, source et raison, sans imprimer de donnée sensible.

## Recette de bout en bout G1 et contre-exemples

Préparer une commande de seed/reset versionnée, un compte de démonstration, l'URL de départ et l'ordre des actions. Exécuter : appel S08 réel → transcript et audio originaux visibles → S09 aperçu/message → S10 lien et dépôt G1/deux photos → S11 première analyse `allow_new` puis seconde `reuse_only` → S12 observations sourcées → S13 demande CCTV simulée si utile → S14 lookup mock clairement marqué → S15 analyse → S05 amendement → S06 approbation → S07 enregistrement/envoi **simulés** et reçus. Mesurer exactement un appel Vision par empreinte/pipeline neuf et zéro appel sur le rejeu ; enregistrer la source de chaque fait important.

Rejouer avec G2 (plaque ambiguë et voiture bleue non impliquée) et G3 (récit contradictoire) : le blocage et sa raison sont visibles, aucun destinataire ni montant inventé. Injecter une modification après approbation et une réponse réseau tardive ; l'API refuse l'ancienne version et conserve un seul reçu. Les scénarios non disponibles, notamment vidéos V04–V10 non encore produites, restent `not_tested` avec la pièce manquante indiquée.

## Livrables et seuil de sortie

Livrer le dataset versionné, runner(s), tests de schéma/séparation des corrigés, rapport JSON + Markdown d'exemple, commandes offline/opt-in, documentation de seed/reset et tableau des 42 IDs avec verdict explicite. CI exécute seulement le mode offline déterministe ; l'expérience Logfire EU et les appels payants restent opt-in. La recette est bloquée par tout échec d'invariant de sécurité métier ci-dessus ; les scores de qualité agent sont rapportés par domaine et soumis à revue humaine, sans seuil global inventé. Une démonstration réelle de S08 reste une preuve distincte d'une fixture.

**Points d'appui :** `apps/api/evals/`, `docs/demo/evaluation-matrix.md`, `docs/demo/evaluation-agents.md`, `docs/demo/evaluation-ux.md`, `docs/demo/corriges-videos-g1-g2-g3.md`, `.github/workflows/ci.yml`.
