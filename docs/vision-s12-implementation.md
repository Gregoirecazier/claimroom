# S12 — Observations Vision sourcées

## Chemin livré

`POST /v1/cases/{case_id}/vision-observations` accepte `{evidence_id, processing_policy}`. L'API vérifie que la pièce appartient au dossier et n'accepte que PNG, JPEG, WebP ou MP4 privé. L'analyse est une action explicite `allow_new`; `reuse_only` ne déclenche aucun analyseur facturable. Par défaut, aucun fournisseur Vision réel n'est configuré : une tentative de préparation retourne et persiste `unavailable/analyzer_unavailable`. Avec `VISION_FIXTURE_MOCK=1`, l'API active un adaptateur mock réservé aux SHA-256 exacts des neuf médias synthétiques G1–G3. Ses observations prédéfinies ont été rédigées après inspection des médias, sont marquées `mode=mock` et ne constituent ni une analyse Vision réelle, ni une évaluation. Un média différent donne `unavailable/fixture_not_supported`.

Un adaptateur live conforme à `EvidenceAnalyzer` et au protocole `VideoAnalyzer` peut être injecté via la dépendance FastAPI `get_vision_service` dans un déploiement. Il reçoit seulement les octets privés du média et son type ou sa spécification de pipeline, jamais le récit, les rôles assuré/tiers, ni les corrigés G1–G3. Le mode mock n'ouvre aucun accès au répertoire des corrigés.

Les images sont lues du stockage privé côté serveur. Taille et empreinte vérifiée sont contrôlées avant l'appel. Une observation précise une zone normalisée (`x,y,width,height`), son statut (`observed`, `uncertain`, `not_visible`), texte factuel, catégorie, identifiant de véhicule local au média, plaque candidate brute, positions incertaines, confiance et calibration éventuelles. Le service ajoute lui-même `media_id` et `source_ref` vers **l'evidence ID du dossier** ; l'adaptateur ne peut pas citer une autre pièce. Une plaque partiellement lisible garde `?` et les indices incertains ; aucun champ du contrat intrinsèque ne qualifie un véhicule d'assuré ou de tiers.

Les vidéos passent par le registre S11 avec une nouvelle version de schéma de sortie (2), donc un ancien artefact non validé ne constitue pas un hit. Le traitement neuf vérifie la durée `mvhd` du MP4 et rejette les timecodes hors extrait avant la persistance de l'artefact. Un hit `reuse_only` ne relance pas l'analyseur et les sources sont reliées à l'evidence ID courant. Le mock et un futur adaptateur live ont des empreintes de pipeline différentes. Les résultats sont également enregistrés comme `provider_results` de type `vision`, avec mode, version, empreinte de pipeline, UUID de résultat et statut. L'UI affiche les observations, leur locator, les caractères incertains et les erreurs. Une erreur de stockage ou de modèle est `unavailable` ou `error` avec zéro observation, jamais une absence de preuve réussie. La lecture du dossier récupère ces résultats sans nouvel appel Vision.

`build_analysis_input` expose les observations valides comme citations d'evidence uniquement si le résultat est `matched`, lié à une pièce du dossier et à la version active du pipeline. Les résultats d'erreur, les artefacts d'une autre version et les références à une autre pièce ne deviennent pas des faits visuels citables. Les observations restent factuelles : les photos de dégâts ne démontrent ni mouvement, ni fuite, ni causalité. La contradiction avec le récit relève d'une analyse de dossier ultérieure et doit rester une sortie distincte à revoir.

## Vérification et limites

- Tests déterministes de source, zone, plaque ambiguë, photo inaccessible, résultat vide, `reuse_only`, liaison dossier et persistance PostgreSQL. Les neuf médias synthétiques exacts sont acceptés par le mock ; tout autre hash est refusé. G2 garde la voiture bleue et la voiture sombre sur des tracks distincts ; G3 décrit la trajectoire visible sans conclure à une fraude.
- Durée du MP4 G1 réel lue comme 5,042 s ; un timecode de 6 s est rejeté.
- Tests API complets exécutés avec PostgreSQL jetable ; build et tests web exécutés.
- Aucun fournisseur Vision réel et aucune évaluation I01–I05/L01–L03 n'ont été exécutés. Les observations des tests proviennent de fakes ou du script mock explicite, pas des corrigés.
- Les médias G2/G3 ne sont pas encore semés dans les dossiers par l'API. Leurs fichiers restent disponibles pour l'évaluation future, sans être transmis à l'analyseur.

## Cache photo et synthèse du dossier

Les photos utilisent aussi `media_analysis_artifacts`, avec une clé unique
`(user:<actor>:image:<mime>, sha256_verified, pipeline_fingerprint)`. Les deux
politiques cherchent le résultat existant avant tout appel : `reuse_only` retourne
`not_preprocessed` en cas d'absence, tandis que `allow_new` réserve une tentative.
Les demandes simultanées voient `processing`. Seules les observations validées
sont sauvegardées ; les erreurs peuvent être retentées explicitement. Le bail de
15 minutes et les limites de reprise décrites dans S11 s'appliquent aussi aux photos.
Le résultat survit aux redémarrages et se réutilise entre dossiers du même utilisateur ;
les références sont reconstruites vers la pièce du dossier courant. Aucune migration
supplémentaire n'est nécessaire. Les anciens `provider_results` photo sans artefact
ne sont pas rétroactivement réutilisés : une préparation est nécessaire.

La version de prompt (`VIDEO_PROMPT_VERSION`, commune au pipeline Vision live),
le modèle, le schéma et les paramètres déterminants participent à l'empreinte.
Changer le texte du prompt exige de changer sa version : aucun hash automatique
n'est calculé sur le texte du prompt. Le mock a sa propre version
`fixture-observations-v1`. Un futur adaptateur reçoit désormais la spécification
également dans `analyze_image(path, mime_type, pipeline)`.

Ce registre concerne la route historique `vision-observations`, dont l'adaptateur
reste un mock des fichiers G1–G3 exacts. Il ne couvre pas à lui seul le parcours
actuel de `MediaWorkflow` → `AccidentJourney` → `AstraAccidentAnalyzer`.
Ce dernier appelle réellement `gpt-6-astra` en raisonnement élevé avec les photos,
les images de vidéos à 4 fps et le récit. Un appel commun compare les candidats
et produit le rapport ; le temps d'attente comprend cet appel multimédia.
Le chemin Gemini subsiste également pour les anciennes routes d'analyse.

Le chemin Pipelex distinct assemble un snapshot textuel du dossier. Il ne transmet
pas les fichiers photo/vidéo et ne dispose pas de cache de synthèse. Ce changement
de modèle concerne ce chemin seulement : le parcours automatique reste sous Astra.

La méthode active `claims-analysis-v2.1.0` utilise maintenant `gpt-6-luna` via
Responses, avec `reasoning.effort=none`, température 0 et plafond de 3 000 tokens,
pour conserver le budget de sortie et le mode sans raisonnement supplémentaire.
Le catalogue local déclare Luna ; `ClaimsInferenceManager` adapte uniquement son
worker, car Pipelex 0.65 ne transmet pas le réglage de raisonnement pour les sorties
structurées. Les contrats de sortie et contrôles serveur sont conservés.
Voir la [documentation officielle Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).

## Cache de la requête Astra active

`AccidentJourney` branche le registre PostgreSQL sur `AstraAccidentAnalyzer`.
Avant l'appel Responses, SHA-256 est calculé sur le JSON canonique de la requête
complète : contenu des photos et frames, UUID sources, ordre des pièces, récit et
contexte transmis, texte intégral du prompt, schéma, modèle et paramètres.
Le scope est limité au propriétaire (`user:<id>:astra-request`). Une réponse
validée est persistée et un rejeu identique évite l'appel API. Deux demandes
concurrentes réservent une seule tentative ; le worker attend si elle est en cours.
Les échecs ne sont pas des hits. Le cache persiste même si l'enregistrement du
rapport de dossier échoue après la réponse du fournisseur.

Ce cache conserve l'analyse conjointe. Ajouter une photo, changer le récit, le
catalogue, le prompt ou le modèle invalide la requête. Les UUID sources font
partie de la requête : recopier les médias dans un autre dossier ne produit pas
un hit. Les fichiers sont encore lus et les frames préparées pour vérifier les
entrées ; seul l'appel au modèle est évité. Le cache par média de S11/S12 concerne
les routes historiques séparées, pas cette analyse conjointe. Aucun cache externe
OpenAI n'est nécessaire et aucune nouvelle migration n'est requise.
