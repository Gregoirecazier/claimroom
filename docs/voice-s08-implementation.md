# S08 — câblage de l'agent vocal

Le contrat métier est dans [08-appel-et-intake.md](specs/08-appel-et-intake.md). Les routes et la passerelle sont codées ; un appel réel demande le déploiement et les identifiants des trois fournisseurs.

## Parcours déployé

1. Le numéro entrant Twilio est importé dans Vapi et lié à un assistant **enregistré**. Vapi pilote l'appel et les outils.
2. Son `custom-transcriber` ouvre `wss://<hôte-api>/v1/voice/gradium/transcriber`. La passerelle envoie le canal appelant PCM16 à Gradium STT, puis renvoie à Vapi les transcriptions partielles et finales. Son `custom-voice` appelle `https://<hôte-api>/v1/voice/gradium/tts` ; la passerelle produit du PCM16 Gradium TTS.
3. Le Server URL de l'assistant pointe directement vers `https://<hôte-api>/v1/voice/vapi/server-events`. Le webhook accepte les `transcript` **finaux** des deux locuteurs et `end-of-call-report` ; les partiels ne sont pas persistés. Le function tool `next_intake_step` pointe vers `https://<hôte-api>/v1/voice/vapi/next-intake-step`, avec une réponse `results[]` par `toolCallId`. L'identifiant d'appel provient de `message.call.id`, jamais des arguments générés par le modèle.
4. Le rapport final lance la récupération de `GET https://api.vapi.ai/call/{id}/stereo-recording` avec la clé privée Vapi. Le 302 est suivi uniquement vers un hôte HTTPS autorisé ; le fichier WAV ou MP3 original est copié sans conversion dans le bucket Supabase privé. SHA-256, taille, format et état (`pending`, `available`, `error`) sont liés à la session. `call.artifact.upload` et les relectures du rapport peuvent relancer la récupération ; le gestionnaire peut aussi appeler `POST /v1/voice/{case_id}/recording/retry`.
5. La fiche dossier contient les segments finaux ordonnés, avec locuteur et temps lorsqu'ils sont fournis par Vapi. `GET /v1/voice/{case_id}/recording/read-url` vérifie le propriétaire du dossier et délivre une URL signée de 300 secondes ; l'interface la demande seulement à la lecture. Une absence ou un échec d'enregistrement est visible.

`POST /v1/voice/mock/intake-sessions` importe un appel simulé. `POST /v1/voice/vapi/normalized-events` reste disponible pour un événement déjà normalisé. La base garde une session par identifiant Vapi et des clés d'événement idempotentes. Seuls le récit, le lieu, le prénom et le nom sont à recueillir pendant l'appel. La clôture remercie l'appelant et annonce brièvement le SMS de complément. L'assistant enregistré pose uniquement la `next_question` renvoyée par le tool, sans ajouter de question sur le tiers, le danger ou les blessures. Les faits P0 restent sourcés dans une transcription finale de l'appelant ; une heure vague reste inconnue. L'urgence bloque l'analyse automatique.

Un gestionnaire peut lancer `POST /v1/voice/{case_id}/reextract` depuis « Réexaminer la transcription » pour un appel terminé incomplet. La reprise utilise uniquement les segments appelant déjà conservés, ajoute une révision auditée si elle trouve de nouveaux faits et refuse d'écraser une déclaration corrigée manuellement. Un lieu ambigu comme « places de l'Étoile » est affiché comme extrait incertain et reste à confirmer ; les réponses sur la sécurité, les blessures et le départ du tiers ne sont pas déduites du seul récit du choc.

## Configuration sans secrets dans le dépôt

La dépendance Gradium est une dépendance de base de l'API, car Vercel installe le groupe principal de `pyproject.toml` pour la fonction Python ; un extra optionnel n'y est pas installé. `requirements.txt` est exporté sans groupe optionnel (`uv export --locked --no-dev --no-emit-project --no-hashes --format requirements.txt --output-file requirements.txt`). Définir les variables côté serveur seulement :

| Variable | Valeur attendue |
| --- | --- |
| `VOICE_LIVE_ENABLED` | `true` une fois le déploiement validé |
| `VOICE_WEBHOOK_SECRET` | Secret serveur dédié aux webhooks et outils Vapi, transmis en `X-Vapi-Secret` ou bearer |
| `VOICE_OWNER_USER_ID` | UUID du gestionnaire propriétaire des nouveaux dossiers |
| `VOICE_ASSISTANT_ID`, `VOICE_ASSISTANT_VERSION` | ID et version de l'assistant Vapi enregistré |
| `VOICE_EXTRACTOR_VERSION` | Version des règles d'extraction, par exemple `voice-rules-v3` |
| `GRADIUM_BRIDGE_ENABLED` | `true` pour STT et TTS |
| `VAPI_AUDIO_SECRET` | Secret distinct pour les routes audio, transmis en `X-Vapi-Secret` |
| `GRADIUM_API_KEY`, `GRADIUM_VOICE_ID` | Créer une clé Gradium dédiée (aucune clé active constatée), choisir une voix française du catalogue et poser les deux valeurs dans les secrets de déploiement |
| `VAPI_PRIVATE_API_KEY` | Clé privée serveur pour le téléchargement stéréo Vapi |
| `VAPI_ORG_ID`, `VOICE_WEB_TEST_ENABLED` | S17 : organisation Vapi et activation explicite du test navigateur. L'API émet un JWT public de cinq minutes limité à l'origine web et à l'assistant, seulement pour le gestionnaire démo. |
| `VAPI_RECORDING_ALLOWED_HOSTS` | Liste d'hôtes HTTPS du 302 d'enregistrement, séparés par des virgules, constatés et validés sur un appel test |
| `DATABASE_URL` | PostgreSQL du projet, migration Alembic à jour |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Accès serveur à Supabase Storage |
| `VOICE_RECORDING_BUCKET` | Bucket **privé dédié** (défaut `claim-voice-recordings`) ; distinct du bucket pièces `claim-evidence` |

Le bucket `claim-voice-recordings` a été créé dans le projet Supabase de démo : privé, limite **50 MB**, types `audio/wav` et `audio/mpeg`. Le code refuse un fichier supérieur à 50 000 000 octets. Vérifier par un upload factice via le service rôle puis une URL signée de 300 secondes. Le bucket existant `claim-evidence` (10 MB, images/PDF/MP4) ne convient pas. Dans Vapi, créer l'assistant S08 séparément et le vérifier avant de réaffecter le numéro Twilio existant (actuellement Asclepios), opération autorisée par le propriétaire et coordonnée au déploiement. Configurer langue française, `artifactPlan.recordingEnabled=true` et un format stéréo compatible avec la limite, la transcription, les trois URL ci-dessus et les secrets correspondants. Le tool doit pouvoir renvoyer les faits candidats sous `facts[]` avec `field`, `value` et `excerpt` littéral du segment final. Le modèle ne doit pas inventer de temps, de statut de danger ou de blessure ; l'API valide conservativement les extraits. Enregistrer la version de l'assistant avant de renseigner son ID/version côté API. Aucun numéro, clé ou secret n'est versionné ici.

## Vérification avant ouverture aux appels

1. Déployer API + migration et vérifier le TLS HTTPS ainsi que l'upgrade WSS sur l'hôte **Preview réel**. Les fonctions Vercel disposent d'un support WebSocket bêta soumis à une durée maximale : mesurer une conversation complète, reconnexions Gradium et fin d'appel sur le déploiement choisi.
2. Tester d'abord l'assistant S08 distinct avec le mode Talk du dashboard Vapi, ou un appel sortant ciblant son `assistantId` si l'accès fournisseur le permet. Après cette vérification, réaffecter le numéro Twilio existant à S08 et faire un appel interne ; vérifier que Vapi utilise l'assistant enregistré, que Gradium STT reçoit uniquement l'appelant, que le TTS est audible, et que le tool pose les questions P0 manquantes.
3. Vérifier dans le dossier : un seul cas par `message.call.id`, transcript final des deux locuteurs dans le bon ordre, triage/urgence et provenance, puis enregistrement `available`, format/taille/SHA-256. Écouter par URL signée courte et confirmer qu'un autre utilisateur n'a pas accès.
4. Rejouer le webhook final et déclencher `call.artifact.upload` : aucun doublon de dossier ou d'objet. Vérifier un appel sans enregistrement et une récupération échouée ; le statut `error` doit apparaître, puis `POST /v1/voice/{case_id}/recording/retry` doit pouvoir reprendre le téléchargement.

La tâche de capture lancée après le webhook n'est pas durable si l'hébergement coupe la fonction après sa réponse. L'état `pending` persiste ; le rejeu Vapi ou la route de reprise permet de réparer. Pour une exploitation autonome, remplacer ce lancement par une file durable/worker et alerter sur les sessions `pending` anciennes. L'URL Vapi du 302, la clé privée et les octets audio ne doivent jamais être journalisés. Le validateur des faits ne prouve pas toute la sémantique française ; les formulations non reconnues restent à confirmer.

Contrats fournisseur : [événements Vapi](https://docs.vapi.ai/server-url/events), [outils Vapi](https://docs.vapi.ai/tools/api-request-vs-function), [enregistrement Vapi](https://docs.vapi.ai/assistants/retrieve-call-artifacts), [passerelle Gradium](https://docs.gradium.ai/integrations/agent-frameworks/vapi), [WebSocket Vercel](https://vercel.com/docs/functions/websockets).
