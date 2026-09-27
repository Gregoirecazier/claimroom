# Photos reçues → garages proches → SMS Twilio

Le module recherche des réparateurs dans OpenStreetMap autour du **lieu du sinistre enregistré dans le dossier**. Il propose au maximum trois garages et un lien Google Maps par garage. La distance annoncée est à vol d’oiseau ; l’itinéraire Google Maps part de la position actuelle du destinataire.

## État de l’intégration

Le code comprend la recherche OSM, l’aperçu dans l’espace authentifié, les réglages par dossier, une file PostgreSQL durable, un worker d’envoi Twilio, un webhook MMS signé et les retours de livraison. Le mode par défaut est `mock` : les recherches OSM sont réelles, le SMS est enregistré mais aucun téléphone n’est contacté. La migration, le déploiement et l’activation du mode réel restent des opérations de mise en service.

Le déclencheur retenu est **l’enregistrement des images du dépôt web** (`GARAGE_SMS_TRIGGER=photos`, valeur par défaut). Twilio sert uniquement au SMS de réponse. Le raccordement se fait dans la transaction de finalisation des preuves, quelle que soit l’URL de la page de dépôt. Le webhook MMS reste une option pour une éventuelle évolution (`GARAGE_SMS_TRIGGER=mms`).

Pour `/depot`, le destinataire est repris automatiquement depuis le SMS d’invitation associé au lien privé exact ayant servi au dépôt. Il n’est pas nécessaire de ressaisir le numéro. Une désactivation explicite par le gestionnaire empêche cette activation automatique. Le panneau se trouve dans l’onglet **Photos** de la version actuelle de l’application.

## Parcours

1. Dans le dossier authentifié, renseigner une adresse précise du sinistre.
2. Dans **Garages à proximité**, rechercher et prévisualiser le SMS. Si plusieurs lieux sont possibles, sélectionner une adresse précise ; les résultats limités à une ville ne sont pas sélectionnables.
3. Pour le dépôt assuré, le numéro de l’invitation est récupéré automatiquement. Pour un ajout manuel par le gestionnaire, enregistrer le numéro au format international et activer la réponse. Le numéro n’est jamais déduit des images.
4. La finalisation du dépôt d’une photo de type `scene_photo`, `damage_photo` ou `vehicle_photo` crée un travail dans la même transaction que l’enregistrement de la preuve. Les photos suivantes ne créent pas d’autre SMS pour les mêmes réglages. **Traiter les photos déjà reçues** permet un déclenchement manuel explicite à partir des preuves du dossier. En mode alternatif `mms`, une image reçue via Twilio crée un travail rattaché au dossier via le numéro expéditeur.
5. Le worker géocode l’adresse (ou reprend le point confirmé), recherche les garages et prépare le message. Les rayons 500 m → 1 km → 2 km → 5 km sont examinés dans cet ordre ; arrêt au premier rayon contenant un garage. Un appel Overpass couvre 5 km, puis le filtrage progressif se fait localement. Après une erreur ou cinq secondes d’attente, une recherche Nominatim bornée à cette zone fournit une sélection de réparateurs OSM (non exhaustive), filtrée et triée par distance. Le résultat est mis en cache quinze minutes. Les trois résultats les plus proches sont retenus.
6. En mode réel, le worker soumet le SMS à Twilio. Le suivi distingue acceptation, envoi, livraison et échec ; une acceptation ne prouve jamais la livraison.

La recherche utilise `shop=car_repair` sur les nœuds, chemins et relations OSM, avec dédoublonnage des représentations proches d’un même établissement. Les garages ne sont pas présentés comme agréés, disponibles ou ouverts. Aucun rendez-vous, devis ou engagement de prise en charge n’est créé. [Tag OSM](https://wiki.openstreetmap.org/wiki/Tag:shop%3Dcar_repair), [Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL).

## Mise en service

Appliquer les migrations avant de déployer le code :

```sh
cd apps/api
uv run --env-file .env alembic upgrade head
```

Configurer les variables serveur de `.env.example` :

- `GARAGE_SMS_MODE=mock` pour la répétition ; `live` pour les vrais SMS.
- `GARAGE_SMS_TRIGGER=photos` pour le dépôt web retenu ; `mms` pour un éventuel canal MMS compatible.
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`.
- `TWILIO_PHONE_NUMBER` pour recevoir les MMS ou envoyer sans Messaging Service.
- `TWILIO_PUBLIC_BASE_URL` : origine HTTPS publique de l’API, sans chemin ni paramètres.
- `TWILIO_MESSAGING_SERVICE_SID` pour utiliser un pool d’expéditeurs adapté au pays (remplace le numéro expéditeur pour les envois).
- `OSM_USER_AGENT` : nom de l’application et contact opérateur réel.

Les secrets Twilio restent exclusivement côté serveur. Aucun secret n’est nécessaire pour les [liens Google Maps](https://developers.google.com/maps/documentation/urls/get-started).

Les noms déjà utilisés dans le projet, `TWILIO_SMS_MESSAGING_SERVICE_SID` et `TWILIO_SMS_WEBHOOK_BASE_URL`, sont également acceptés comme valeurs de repli. Le suffixe existant `/v1/webhooks/twilio/sms` est retiré pour construire les callbacks propres aux garages ; les callbacks du SMS d’invitation restent inchangés.

Lancer le worker avec le même environnement et la même base que l’API :

```sh
uv run --env-file .env python -m claim_api.garage_worker
# Pour traiter au plus un travail, puis quitter :
uv run --env-file .env python -m claim_api.garage_worker --once
```

Le worker est un processus supervisé séparé : une fonction Vercel ne le maintient pas en vie. Les endpoints web n’effectuent pas l’envoi et ne lancent pas de tâche mémoire susceptible d’être perdue après la réponse HTTP.

Si le numéro reçoit réellement des MMS, configurer **un webhook HTTP POST** vers `https://<api>/v1/webhooks/twilio/inbound`. Il vérifie `X-Twilio-Signature`, le compte, le numéro receveur et les références de médias Twilio. Un message contenant une image crée un travail ; un SMS texte seul ne déclenche rien. Le numéro expéditeur doit correspondre à **un seul dossier activé** ; en cas de zéro ou plusieurs correspondances, le webhook retourne `mms_case_not_unique` sans envoyer de SMS. Ne pas laisser plusieurs dossiers activés pour le même numéro. [Contrat webhook](https://www.twilio.com/docs/messaging/guides/webhook-request), [signatures](https://www.twilio.com/docs/usage/security).

Le webhook conserve les références privées des médias reçus dans le travail. Il **ne télécharge pas ces fichiers dans Supabase et ne les ajoute pas à l’analyse Gemini** : cette importation reste à raccorder si le canal MMS est utilisé. Le dépôt web existant, lui, sauvegarde les preuves dans le dossier.

Si un webhook reçoit déjà les photos, conserver son traitement et y raccorder la création du travail (`GarageRepository.enqueue_mms`) après réception. Ne pas remplacer ce webhook sans reprendre son import des médias. Un transfert HTTP brut vers une autre URL ne conserve pas une signature Twilio valide : la signature dépend de l’URL appelée.

Le worker fournit automatiquement à Twilio un callback propre au message : `https://<api>/v1/webhooks/twilio/status/<message_uuid>`. Les callbacks signés sont acceptés même s’ils arrivent avant la réponse de création du SMS. [Statuts Twilio](https://www.twilio.com/docs/messaging/guides/outbound-message-status-in-status-callbacks).

## Limites et reprise

- Le navigateur interrompt une requête d’aperçu après 25 secondes et propose une relance ainsi qu’un lien de recherche Google Maps. La carte conserve le point d’accident.
- Aucun résultat à 5 km : SMS indiquant qu’aucun garage n’est référencé dans ce rayon et invitant à contacter le conseiller. Une panne OSM produit un échec visible, jamais un faux résultat vide.
- Adresse absente, ambiguë ou trop étendue : état `needs_location`, sans SMS. Préciser l’adresse ou confirmer un point, enregistrer les réglages, puis traiter les photos déjà reçues.
- La confirmation d’un point est liée au texte exact du lieu. Modifier le lieu invalide ce point ; un travail préparé pour une autre adresse est annulé.
- Désactiver ou modifier les réglages annule les travaux encore en attente. Une requête déjà soumise à Twilio ne peut pas être rappelée. Enregistrer des réglages identiques conserve la même série de photos et ne crée pas une nouvelle réponse.
- Une simulation et un envoi réel utilisent des séries distinctes : après passage en mode réel, traiter les photos déjà reçues peut créer une réponse réelle. Repasser le worker en mode `mock` annule les travaux réels qu’il récupère encore en attente, sans les envoyer.
- Un webhook MMS répété conserve le même travail grâce au `MessageSid`. Une nouvelle MMS distincte crée une nouvelle réponse ; le dépôt web regroupe toutes les photos sous les mêmes réglages.
- Délai d’attente ou erreur réseau après soumission : état `unknown`, **aucune nouvelle tentative automatique**, car Twilio peut avoir accepté le SMS. Vérifier le journal Twilio ; un callback tardif peut réconcilier l’état. Après interruption du worker, les travaux bloqués depuis cinq minutes deviennent `failed` (recherche) ou `unknown` (soumission). Aucun bouton ne relance automatiquement ces états.
- Les recherches sont mises en cache : géocodage 24 h, garages 15 min, 256 entrées maximum par processus. Le géocodage est limité à une requête toutes les 1,1 seconde dans un processus. Pour la démo avec le Nominatim public, utiliser **un seul processus de recherche à la fois** (ne pas lancer des aperçus concurrents au worker). Pour plusieurs instances, configurer un géocodeur dédié ou un limiteur/cache partagé. Respecter la [politique Nominatim](https://operations.osmfoundation.org/policies/nominatim/).
- Le SMS inclut l’attribution OSM et peut occuper plusieurs segments facturés. Les liens Google Maps restent complets, sans service de raccourcissement.

## API et validation

Routes de dossier authentifiées : `POST /garage-preview` (`expected_state_version`, `origin` facultatif), `GET /garage-sms`, `PATCH /garage-sms` (`expected_state_version`, `recipient`, `enabled`, `origin`), `POST /garage-sms/photo-received`, toutes sous `/v1/cases/{case_id}`. Les consultations et mutations vérifient le propriétaire. Les réglages et statuts SMS ne modifient pas les décisions ou approbations du dossier.

`tests/test_garages.py` vérifie les rayons, le tri, les doublons, les liens, les lieux ambigus, les erreurs OSM, les signatures et paramètres Twilio, l’isolation des dossiers, les versions, le mode simulé et les soumissions incertaines. Les tests PostgreSQL vérifient aussi le destinataire du lien privé exact, l’activation automatique après dépôt, le respect d’une désactivation et l’absence de doublon lors d’une finalisation répétée. Les tests de migration PostgreSQL demandent la base de test jetable utilisée par la CI.

Lors de la vérification du 27 septembre 2026, Nominatim a répondu ; les deux instances publiques Overpass essayées étaient indisponibles. Aucun SMS réel n’a été envoyé. Un essai complet sur les services externes reste à faire avant activation.

Validation locale : 506 tests API réussis sur PostgreSQL jetable vierge (un essai payant de modèle exclu), 103 tests web réussis et compilation TypeScript/Vite réussie.
