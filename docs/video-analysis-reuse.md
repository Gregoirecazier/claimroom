# S11 — Réutilisation des observations vidéo

## Commande et coût

`POST /v1/cases/{case_id}/evidence/{evidence_id}/video-analysis` accepte
`{"processing_policy":"reuse_only"}` (valeur par défaut) ou
`{"processing_policy":"allow_new"}`. Le premier mode ne réclame jamais un
nouvel artefact et n'appelle jamais l'analyseur. Sur un miss, il retourne
`not_preprocessed`; `pipeline_changed=true` signale qu'une sortie existe pour
les mêmes octets avec une autre version du pipeline. Le second mode est une
préparation explicite. Une analyse déjà en cours retourne `processing` et
une sortie compatible retourne `reused` dans les deux modes.

Le `main` actuel ne dispose d'aucun adaptateur Vision vidéo. Le service expose
un port `VideoAnalyzer` et refuse `allow_new` avec `analyzer_unavailable` tant
qu'un adaptateur n'est pas injecté côté serveur. L'interface affiche ce refus,
sans appel fournisseur. L'adaptateur doit recevoir uniquement le fichier MP4
privé temporaire et `VideoPipelineSpec`, puis retourner des observations
intrinsèques horodatées et des compteurs d'usage numériques. Aucun récit,
assureur, responsabilité ou destinataire ne doit entrer dans l'artefact.

## Identités et isolation

Un upload MP4 peut atteindre 10 Mo. Sa SHA-256 client reste déclarative à
la finalisation. Lors de la première demande vidéo, le serveur relit l'objet
privé en flux, vérifie taille et hash, puis stocke `sha256_verified`. Un
mensonge ou une taille différente marque l'evidence `mismatch` et bloque
l'analyse. Les médias G1 semés par le serveur utilisent leur empreinte déjà
vérifiée. Avant tout appel à l'analyseur, les octets téléchargés sont hachés
une nouvelle fois pour éviter de facturer un objet modifié. Le serveur lit le
chemin privé avec son identité de service ; aucune URL signée n'entre dans le
registre ni dans la trace.

La clé unique est `(user:<actor_uuid>, sha256_verified,
pipeline_fingerprint)`. Le scope utilisateur est le périmètre d'autorisation
disponible aujourd'hui ; une future notion d'organisation peut le remplacer
sans ouvrir les artefacts entre tenants. Le fingerprint SHA-256 porte le nom
d'analyseur, l'identifiant exact du modèle, les versions de prompt, sortie,
OCR et prétraitement, la cadence, le redimensionnement et les paramètres
déterminants (`VIDEO_DETERMINISTIC_PARAMETERS_JSON`). Changer l'un d'eux
force une nouvelle préparation explicite.

`media_analysis_artifacts` conserve un seul résultat intrinsèque par clé,
avec statut, tentative clôturée par token, bail de 15 minutes, compteurs
d'usage et historique d'échecs. L'unicité SQL et le verrou de ligne empêchent
deux workers de réclamer simultanément la même analyse. Une sortie `failed`
ou un bail expiré ne peut être relancé qu'avec `allow_new`. La clôture d'une
ancienne tentative est rejetée si une nouvelle a été réclamée. Un adaptateur
de production devra imposer un timeout inférieur au bail ou prolonger celui-ci
pendant un traitement long ; sans idempotence fournisseur, la reprise d'une
tentative expirée peut sinon coûter un second appel.

Chaque dossier reçoit sa liaison `(case_id,evidence_id,artifact_id)` ; elle
vérifie propriétaire, scope et hash puis incrémente la révision du dossier et
invalide brouillon/approbation antérieurs. Les références sources utilisent
**l'ID evidence local** et `video:<début_ms>-<fin_ms>`. L'analyse de dossier ne
reçoit que les observations de la version de pipeline courante, puis recalcule
les conclusions dans son propre contexte. Le registre ne propage aucune
conclusion entre dossiers.

La trace Logfire, lorsqu'elle est activée, contient uniquement
`analysis_reused`, `artifact_id`, `duration_ms`, statut et coût évité estimé
issu du compteur d'usage précédent. Elle ne contient ni octets, ni URL signée,
ni récit.

## Exploitation

1. Appliquer la migration Alembic `20260925_video_reuse` sur la base cible.
2. Configurer les champs `VIDEO_*` du pipeline et l'adaptateur vidéo côté API.
3. Préparer une fois G1 avec `allow_new`, puis utiliser `reuse_only` pour les
   autres dossiers autorisés portant les mêmes octets.
4. Après `reused` ou `completed`, relire le dossier avant de relancer l'analyse
   de dossier : la liaison a changé sa révision.

Le hashing d'un upload de 10 Mo est actuellement synchrone dans la commande
vidéo ; un worker dédié sera nécessaire si la latence Storage rend cette
requête interactive trop longue. Les uploads vidéo navigateur utilisent le
flux signé Supabase et restent soumis aux limites configurées sur le bucket.
