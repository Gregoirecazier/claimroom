# S07 — Aperçu et reçu du package simulé

**Déjà livré sur `main`.** `POST /v1/cases/{id}/registration` puis `/send` créent des actions `mode=mock` persistantes, liées au draft et à l'approbation. La séquence, l'idempotence, les conflits de clé et le retour du reçu existant sont contrôlés côté API. Aucun assureur, SI ou service mail n'est contacté.

**Écart à fermer.** L'interface actuelle affiche référence et statut mais pas l'aperçu du message et des pièces de la version approuvée, ni un reçu téléchargeable. Le reçu persistant ne porte pas encore une projection complète et figée du package envoyé ; les liens de lecture expirent et ne constituent pas une preuve durable du contenu.

**Contrat à ajouter.** Fournir une projection `TransmissionPreview` calculée par l'API depuis le draft courant : destinataire, corps, montant/devise, IDs et métadonnées/checksums des pièces, référence assureur simulée et mention `SIMULATION`. L'aperçu porte le digest et la révision et devient invalide si l'un change. Au `send`, conserver dans une enveloppe versionnée ou un document serveur immuable le `draft_id`, digest, `approval_id`, `registration_action_id`, destinataire, message, manifeste des pièces et horodatage ; permettre son téléchargement après rechargement, sans URL signée périmée ni média binaire public. Ne pas joindre une vidéo locale non finalisée. L'API revalide l'approbation, la version et l'idempotence existantes.

**Critères d'acceptation.** L'utilisateur lit l'aperçu avant de confirmer. Après envoi/rechargement, le reçu reproduit exactement la version approuvée. Un amendement ou une pièce différente invalide l'ancien aperçu ; enregistrement échoué empêche l'envoi. Double clic/retry donne le même reçu, sans deuxième action. Un état `unknown` reste visible et requiert réconciliation, jamais un succès implicite. Tests de snapshot, idempotence et cohérence pièce/version.

**Points d'appui.** `routes/review.py`, `mock_actions.py`, `actions`, `ReviewPanel.tsx`, aperçu de la PR 13 comme référence visuelle seulement.
