# S17 — Tester l'agent vocal dans le navigateur

**But.** `/voice-test` permet au gestionnaire démo de parler au même assistant Vapi/Gradium que le numéro S08, depuis le micro et les haut-parleurs du navigateur. Aucun appel Twilio ni numéro de téléphone n'intervient.

## Contrat de la tranche

1. La page exige la session Supabase du gestionnaire démo. Un clic explicite demande l'accès au micro **avant de créer l'appel Vapi**, puis démarre l'appel ; un autre le termine. Afficher les états prêt, connexion, en cours, terminé et erreur, ainsi que les segments finaux de l'appelant et de l'assistant. Un refus de micro, une autorisation en attente pendant quinze secondes, une coupure ou plus de vingt secondes sans connexion donne une erreur lisible et permet de réessayer. Arrêter l'appel lors de la fermeture de la page.
2. `POST /v1/voice/web-test/session`, authentifié, n'accepte que `VOICE_OWNER_USER_ID` et l'origine autorisée par `CORS_ORIGINS`. Quand `VOICE_WEB_TEST_ENABLED=true`, il signe un JWT public Vapi d'au plus cinq minutes à partir de la clé privée **serveur** existante. Les restrictions limitent ce JWT à l'origine de la requête, à `VOICE_ASSISTANT_ID` et interdisent les assistants transitoires. Réponse non cacheable `{token, assistant_id, expires_at}`. La clé privée n'est jamais livrée au navigateur.
3. Le navigateur utilise `@vapi-ai/web` avec ce jeton et l'assistant publié S08. Le webhook existant continue de recevoir les transcriptions finales et le rapport de fin ; les événements de type `webCall` portent `telephony_provider=web` dans le contrat et en base. Ils créent un dossier synthétique `voice_web`, séparé des appels Twilio, tout en conservant la provenance, le triage, l'audit et l'enregistrement original en Storage privé. `GET /v1/voice/web-test/calls/{call_id}` résout le dossier seulement pour son propriétaire, pour ouvrir ensuite l'écran gestionnaire et écouter l'enregistrement.
4. Un appel web n'a pas de numéro d'appelant. Le choix `caller_id` du suivi gestionnaire reste indisponible. Aucun média audio n'est stocké dans le navigateur au-delà de la durée de la conversation.

## Suite après l'appel

Une fois le dossier vocal synchronisé, `/voice-test` ouvre une conversation **WhatsApp simulée** sous le nom **Claimroom insurance**. Aucun message n'est remis au réseau WhatsApp et aucun numéro n'est requis. L'agent guidé ne redemande que les éléments absents après l'appel, dans cet ordre : lieu, date et heure, blessés, danger actuel, référence de sinistre, référence de contrat, modèle et immatriculation du véhicule assuré, photo d'ensemble, photo des dégâts. Les références, les détails du véhicule et les photos peuvent être passés ; ils restent alors manquants dans le dossier. Une fois ces questions traitées, l'assuré peut ajouter un complément libre ou une autre photo. La plaque d'un tiers éventuellement mentionnée dans un texte reste une déclaration à corroborer, pas un résultat de lookup.

Les réponses de texte sont enregistrées dans l'intake du dossier via l'API gestionnaire ; les photos JPEG, PNG ou WebP de 5 Mo maximum sont ajoutées à ses pièces privées via les upload intents existants. L'historique visuel du chat est conservé pour la session du navigateur et repris dans le dossier sous **Claimroom insurance**, avec les pièces enregistrées (images, PDF et autres documents) dans le fil. Les informations et pièces validées demeurent dans le dossier après la session, mais le texte des bulles ne devient pas un historique serveur. Pour les appels web, aucun numéro destinataire ni formulaire de rappel n'est affiché dans le dossier. Le suivi par numéro des appels téléphoniques reste accessible dans un volet distinct. Le panneau gestionnaire peut créer des messages d'ouverture WhatsApp simulés (`channel=whatsapp`) pour l'accès au dépôt sécurisé ; les anciens SMS restent en base comme historique.

Un appel terminé avec un triage vocal `incomplete` peut donner lieu au message d'ouverture simulé : le suivi sert justement à compléter la déclaration. Le triage historique de l'appel reste `incomplete` ; les réponses ultérieures sont des corrections de l'intake, pas une réécriture de la transcription. Les états `collecting`, `error` et `urgent_human_handoff` continuent de bloquer la création du message.

## Acceptation

- Sur le site de production, le gestionnaire connecté démarre, parle et termine un appel sans Twilio. La transcription finale apparaît sur la page ; le dossier lié affiche la même transcription et l'enregistrement original après réconciliation Vapi.
- Sans connexion, depuis une autre origine, avec un autre utilisateur ou sans configuration serveur, aucun jeton n'est émis. Le jeton n'autorise qu'un assistant et l'origine du site.
- Le refus du micro et l'échec Vapi n'affichent ni clé privée ni faux succès. Un appel interrompu reste `incomplete` ou `error`, jamais `complete` par défaut.
- Tests API ciblés sur l'autorisation, les restrictions JWT, le type `webCall` et l'isolation ; tests web ciblés sur démarrage/arrêt, transcription finale et erreur.

## Configuration

API : `VOICE_WEB_TEST_ENABLED=true`, `VAPI_ORG_ID`, `VAPI_PRIVATE_API_KEY`, `VOICE_ASSISTANT_ID`, `VOICE_OWNER_USER_ID` et l'origine web dans `CORS_ORIGINS`. Le projet web réutilise `VITE_API_BASE_URL` et la connexion Supabase. Aucun identifiant Vapi privé dans Vite.

Sources fournisseur : [appels web Vapi](https://docs.vapi.ai/quickstart/web), [JWT public Vapi](https://docs.vapi.ai/customization/jwt-authentication), [récupération des enregistrements](https://docs.vapi.ai/assistants/retrieve-call-artifacts).
