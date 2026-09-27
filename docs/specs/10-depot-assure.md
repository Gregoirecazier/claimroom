# S10 — Lien assuré, corrections et dépôt de pièces

**État et dépendances.** Le droit de dépôt, la page assuré, les uploads S02 et la galerie G1 sont livrés. La galerie G2/G3 utilise les fichiers synthétiques déjà présents côté API et les attache sur sélection au stockage privé du dossier concerné. S08 crée le dossier et la provenance vocale. La banque de dix vidéos envisagée initialement n'a pas été fournie.

## Droit d'accès

- Créer un jeton aléatoire opaque à forte entropie, stocké sous empreinte côté serveur. Il lie `case_id`, capacités `read_summary|correct_intake|upload_evidence|attach_demo_media`, date d'expiration, date de révocation et version du droit. Ne placer ni `case_id` autonome ni chemin Storage comme autorisation. Définir une durée explicite et un renouvellement depuis la session gestionnaire ; ne jamais prolonger silencieusement le même jeton.
- Pour S09, `issue(case_id, content_revision, ttl)` retourne `{grant_id, url, expires_at}` une seule fois à l'aperçu authentifié ; `validate` vérifie l'empreinte du jeton, le dossier, la révision, l'expiration et la révocation ; `associate(grant_id, message_id)` ne peut réussir que pour un message du même dossier, dans la transaction de création. Un droit expiré ou abandonné n'est pas réutilisé. La migration expose `deposit_grants(id, case_id, token_sha256, capabilities, content_revision, expires_at, revoked_at, message_id, created_at)` avec contraintes FK et unicité du hash, sans jeton brut.
- `GET /v1/deposit/session` échange le jeton contre une session courte et limitée. Les routes de dépôt vérifient cette session, le dossier, la capacité et l'expiration **à chaque appel**, y compris lecture de résumé, émission de signed upload, finalisation et sélection de média. Le jeton n'apparaît pas dans les traces, Referer externe, localStorage ou erreurs. Réduire les données affichées à ce dont l'assuré a besoin ; masquer résultats de lookup, notes internes et coordonnées du correspondant.
- Une session Supabase de gestionnaire reste séparée : le droit assuré ne peut ni lancer analyse/lookup, ni approuver, ni transmettre, ni lire les pièces d'un autre dossier. Retrait du droit ou expiration : écran de reprise sans fuite du contenu. Les URL de lecture/écriture Storage restent courtes et privées.

## Parcours assuré

1. Afficher le résumé des déclarations vocales, avec étiquette « selon votre déclaration », date/heure manquante et provenance de l'heure. Montrer l'état de transcription et d'enregistrement sans confondre texte automatique et audio original ; la correction d'un fait n'efface pas la transcription source.
2. Soumettre une correction structurée avec `expected_state_version`, champ, nouvelle valeur et raison facultative. Enregistrer une nouvelle source `insured_correction` avec auteur/session, horodatage et champ remplacé ; incrémenter `content_revision`, rendre l'analyse et l'approbation précédentes obsolètes. Réponse `409 stale_case` avec version courante si le dossier a changé. Une correction ne remplace pas sans trace la déclaration initiale.
3. Réutiliser les upload intents S02 pour images/PDF/vidéo. La finalisation confirme `case_id`, capacité, MIME, taille, chemin émis et checksum selon le statut réel (`verified` ou `client_declared`). Une pièce incomplète ou dont le MIME/taille diffère n'est pas visible comme preuve exploitable. Prévoir reprise visible après échec ou URL expirée. Ne jamais analyser un upload seulement commencé.
4. Afficher les médias synthétiques disponibles pour le scénario du dossier avec ID stable, miniature, durée vidéo et provenance `demo_fixture`. Une sélection copie le fichier dans le stockage privé **du dossier courant** et crée un attachement traçable ; un second clic reste sans effet. Aucun résultat Vision ni annotation de référence n'est copié. Les photos G2/G3 sont indiquées comme reconstructions de la vidéo, et G3 identifie les photos de la Toyota comme celles du véhicule tiers sans en inférer la responsabilité.
5. Après soumission, afficher les pièces reçues, statuts de contrôle, corrections et prochaines étapes. Une scène contraire au récit reste une contradiction à analyser par S15 ; ne pas réécrire le récit au moment de l'attachement.

## Contrat API à livrer

Documenter dans `docs/contracts.md` les routes de création/révocation du droit par le gestionnaire, d'échange du lien, de lecture du résumé, de correction et de dépôt. Réutiliser le service de preuves existant derrière un principal « assuré invité » limité au dossier, sans dupliquer les règles de validation. Toutes les mutations portent `expected_state_version` et audit. Les réponses distinguent `expired_grant`, `revoked_grant`, `forbidden_capability`, `stale_case`, `invalid_upload` et `storage_unavailable`. Aucun accès invité n'est activé sur les routes gestionnaire existantes par simple présence d'un jeton.

## Recette et tests ciblés

- S05 de la matrice UX : deux dossiers, deux jetons, un jeton expiré et un révoqué ; essayer lecture, correction, upload intent, finalisation et signed read URL croisés. Chaque tentative est refusée côté API et ne change aucune ligne.
- Sur G1, corriger un fait puis déposer une photo et une vidéo ; vérifier la fiche gestionnaire après rechargement, la nouvelle `content_revision` et l'invalidation de l'analyse/approbation. Vérifier que la transcription originale et l'audio S08 restent accessibles au gestionnaire.
- MIME déguisé, dépassement de taille, checksum déclaré incohérent, lien Storage expiré et upload interrompu donnent une erreur récupérable ; aucune pièce inachevée dans l'analyse. Deux clics sur « sélectionner G1 » ne créent qu'un attachement actif.
- Interface mobile utilisable depuis un lien SMS ; aucune annotation corrigée G1–G3 ni secret dans les données livrées au navigateur. Build web et tests API ciblés, puis parcours manuel depuis le lien émis par S09.

**Points d'appui :** `apps/api/claim_api/routes/evidence.py`, `apps/api/claim_api/evidence_service.py`, `docs/contracts.md`, `docs/jury-path.md`, `docs/demo/evaluation-ux.md`.

### Parcours du chat privé simplifié

Après confirmation du récapitulatif, demander uniquement la date et l’heure de l’accident si elles manquent, puis inviter à joindre des photos du véhicule et des dégâts avec le bouton +. Si la date/heure est déjà connue, passer directement aux photos. Accuser réception après l’enregistrement d’une photo et permettre d’en ajouter d’autres. Un PDF ou une vidéo ne remplace pas une photo dans ce parcours.

Ne plus relancer automatiquement sur le contrat, le lieu, le danger, les blessures, ni les compléments issus de l’analyse. Les corrections explicites du récapitulatif restent possibles. Le contenu du SMS et le parcours d’appel restent inchangés.
