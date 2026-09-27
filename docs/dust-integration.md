# Agents Dust : contrat et appels backend

Le backend dispose d'un client Dust et de routes authentifiées pour les cinq
spécialistes. Les appels sont asynchrones côté Dust et persistés dans Supabase.
Le dépôt d'une photo ou vidéo déclenche désormais Gemini, puis l'agent garage.
Son brouillon est éditable dans la section Analyse. Les autres agents restent
accessibles par les routes ci-dessous. Voir [le parcours média](media-workflow.md).

## Configuration

Les secrets restent dans `apps/api/.env` en local et dans les variables du projet
API Vercel en déploiement. Le démarrage local doit charger ce fichier, par exemple
`uv run --env-file .env uvicorn app:app --reload --port 8000` depuis `apps/api`.
Une clé dans le fichier n'est pas automatiquement chargée par le code Python.

```dotenv
DUST_API_KEY=<secret serveur existant>
DUST_API_BASE_URL=https://dust.tt
DUST_WORKSPACE_ID=0G0kj9rhj8
DUST_AGENT_LEGAL=sduIybtm6M
DUST_AGENT_CCTV=qBEkK3Nkg3
DUST_AGENT_REPAIR=HwU56jOUcl
DUST_AGENT_GARAGE=39cz24sZfG
DUST_AGENT_RECOVERY=QC7kvgzlNa
ANALYSIS_PROVIDER=gemini
GEMINI_API_KEY=<secret serveur existant>
```

Les IDs ci-dessus et le domaine US sont les valeurs par défaut du client ; les
variables permettent de les remplacer. Un espace EU utilise `https://eu.dust.tt`.
Le client n'accepte que ces deux domaines et refuse les redirections HTTP pour ne
pas transférer la clé. Aucune nouvelle dépendance Python n'est nécessaire.

Appliquer la migration additive avant les appels :

```sh
cd apps/api
uv run --env-file .env alembic upgrade head
```

La révision `20260926_dust_runs` ajoute la table privée `dust_runs`, avec RLS et
sans droits navigateur. La migration n'est pas appliquée automatiquement au
démarrage. Les dossiers, décisions, validations humaines et analyses Gemini
existants ne sont pas modifiés par les résultats Dust.

### Vérification réelle du 26 septembre 2026

La clé présente dans `apps/api/.env` a été testée en lecture sur les cinq
configurations d'agents. Dust renvoie pour chacune HTTP 403,
`workspace_auth_error`, « Only admins can access this agent. »
La clé n'a pas été affichée. Les permissions des agents et de la clé n'ont pas
été modifiées. Il faut vérifier leur visibilité et les droits de la clé dans
Dust avant de valider une exécution réelle. Une unique invocation technique
sans données de dossier a ensuite été tentée via le client : le POST de création
a renvoyé HTTP 429 (`dust_rate_limited`). Aucun résultat d'agent réel n'a donc pu
être validé. Vérifier aussi les limites/quota et la disponibilité côté Dust avant
de retenter ; ce statut seul ne permet pas de conclure à sa cause exacte.

## Séquence Gemini → Dust

1. Déposer les médias par les routes existantes.
2. `POST /v1/cases/{case_id}/analysis-runs` avec `expected_state_version`.
   Avec `ANALYSIS_PROVIDER=gemini`, l'adaptateur existant envoie les fichiers
   directement à l'API Gemini. Attendre un résultat `ready` et lire la nouvelle
   `state_version` du dossier renvoyé.
3. `POST /v1/cases/{case_id}/dust-runs` avec le rôle `repair` et cette version.
   Le backend exige la dernière analyse Gemini réussie, comportant des résultats
   média pour la même `content_revision`. Sinon il répond 409
   `dust_current_gemini_analysis_required` avant tout appel Dust.
4. Dust reçoit `gemini_media_analysis` : observations, dommages par véhicule,
   incertitudes, hypothèses et citations UUID/secondes. Il ne reçoit ni vidéo,
   ni URL signée, ni chemin Storage, ni clé Gemini. La responsabilité et les
   coûts proposés restent à revoir, sans montant approuvé automatique.

L'API Gemini conserve son modèle configurable `GEMINI_MODEL`. Le client Dust
ne sélectionne pas de modèle : chaque agent utilise celui configuré dans Dust.
La création d'un run Dust ne relance pas Gemini et ne recharge pas les médias.

## Lancement d'un agent

Toutes ces routes demandent le Bearer **Supabase de l'utilisateur**, jamais la
clé Dust côté navigateur. Le dossier doit appartenir à cet utilisateur et être
synthétique. Un signalement de danger/blessure bloque le parcours au profit de
la revue humaine.

```http
POST /v1/cases/{case_id}/dust-runs
Content-Type: application/json
Authorization: Bearer <jeton Supabase>

{
  "agent": "repair",
  "expected_state_version": 4,
  "idempotency_key": "00000000-0000-4000-8000-000000000001",
  "documents": [
    {
      "id": "devis-demo-1",
      "title": "Devis fictif du garage A",
      "text": "Pare-chocs : 400 EUR HT ; 3 h à 80 EUR HT ; TVA 20 %."
    }
  ]
}
```

Les documents sont des extraits textuels facultatifs, pas des pièces authentifiées
automatiquement. Ils sont conservés avec l'entrée du run. Pour un PDF, fournir
son texte extrait ; aucune nouvelle extraction PDF n'est ajoutée ici.

| Rôle | Entrée principale | Résultat demandé |
| --- | --- | --- |
| `legal` | Récit, contrats fournis, lookup assureur/correspondant, analyse Gemini à jour si disponible | Couverture, responsabilité proposée, route de recours, lacunes |
| `cctv` | Lieu et heure obligatoires, récit, Camérci | Caméras documentées, responsable, brouillon de demande ou blocage |
| `repair` | Analyse Gemini à jour obligatoire, devis et données tarifaires fournis | Écarts, coûts indicatifs, hypothèses et devis manquants |
| `garage` | Préférences dans les documents, récit, résultats `legal`/`repair` à jour si présents | Dossier minimal et demande de devis/rendez-vous en brouillon |
| `recovery` | Contrats, expertise, factures et preuves de paiement fournies, lookups, rapports `legal`/`repair` à jour si présents | Recours préparatoire, justificatifs et destinataire à valider |

Les rapports précédents sont explicitement marqués **non validés par un humain**.
Seul le rapport le plus récent réussi de chaque rôle dépendant est fourni.
L'absence de preuve de paiement ne devient jamais une indemnisation présumée.

La mission CCTV impose comme point de départ
[Camérci](https://camerci.fr/#6/46.647/2.575). Cette URL est une vue générale de
la carte, pas la position d'un accident. Si l'outil web Dust ne peut pas lire la
carte interactive, l'agent doit signaler cette limite. Mentionner Camérci dans un
prompt ne constitue pas un connecteur de téléchargement vidéo. L'agent Dust
n'envoie pas lui-même de demande. Le panneau CCTV distinct permet de préparer
et d'envoyer un email via Resend à un responsable identifié ; aucune API
Camérci de récupération vidéo n'est inventée.

## Suivi durable, sans worker en mémoire

Le lancement retourne HTTP 202 avec un `DustRunView`, notamment `id`, `status`,
`agent`, `conversation_id`, `input_content_revision` et `gemini_analysis_run_id`.
Un refus distant immédiat peut être enregistré comme `failed` dans cette réponse :
le frontend doit vérifier `status`, pas seulement le code HTTP.

```text
GET  /v1/cases/{case_id}/dust-runs
POST /v1/cases/{case_id}/dust-runs/{run_id}/refresh
```

Le GET lit l'historique local (100 derniers runs), sans appeler Dust. Le POST
`refresh` lit la conversation Dust puis stocke le résultat ; le frontend peut
l'appeler toutes les 3–5 secondes, puis s'arrêter sur un statut terminal.
Les rafraîchissements arrivant moins de 3 secondes après la dernière mise à jour
retournent l'état local. Il n'y a ni boucle de fond dans Vercel, ni attente longue
dans la requête de lancement. Sans rafraîchissement, le résultat reste chez Dust
jusqu'au prochain rafraîchissement.

Statuts : `submitting`, `running`, `needs_action`, `ready`, `failed`,
`submission_unknown`, `stale`. `needs_action` exige une intervention dans Dust ;
le backend ne valide aucun outil à la place du gestionnaire. `ready` signifie
rapport reçu et structure validée, jamais décision d'assurance approuvée.

Le backend valide le JSON et les identifiants dossier/révision/rôle. Une citation
média doit correspondre exactement à une citation Gemini d'entrée. Une référence
interne doit exister dans l'entrée du run. Les URL publiques proposées restent
des sources à vérifier par le gestionnaire : le contrôle structurel ne prouve
pas leur contenu ni la validité juridique de la conclusion.

Une édition du dossier rend les anciens résultats `stale`. Une exécution Dust
ne change pas la `content_revision` du dossier ni sa dernière analyse Gemini.
Les estimations Dust ne sont pas inscrites dans les montants approuvés.

### Doublons et erreurs

Réutiliser la même `idempotency_key` et le même corps après une interruption
réseau côté navigateur. Un autre corps avec cette clé renvoie 409. Un seul run
actif par dossier/rôle est autorisé. Chaque tentative est réservée en base avant
la création distante ; le POST Dust n'est jamais retenté automatiquement.

Si Dust a pu créer une conversation mais que sa réponse est perdue, le statut
devient `submission_unknown`. Si le serveur est interrompu pendant la soumission,
un rafraîchissement après 90 secondes le signale de la même façon. Chercher dans
Dust le titre contenant l'UUID du run avant toute nouvelle tentative. Aucune
réconciliation automatique ni promesse d'exécution exactement une fois n'est
faite. Un GET Dust temporairement indisponible peut être retenté par refresh.

## Validation

Tests sans appels fournisseurs : transport, permissions applicatives, sélection
d'agent, refus des redirections, citations Gemini, versions, idempotence,
soumission incertaine, routes authentifiées, rendu SQL de migration. Les tests
PostgreSQL existants nécessitent `TEST_MIGRATION_DATABASE_URL` sur une base locale
jetable ; aucun schéma distant n'est modifié par les tests unitaires.

Documentation officielle consultée :
[création de conversation](https://docs.dust.tt/api-reference/conversations/create-a-new-conversation),
[lecture de conversation](https://docs.dust.tt/api-reference/conversations/get-a-conversation),
[types et statuts des messages](https://github.com/dust-tt/dust/blob/main/front/types/assistant/conversation.ts).
