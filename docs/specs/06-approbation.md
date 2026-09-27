# S06 — Compléter la revue humaine avant approbation

**Déjà livré sur `main`.** `POST /v1/cases/{id}/approvals` vérifie le draft courant, son digest, la révision, les trois gates d'analyse et le correspondant vérifié ; l'accord, l'acteur et l'heure persistent. Les amendements le révoquent. Le front désactive les transitions bloquées. G1 est volontairement bloqué faute de plaque/correspondant vérifiés ; le scénario historique `complete` permet de tester le chemin de bout en bout.

**Écart à fermer.** L'API ne demande pas encore une confirmation explicite du contenu revu, ne contrôle pas un rôle/une organisation au-delà de la propriété du dossier, et ne vérifie pas le devis/total de S04. La case de revue de la PR 13 n'a pas été raccordée à `ReviewPanel.tsx`. Les gates actuelles sont insuffisantes pour affirmer que G1 est prêt (S15).

**Contrat à ajouter.** La requête d'approbation porte `draft_id`, digest courant et confirmation explicite que récit, sources, pièces, devis/total et destinataire ont été examinés ; la confirmation seule ne remplace aucun contrôle serveur. Définir le droit `claims_handler:approve` dans le modèle d'auth/organisation puis vérifier son appartenance au dossier. Recontrôler en transaction S15, devis attaché et cohérent, IDs/checksums de pièces, source du destinataire et révision du package. Retourner des `reason_codes` précis en `422`; `409` sur version périmée. Une requête répétée pour la même version garde le même accord.

**Critères d'acceptation.** Sans devis, avec écart de total, gate bloquée, pièce étrangère, mauvais rôle ou vieille version : aucune approbation active. Après amendement, `superseded_at` est renseigné et le statut redevient approprié. Deux clics ne créent qu'une ligne. Un utilisateur non autorisé ne peut contourner le front en appelant l'API. Tests API avec deux acteurs, deux versions et un devis remplacé.

**Points d'appui.** `PostgresCaseRepository.approve_current_draft`, table `approvals`, `ReviewPanel.tsx`. L'acceptation finale G1 dépend de S15 ; conserver le chemin existant `complete` pendant la transition.
