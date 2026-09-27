# Analyse conjointe des photos et vidéos

Le bouton **Run analysis** utilise par défaut Gemini 3.8 Flash pour examiner ensemble
les pièces finalisées d'un même dossier : photos de l'assuré, vidéos de l'assuré,
vidéos CCTV et documents PDF. L'API existante reste
`POST /v1/cases/{case_id}/analysis-runs` avec `expected_state_version`.

## Activation

1. Définir côté serveur `ANALYSIS_PROVIDER=gemini`, `GEMINI_API_KEY` et
   `GEMINI_MODEL=gemini-3.8-flash`. Pour utiliser Pipelex/OpenAI sur les dossiers
   sans photo, vidéo ni PDF tout en conservant Gemini pour les dossiers avec ces
   pièces, choisir `ANALYSIS_PROVIDER=hybrid` et fournir aussi `OPENAI_API_KEY`.
   Ne jamais mettre ces clés dans une variable `VITE_*`.
2. Appliquer `uv run --env-file .env alembic upgrade head` depuis `apps/api`.
   La migration `20260926_video_evidence` ajoute les catégories et limites vidéo.
3. Configurer le bucket Supabase **privé** `claim-evidence` : limite 50 MiB,
   types `image/jpeg`, `image/png`, `image/webp`, `application/pdf`, `video/mp4`,
   `video/quicktime`, `video/webm`. L'API limite toujours photos/PDF à 5 MiB.
4. Déployer l'API et le web ; prévoir au moins 300 secondes pour la requête API
   et son proxy. L'analyse synchrone dispose de 240 secondes, puis d'un budget
   de nettoyage de 15 secondes. Aucun worker n'est nécessaire pour cette version.
5. Dans un dossier, importer les photos, puis choisir **CCTV video** pour le clip
   de caméra et **Insured video** pour les vidéos de l'assuré. Attendre la
   finalisation de chaque pièce, puis lancer **Run analysis**.

Le service lit uniquement les chemins persistés dans le dossier autorisé,
vérifie taille et SHA-256 lorsque présent, transmet les fichiers à l'API Files
Google et attend leur traitement. Une seule requête d'inférence contient toutes
les pièces et la déclaration. Les copies Google sont supprimées en fin de traitement
(y compris après erreur) ; en cas d'échec de suppression elles restent soumises
à l'expiration de l'API Files, actuellement 48 h. Ni clé Storage ni URL signée ne
sont envoyées dans le prompt. Il s'agit bien d'un transfert des médias vers Google.

## Résultat

`analysis_run.output.media_analysis` contient :

- Les UUID des pièces analysées et une synthèse croisant déclaration, photos et vidéos.
- Les observations, les véhicules et plaques transcrites, leur lisibilité et leur confiance.
- Les dégâts visibles par véhicule, les pièces touchées, la compatibilité avec le choc
  et une éventuelle fourchette de réparation en unités monétaires mineures.
- Une responsabilité probable (`insured`, `third_party`, `shared`, `undetermined`),
  son raisonnement, ses limites et des références aux pièces et aux secondes des vidéos.

Une plaque illisible reste `null`. Les coûts peuvent rester `null` si le contexte
ne permet pas de chiffrage. Le rapprochement entre véhicules de plusieurs pièces
reste explicite et incertain lorsque nécessaire. Les citations sont validées
contre les pièces du dossier ; la véracité des observations et l'exactitude des
horodatages demeurent à vérifier humainement. Les vidéos demandent un échantillonnage
à 5 images par seconde pour mieux examiner les chocs brefs. Elles sont analysées par le
modèle, sans garantie d'inspection exhaustive de chaque image ni d'OCR parfait.

La responsabilité est une hypothèse soumise au gestionnaire. Toute analyse visuelle
maintient le contrôle « evidence » en `needs_review`, avec une route `handler_review`.
La fourchette de réparation n'alimente pas le montant de recours `amount`, qui
nécessite toujours un résultat de chiffrage vérifié selon le contrat existant.
Les protections de révision empêchent une analyse devenue obsolète de créer un brouillon.

## Limites et exploitation

- 20 pièces maximum et 200 MiB cumulés par dossier pour un appel ; vidéos de 50 MiB
  maximum chacune. Préférer des extraits courts centrés sur l'accident. Une limite
  dépassée provoque un échec explicite, jamais une analyse silencieuse d'un sous-ensemble.
- MP4, MOV et WebM sont acceptés ; un codec non décodable par Google peut échouer.
- L'ajout d'une pièce nécessite une nouvelle analyse. Il n'y a pas d'acquisition
  automatique depuis une caméra : le clip déjà obtenu doit être importé.
- Le bouton de CCTV de démonstration reste une image synthétique distincte ; il ne
  récupère aucune vidéo réelle. Le reste du produit conserve ses dossiers synthétiques
  et ses annuaires d'assurance simulés.
- `ANALYSIS_PROVIDER=pipelex` conserve l'ancien traitement de métadonnées, sans lecture
  des médias, avec `OPENAI_API_KEY`. Aucun basculement silencieux n'est effectué.
- `ANALYSIS_PROVIDER=hybrid` choisit Pipelex pour un dossier sans média pris en
  charge, et Gemini dès qu'une photo, vidéo ou PDF est attaché. Le moteur choisi
  et sa version sont conservés sur le run ; l'absence de sa clé provoque un échec
  explicite, sans repli vers l'autre moteur.
- Sans clé, quota, stockage ou modèle accessible, le run est enregistré comme échoué
  avec un code explicite. Aucun résultat factice ne remplace un appel Gemini en échec.
- Les tests simulent Google et Storage. Un essai avec la clé de l'environnement et
  un dossier contenant les véritables médias reste nécessaire après configuration.

Références : [vidéos Gemini](https://ai.google.dev/gemini-api/docs/generate-content/video-understanding),
[sorties structurées](https://ai.google.dev/gemini-api/docs/generate-content/structured-output),
[API Files](https://ai.google.dev/gemini-api/docs/files).

## Parcours média unifié

Le bouton **Analyser les pièces** utilise `POST /v1/cases/{id}/media-analysis-runs`
et sélectionne toujours Gemini, même si `ANALYSIS_PROVIDER=pipelex` pour les dossiers
sans média. Une analyse Gemini réussie de la même révision et du même modèle est
réutilisée. Les observations, plaques, dommages et citations temporelles sont
visibles dans la section Analyse ; une ancienne révision n'est pas affichée comme actuelle.

Après finalisation d'une nouvelle photo ou vidéo (CCTV, scène ou assuré) via le dépôt
connecté, le backend enregistre d'abord la pièce puis appelle Gemini avec les
pièces du dossier. Le dépôt assuré utilise aussi ce parcours. L'ajout du lot G1
par son action dédiée déclenche la même analyse. L'appel attend la fin du traitement ;
une tâche persistée permet aussi la reprise par le worker Railway. Une erreur fournisseur est conservée dans
`latest_analysis`, le fichier reste enregistré et le gestionnaire peut relancer
l'analyse. Rejouer la finalisation d'un fichier enregistré ne relance pas Gemini.

La réception CCTV de démonstration reste une image synthétique ; elle ne représente
pas la récupération d'une vidéo réelle. Les vidéos réellement obtenues doivent être
déposées dans le dossier, ce qui déclenche l'analyse. Le panneau de correspondance
CCTV permet un envoi réel via Resend ; la récupération des réponses n'est pas
automatisée. Voir [la configuration du parcours média](media-workflow.md).

Les anciennes routes `video-analysis` et `vision-observations` restent compatibles
pour leurs clients existants ; l'interface gestionnaire n'utilise plus leurs actions
de préparation/récupération, qui étaient séparées du parcours Gemini.

Le parcours média utilise un contrat visuel dédié (`MediaAnalysis`) et un contexte
limité au récit déclaré et aux médias. Il ne demande pas à Gemini de rédiger un
recours ni de remplir les champs assureur/montant du dossier. La réponse est
validée côté serveur avant d'être conservée dans le format d'analyse existant.
Le repli après rejet du schéma ne consomme pas une tentative de reprise sur
erreur temporaire ; trois tentatives au maximum restent disponibles pour les
erreurs 500/502/503/504, dans le budget total de 240 secondes.

Si Gemini refuse le schéma visuel, le même appel aux médias est repris en mode
descriptif. La réponse est affichée comme un **rapport descriptif**, avec une
limitation explicite : aucune plaque, estimation ou citation temporelle structurée
n'est fabriquée à partir de ce texte. La responsabilité structurée reste
indéterminée et nécessite une revue humaine.
