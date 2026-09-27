# S15 — Analyse sourcée, gates et brouillon de recours

**État et dépendances.** `analysis.py` exécute Pipelex avec `AnalysisInputV1`/`AnalysisOutputV1`, persiste les runs et applique trois gates. S12 fournit des observations vidéo sourcées en mock opt-in ; S14 branche le lookup synthétique et son correspondant. S03/S04 fournissent le compte rendu éditable et le coût/devis versionnés. Démarrer l'implémentation après intégration de S14 et des migrations S05–S07 ; la rédaction du contrat et les fixtures peuvent avancer avant. La responsabilité reste une proposition soumise au gestionnaire.

## 1. Construire un instantané immuable

`POST /v1/cases/{case_id}/analysis-runs` garde `{expected_state_version}`. Au début du run, lire en une transaction le dossier, `content_revision`, toutes les sources autorisées du même `case_id` et leurs versions. Persister l'entrée exacte et la version de méthode avant l'appel modèle. Le constructeur doit inclure :

- Déclarations et corrections du dossier, chacune avec champ, texte, provenance, date et référence ; distinguer texte de transcription, correction assuré et édition gestionnaire.
- Pièces finalisées uniquement, avec checksum/statut, provenance et source. Pour chaque observation S12 : `evidence_id`, timecodes/image, catégorie, texte, niveau d'incertitude, `pipeline_fingerprint` courant et `source_ref` résoluble. Une observation d'un autre média, d'un autre dossier ou d'un ancien pipeline ne peut soutenir une affirmation courante.
- Résultat S14 **courant** : plaque candidate, association au véhicule impliqué et sources de corroboration ; `status`, couverture à la date du sinistre, assureur et correspondant sourcés. Les résultats anciens restent en audit, pas dans le choix actif.
- Estimation, lignes de coût, devis PDF courant et association au brouillon. Un montant `demo_fixture` ou `handler_entered` conserve cette provenance ; le modèle ne convertit pas une image en prix. Les extraits juridiques/procéduraux ne sont fournis que s'ils existent dans un corpus vérifié et portent un identifiant stable. Aucun corpus disponible = `legal_basis_unverified`.

Introduire `AnalysisInputV2`/`AnalysisOutputV2` si V1 ne peut représenter ces liens ; ne jamais réinterpréter silencieusement le JSON V1. Le schéma versionné est validé par Pydantic et documenté. Exclure les corrigés G1–G3, les attendus d'évaluation, les URLs signées et les données d'un autre dossier.

## 2. Sortie proposée, contrôlée par l'API

Le port `ClaimsAnalyzer` retourne une route proposée `subrogation|handler_review|insufficient_information`, des propositions `supported|hypothesis|contradicted`, chacune avec `source_refs`, incertitude et texte, les contradictions, lacunes critiques/non bloquantes, montant candidat avec ligne/devis source et destinataire candidat avec résultat S14. Conserver la sortie brute, la version prompt/modèle/méthode, le mode fournisseur et l'erreur normalisée. L'API vérifie chaque référence contre l'instantané ; une référence inexistante, un média non finalisé ou une source d'un autre dossier invalide la sortie.

Le texte de responsabilité décrit le mécanisme observable et la version du déclarant séparément, puis formule une hypothèse de recours. Il ne conclut ni identité du conducteur, ni faute établie, ni base légale inventée à partir d'une vidéo. Un document juridique absent reste « à vérifier ». Une plaque lisible sur un véhicule non impliqué ne devient pas la plaque du tiers.

## 3. Gates déterministes

Le code de l'application, jamais le prompt, calcule et persiste `{gate, status, reason_codes, source_refs}`. Les codes sont stables et affichés par le front.

| Gate | Passe si | Échecs à distinguer |
| --- | --- | --- |
| G1 — intake | P0 complets, accident daté/localisé, gravité évaluée et pas d'urgence non prise en charge | `missing_p0`, `urgent_handoff`, `incident_time_unknown` selon le champ manquant réel |
| G2 — tiers et destination | Plaque complète reliée par preuve au véhicule **impliqué** ou association humaine explicite et auditée ; lookup courant `matched`, couverture valide à la date, correspondant cohérent et sourcé | `plate_ambiguous`, `plate_wrong_vehicle`, `plate_uncorroborated`, `coverage_unavailable`, `coverage_outside_date`, `correspondent_missing`, `lookup_stale` |
| G3 — synthèse et montant | Propositions matérielles sourcées ou marquées hypothèses, contradictions exposées, coût justifié si montant proposé, devis cohérent si dossier final chiffré | `invalid_source_ref`, `unsupported_fact`, `contradiction_unaddressed`, `amount_without_source`, `quote_missing`, `quote_mismatch`, `legal_basis_unverified` selon politique du package |

Un `matched` mock est affiché comme mock et ne simule pas une consultation réelle MIB/BCF. G2 n'utilise pas une plaque simplement citée dans le récit sans association vérifiée. G3 n'efface pas une contradiction pour produire un brouillon. La gate 4 (rôle, revue et approbation de la version exacte) appartient à S06 ; S15 ne l'auto-approuve pas. Définir explicitement dans le code les codes bloquants pour `review_ready` et les avertissements qui autorisent seulement une revue, puis documenter cette table dans `docs/contracts.md`.

## 4. Concurrence, persistance et interface

- `begin_analysis` réserve un run pour une seule révision. Si la `content_revision` change pendant Pipelex, terminer le run en `stale`, conserver ses entrées/sorties pour audit et ne toucher ni brouillon ni gates courants. Un échec fournisseur termine en `failed` avec code utile ; le dossier reste éditable.
- La finalisation du run et l'éventuel brouillon/version de package doivent être atomiques. Même requête rejouée après timeout : retourner le même run ou `analysis_in_progress`, sans deuxième brouillon. Un ancien run ne peut écraser un nouveau.
- L'écran montre pour chaque proposition sa source ouvrable, son statut et son incertitude ; les raisons de gate sont lisibles, avec action de correction. Montant et destinataire sont proposés seulement s'ils ont leurs sources courantes. Le gestionnaire peut amender via S05 ; tout changement matériel invalide la revue/approbation S06.

## Recette ciblée

1. G1 complet avec observation BMW corroborée, lookup S14 et devis S04 cohérent : run terminé, références résolubles, brouillon `review_ready` pour revue humaine. Vérifier la version exacte des pièces, du devis et du destinataire.
2. G2 : plaque Opel `RK18 L?P` ambiguë, `XY34 ZTR` sur la voiture bleue non impliquée : G2 bloque, aucun assureur/destinataire positif, même si un lookup exact d'un candidat répond `matched`.
3. G3 : récit « j'étais arrêté » contraire à la vidéo : deux sources citées et contradiction visible ; ne pas supprimer le récit ni conclure à une fraude. Extrait vidéo après le choc seulement : aucune causalité catégorique.
4. Couverture absente/hors date, correspondant absent, coût sans source, devis incohérent, source étrangère, pipeline Vision ancien : raison codée et pas de brouillon approuvable. Modifier le dossier pendant inférence : run `stale`, brouillon courant inchangé. Panne Pipelex : `failed` et reprise possible.
5. Tests unitaires du constructeur/schémas/gates, intégration PostgreSQL de concurrence et révisions, puis parcours UI G1/G2/G3. La qualité juridique/formulation est évaluée séparément par S16 ; un test déterministe de gate ne prouve pas cette qualité.

**Points d'appui :** `apps/api/claim_api/analysis.py`, `apps/api/claim_api/routes/analysis.py`, `apps/api/claim_api/models.py`, `docs/contracts.md`, `docs/demo/evaluation-agents.md`.
