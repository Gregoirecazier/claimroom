# Aperçu de l’espace assureur

Depuis `apps/web` :

```sh
npm ci
npm run demo
```

Ouvrir `http://127.0.0.1:5180/` (ou `npm run demo -- --port 5181` si le port est occupé).
Cet aperçu utilise trois dossiers fictifs G1/G2/G3 en mémoire. Il conserve les médias de chaque scénario, sans appeler l’API, les agents ou un assureur. Un rechargement réinitialise les modifications. La compilation de production utilise toujours l’authentification et l’API existantes.

## Parcours de revue

- Ouvrir un dossier par sa référence. Le résumé affiche la prochaine action, l’estimation, le destinataire, les pièces, la chronologie et la note transmise.
- Le bandeau des cinq étapes reste visible sous l’en-tête pendant le défilement. Les flèches en haut à gauche parcourent les vues visitées ; elles sont désactivées quand aucun retour ou aucune avance n’est disponible. Les étapes s’ouvrent sous le résumé avec un défilement doux, désactivé si le système demande de réduire les animations. Les cinq étapes ouvrent la déclaration, les pièces, l’analyse, la validation et l’envoi simulé. Les liens vers le devis, le compte rendu et les messages ouvrent la section correspondante. Les saisies non enregistrées restent disponibles pendant la navigation entre sections du même dossier.
- G1 permet de revoir le devis, amender la proposition et la note, puis valider la version, préparer la transmission et confirmer l’envoi simulé. Le reçu JSON conserve la proposition envoyée. G2/G3 restent incomplets.
- « Exporter le compte rendu » télécharge un Markdown des faits, sources, incertitudes, estimation et pièces actuels. Les liens privés des médias ne sont pas exportés.
- L’ajout de fichiers, le vocal, le suivi WhatsApp et la recherche de caméras nécessitent l’application connectée (`npm run dev` avec sa configuration). Les boutons dépendants de ces services affichent cette limite dans l’aperçu.

## Vérification

```sh
npm test
npm run build
```

Les tests couvrent notamment les liens profonds, la conservation des saisies entre sections, la prochaine action selon les contrôles, les confirmations humaines et l’invalidation des validations après modification.
