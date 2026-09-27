# Photos/vidéos → Gemini → assureur de démonstration → brouillon garage

Le WhatsApp-like de la PR #81 utilise les routes de dépôt assuré existantes.
Chaque photo ou vidéo finalisée est enregistrée dans les pièces avant l'analyse.
La finalisation écrit aussi une tâche durable dans `case_media_workflows`, dans
la même transaction que la pièce. Les PDF ne déclenchent pas ce parcours.
L'analyse Gemini commence immédiatement après la finalisation et couvre les
pièces actuellement disponibles. Une vidéo reçue après une photo entraîne une
nouvelle analyse de la nouvelle révision. Rejouer une finalisation ne crée pas
de nouvelle pièce ni de nouvelle tâche.

La tâche conserve un bail pour empêcher deux workers de traiter simultanément
la même révision. Une modification rend les anciennes tâches obsolètes. Un
échec fournisseur conserve les pièces et les résultats intermédiaires. Les
soumissions Dust incertaines demandent une vérification dans Dust, sans créer
automatiquement une seconde conversation.
Le bouton « Relancer le traitement » reprend un échec confirmé : il réutilise
une analyse Gemini réussie et ne relance Gemini que si cette analyse a échoué.

Après Gemini, les plaques lisibles et complètes sont recherchées exactement
dans `fixtures/mock-insurance/fr.json` et `uk.json` : 1 000 lignes par pays.
La date du sinistre détermine la validité du contrat. Les plaques partielles ne
sont jamais complétées à partir du registre ; une absence ne signifie pas qu'un
véhicule réel n'est pas assuré. La plaque, les citations et l'identifiant du
run Gemini sont conservés avec chaque résultat. Ces correspondances fictives
ne remplacent pas la validation d'implication du véhicule et ne deviennent pas
automatiquement le destinataire du recours.

L'agent Dust `garage` est lancé automatiquement avec les observations Gemini.
Il prépare un email demandant un devis détaillé ; son `draft_body` devient un
brouillon éditable dans l'application. Les agents `repair`/`legal` ne sont pas
automatiquement lancés par ce changement. Leurs rapports récents, s'ils sont
présents, sont transmis selon le contrat Dust existant. Aucun tarif n'est
inventé si aucun devis ou barème n'a été fourni.

## Reprise sans navigateur ouvert

Le service Railway existant peut réveiller l'API toutes les dix secondes.
Définir côté **API Vercel et Railway**, avec exactement la même valeur :

- `MEDIA_WORKER_TOKEN` : secret aléatoire d'au moins 32 caractères.
- Côté **Railway uniquement**, `MEDIA_WORKER_API_URL` : origine HTTPS de l'API,
  par exemple `https://claimroom-demo-api.vercel.app`.

Le worker appelle `POST /internal/media-workflow/tick`, protégé par ce secret.
Il ne lit ni ne transmet les dossiers : l'API traite une tâche enregistrée en
base. La clé Dust et la clé Gemini restent uniquement côté API. Le nouveau
fichier du worker est inclus dans l'image Docker du bridge.

La page dossier peut également faire avancer une tâche via une route
authentifiée limitée à son propriétaire. Ce repli fonctionne lorsque le
gestionnaire garde la page ouverte ; le worker Railway est nécessaire pour
terminer automatiquement les tâches différées sans navigateur.

L'ajout direct d'un média reste synchrone et peut attendre le budget Gemini
existant (240 secondes). La tâche durable permet la reprise si la requête se
termine prématurément. Une analyse Gemini encore marquée en cours après six
minutes réclame une relance manuelle, plutôt qu'un second appel non maîtrisé.

## Vraies demandes email avec Resend

Configurer uniquement sur l'**API Vercel** :

- `RESEND_API_KEY` : clé d'envoi du compte Resend.
- `CLAIMROOM_EMAIL_FROM` : adresse simple sur un domaine vérifié dans Resend.
- `CLAIMROOM_EMAIL_REPLY_TO` : boîte effectivement consultée pour les réponses.

Le panneau CCTV demande la caméra, le responsable, son email et la page source
du contact. Il compose une demande de conservation/disponibilité couvrant cinq
minutes avant/après l'heure déclarée. Camérci sert au repérage ; aucune adresse
email n'est déduite du nom d'un opérateur. Les brouillons des dossiers fictifs
sont identifiés comme démonstrations dans leur objet et leur corps.

Le gestionnaire lit et peut modifier le brouillon, puis sélectionne l'envoi à
ce destinataire et clique « Envoyer l'email ». Le serveur vérifie propriétaire,
version et révision avant de réserver l'envoi. Les textes enregistrés sont les
textes envoyés. Aucun média privé ni URL signée n'est joint automatiquement.

L'envoi utilise `POST https://api.resend.com/emails` avec une clé d'idempotence
stable pour la version du brouillon. Une acceptation est enregistrée avec
l'identifiant Resend ; elle ne prouve ni livraison ni lecture. Une réponse
réseau incertaine est conservée comme telle et ne peut pas être éditée. Après
23 heures, toute reprise exige une réconciliation côté Resend : leurs clés
d'idempotence expirent après 24 heures.

Sources : [envoi Resend](https://resend.com/docs/api-reference/emails/send-email),
[idempotence Resend](https://resend.com/docs/dashboard/emails/idempotency-keys).

La réception des réponses email et de leurs pièces jointes n'est pas branchée :
une vidéo CCTV obtenue en retour peut être déposée dans le dossier ; elle suivra
alors automatiquement le même parcours Gemini. Le WhatsApp-like reste une
simulation privée dans le navigateur, pas une connexion à WhatsApp/Meta.

## Déploiement

Appliquer les migrations jusqu'à `20260926_media_workflow`, après
`20260926_fake_whatsapp`, puis déployer API, web et bridge. Le parcours reste
soumis au contrôle d'auteur du workflow de publication existant. Les tests
utilisent des fournisseurs remplacés ; aucun email réel n'est envoyé par CI.

## Analyse automatique de toutes les réceptions

Les dépôts gestionnaire et assuré (y compris le lien de dépôt partagé par SMS
ou WhatsApp), les médias du téléphone WhatsApp simulé, les pièces G1 ajoutées
en lot, les médias G1–G3 choisis depuis l’espace assuré et la réception CCTV
simulée écrivent désormais tous une tâche durable dans la transaction de la
pièce. L’appel Gemini suit le commit ; la reprise du même dépôt reste idempotente.
Une vidéo obtenue auprès d’un opérateur repéré dans Camérci suit ce parcours dès
qu’elle est importée au dossier. Camérci reste un annuaire : il ne livre pas
lui-même de vidéos et aucun connecteur de réception de MMS/Meta/email n’est
ajouté par cette correction.

Le contrat `gemini-joint-media-v2` exige une réponse structurée même si Gemini
refuse le schéma transmis à l’API : le schéma est alors inclus dans le prompt,
les mêmes fichiers sont joints et la réponse JSON est validée côté serveur.
Un texte libre ne devient plus un résultat « prêt » avec plaques et dégâts vides.
Chaque véhicule visible doit avoir une entrée plaque (éventuellement illisible),
une appréciation de responsabilité sourcée et une description des dégâts ou de
leur impossibilité d’évaluation. L’interface montre les quatre rubriques,
les sources, les incertitudes et les fourchettes indicatives lorsqu’elles sont
justifiables. Les anciennes analyses restent consultables ; une actualisation
les remplace par le contrat courant. Aucun montant n’est approuvé automatiquement.

Après un 429, une indisponibilité réseau ou un 5xx Gemini, la tâche est remise
à disposition après 60 puis 120 secondes, avec un maximum de trois tentatives.
Le worker ou la page dossier peut reprendre la tâche. Une erreur de clé,
un résultat invalide ou l’épuisement des tentatives reste explicite et peut
être relancé manuellement. Augmenter le quota ou activer la facturation Google
reste nécessaire si le 429 provient d’un quota durablement épuisé.

Vérification du 26 septembre : le dossier de la capture atteint bien Gemini
après actualisation de l’onglet, mais l’appel réel renvoie HTTP 429. Les tests
remplacent les fournisseurs ; ils ne valident pas le quota du projet déployé.

Les résultats visuels sont aussi projetés dans les lignes sourcées du compte
rendu et son export. Les plaques, dégâts et responsabilités restent liés aux
pièces et secondes de vidéo originales. Une révision ultérieure les marque
obsolètes ; un chiffrage visuel ne remplace jamais l’estimation approuvée.
