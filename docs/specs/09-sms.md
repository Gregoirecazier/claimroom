# S09 — Suivi SMS du dossier

**État et dépendance.** La table privée `case_messages` existe ; aucun service, route, écran ou envoi SMS n'existe. Un appel S08 réel a produit des échanges et une transcription, et son enregistrement original est désormais stocké. S09 peut commencer en `mode=mock` ; la livraison Twilio est une tranche distincte. Avant de figer un message, intégrer le service de droit de dépôt minimal décrit dans S10. Le portail assuré complet peut suivre S09.

## Entrées et génération

- Lire côté serveur le dossier courant, sa `content_revision`, le résumé de l'appel et les déclarations finales sourcées. Refuser ou garder en brouillon une session `urgent_human_handoff`, incomplète ou sans numéro de rappel exploitable ; ne jamais envoyer un SMS de collecte dans la branche urgence.
- Lire le numéro candidat de `call.customer.number` via l'API Vapi privée pour le `provider_call_id` du dossier, ou depuis le webhook S08 si cette donnée est persistée ensuite. Vérifier le format E.164 et l'appartenance du `call_id` à la session du dossier. L'identifiant d'appelant est une **coordonnée candidate**, pas une preuve d'identité ni de propriété du numéro. Un gestionnaire confirme le numéro avant création ; il peut saisir une correction explicite, auditée, si le numéro est absent ou erroné. La route de création revalide le destinataire approuvé et refuse un changement silencieux du navigateur.
- Produire un aperçu déterministe à partir des seuls faits déclarés, en distinguant heure déclarée et `inferred_from_call`. Lister uniquement les P1 encore manquants. Ajouter au plus trois consignes médias adaptées aux pièces déjà présentes : vue large, détail des dommages et tiers/plaque **si disponible**. La vidéo de démonstration est mentionnée uniquement dans le parcours synthétique. Ne pas introduire de plaque, couleur, assureur, cause ou blessure absents de l'intake.
- Le lien provient du service de droit S10 : un jeton opaque lié au `case_id`, avec expiration. L'aperçu émet un droit court, encore inutilisé, pour montrer **exactement** le corps proposé. La création vérifie le droit, le dossier, la révision et l'expiration, puis l'associe atomiquement au message ; les aperçus abandonnés expirent. Deux appels de création concurrents avec la même clé ne peuvent l'associer qu'à un message. Aucun UUID seul ne donne accès. Le corps historique, qui contient le lien, reste dans la table privée, hors logs/traces/exports non autorisés ; l'interface masque le jeton dans les listes. S10 ne stocke que l'empreinte du jeton dans la table des droits.

## API et persistance

1. `POST /v1/cases/{case_id}/messages/preview` prend `{expected_state_version, recipient_confirmation}` où la confirmation porte le candidat E.164 ou une correction explicite. La réponse donne `recipient_masked`, `body`, `missing_items`, `source_refs`, `deposit_grant_id`, `deposit_link_expires_at`, `content_revision`, `preview_hash`, `warnings`. Elle peut créer un droit S10 expirant, mais aucune ligne `case_messages` ni requête Twilio. L'aperçu expiré ou produit sur une ancienne révision doit être recalculé.
2. `POST /v1/cases/{case_id}/messages` prend `{expected_state_version, idempotency_key, deposit_grant_id, preview_hash}` ; le serveur reconstruit et valide le contenu courant avant de figer `recipient`, `body`, `mode`, `provider`, `payload_hash`, `status`, le lien associé et l'auteur. Il ne doit pas accepter un corps arbitraire du navigateur comme vérité du dossier. `payload_hash` couvre destinataire, corps, lien/version, mode et révision ; le même couple dossier/clé et le même hash renvoie le même message. Même clé et hash différent : `409 idempotency_conflict`.
3. `GET /v1/cases/{case_id}/messages` retourne l'historique et les statuts sans révéler un droit de dépôt réutilisable à un acteur non autorisé. Le propriétaire/gestionnaire authentifié peut ouvrir un lien de secours courant via une route dédiée à S10.
4. `case_messages` est append-only pour le contenu ; les transitions de livraison ne modifient que statut, identifiant fournisseur, timestamps, erreur et audit. Elles incrémentent `state_version`, jamais `content_revision`, et ne révoquent pas une approbation de claim. Prévoir une migration additive pour `deposit_grant_id`/`preview_revision` si nécessaire ; ne pas y stocker le jeton brut.
5. Le mock accepte `queued → accepted → sent → delivered` ou `failed` selon fixture, avec identifiant de simulation. `accepted` signifie prise en charge, pas livraison. `unknown` bloque un nouvel essai aveugle ; réconcilier d'abord. Les callbacks futurs doivent être authentifiés, idempotents et monotones face aux événements hors ordre.

## Écran et reprise

Afficher l'aperçu avant validation, le numéro masqué, la révision, les demandes P1 et le statut exact après création. En cas d'échec, conserver le message historique, afficher `error_code` et donner un accès de secours au dépôt depuis l'espace authentifié. Régénérer un droit expiré crée un **nouveau** message ou une nouvelle version de lien ; ne réécrire ni le SMS initial ni son statut. Un double clic ne crée pas deux lignes.

## Recette et tests ciblés

- S01–S03 de la matrice agents : résumé fidèle, P1 utiles, inconnues conservées. Utiliser des assertions de faits/sources et une revue de formulation, pas une égalité mot à mot.
- S04 de la matrice UX : double clic, retry avec même clé, conflit de clé, échec et `unknown`, historique après rechargement. Tester callbacks désordonnés quand l'adaptateur live sera ajouté.
- Deux dossiers et deux appels : numéros, messages et liens distincts ; un acteur d'un autre dossier reçoit `403`/`404`. Changer une déclaration entre aperçu et création donne `409 stale_case`, sans message envoyé.
- Aucune requête Twilio en exécution par défaut ; marquer chaque résultat `mode=mock`. Documenter une commande locale de démonstration, la migration et les réponses API dans `docs/contracts.md`.

**Points d'appui :** `apps/api/claim_api/migrations/sql/case_messages.sql`, `apps/api/claim_api/routes/voice.py`, `docs/jury-path.md`, `docs/demo/evaluation-agents.md`, `docs/demo/evaluation-ux.md`.
