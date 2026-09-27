# S05–S07 — contrat du package et transmission simulée

## Version du package

Chaque analyse prête crée un brouillon immuable. Son `package_json` capture les lignes du compte rendu S03 (texte, incertitude, références de source), l'estimation S04 (postes, total, version), le devis (PDF, total déclaré, checksum) et les analyses vidéo liées. Le SHA-256 canonique couvre cette capture, le destinataire, le montant/devise, le message, le commentaire transmis et les IDs de pièces. Le commentaire transmis fait partie du message ; aucune note interne n'est introduite dans ce contrat. Les modifications de récit, de pièce, d'estimation, de devis ou de vidéo continuent à augmenter `content_revision`, supprimer le brouillon courant et révoquer l'approbation dans la même transaction. Le nouvel audit d'amendement conserve acteur, date, champs modifiés, filiation et digest sans copier le texte intégral. Les brouillons antérieurs à la migration ont `package_json = NULL` et doivent être reconstruits avant approbation.

Le formulaire affiche les champs changés avant sauvegarde. Après un `409`, il recharge la version courante et conserve les valeurs non enregistrées dans une zone de reprise explicite ; aucune réécriture automatique n'est faite.

## Approbation

`POST /v1/cases/{id}/approvals` exige `draft_id`, `draft_sha256`, `expected_state_version` et `confirmed_review=true`. L'acteur doit avoir une ligne `case_memberships` de la même organisation avec `role=claims_handler` et `can_approve=true` (`claims_handler:approve`). Le créateur d'un dossier reçoit cette appartenance ; la migration la crée pour les dossiers existants. Une seconde personne peut être autorisée par une entrée d'appartenance, sans changer le propriétaire du dossier.

Le serveur vérifie sous verrou : version et digest, snapshot du compte rendu, gates d'analyse, correspondance de la source du destinataire, devis PDF attaché au dossier, version du devis liée à l'estimation courante, total cohérent et manifeste des pièces avec checksums. Les vidéos jointes doivent avoir un checksum vérifié. Les réponses `422` donnent des `details.reason_codes` ; la version périmée reçoit `409`, le rôle insuffisant `403`. Le même accord pour le même brouillon est renvoyé de façon idempotente.

Le scénario G1 reste bloqué tant que S15 n'a pas produit les preuves et gates amont nécessaires. Le scénario `complete` reste utilisable pour le parcours simulé, à condition de joindre un devis cohérent.

## Aperçu et reçu

`GET /v1/cases/{id}/transmission/preview` construit depuis le brouillon courant la projection versionnée : mention `SIMULATION`, destinataire, corps et commentaire, montant, compte rendu, estimation, devis, manifeste des pièces avec checksums, digest, révision et éventuelle référence d'enregistrement simulée. Une version ou un digest périmé invalide l'aperçu.

Après approbation puis enregistrement simulé confirmé, `POST /v1/cases/{id}/send` conserve dans `actions.envelope_json` une copie immuable de cette projection avec les IDs d'approbation, d'enregistrement et d'envoi, la référence simulée et l'horodatage. `GET /v1/cases/{id}/transmission/receipt` la renvoie comme JSON téléchargeable avec `Cache-Control: private, no-store`. Le reçu ne dépend d'aucune URL média temporaire et ne contient pas les octets des pièces. Une action `unknown` bloque une nouvelle tentative jusqu'à réconciliation. Les clés d'idempotence et l'absence d'envoi externe restent inchangées.
