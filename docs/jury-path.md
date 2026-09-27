# Parcours jury : appel, SMS et preuves

Statut : parcours cible à implémenter. Le [diagramme de séquence](sequence-diagram.md) décrit les interactions. La [première démo technique](implementation-plan.md) utilise des fournisseurs simulés : les étapes d’appel et de SMS ci-dessous y sont représentées par un intake et un aperçu du message, et l’envoi final reste simulé. Cette documentation ne signifie pas que ces fonctionnalités ou les dix vidéos sont déjà disponibles.

## Parcours cible

1. Ouvrir la page de démonstration depuis le lien ou le QR code. Lire le contexte fictif proposé pour l’appel.
2. Appeler le numéro de démonstration et raconter l’accident. L’agent ouvre par « Bonjour, comment puis-je vous aider ? », extrait les informations déjà dites et demande uniquement les P0 manquants. La cible de 15–20 secondes concerne le happy path, sans sacrifier un P0 nécessaire.
3. Recevoir le SMS de suivi : résumé de la déclaration, informations encore manquantes, trois consignes photo au maximum et lien sécurisé propre au dossier.
4. Ouvrir le lien, vérifier le résumé et corriger une information si nécessaire. Dans la première démo, retrouver ce lien depuis l’aperçu SMS dans l’espace authentifié ; un lien seul ne remplace pas l’authentification existante.
5. Choisir une vidéo parmi environ dix vidéos synthétiques préparées. Chaque carte indique le scénario et un aperçu ; les jurés n’ont pas à trouver eux-mêmes une vidéo pertinente. Préférer un scénario cohérent avec le récit ; tout écart reste visible comme contradiction.
6. Ajouter, au besoin, les photos synthétiques fournies et les informations manquantes. Soumettre les preuves ; aucune photo personnelle n’est nécessaire pour tester la démo.
7. Suivre l’investigation et les pièces manquantes. Si une CCTV complémentaire est nécessaire, sa réception préchargée est explicitement simulée.
8. Passer au rôle de gestionnaire : consulter le résumé, les preuves, la plaque candidate, les résultats assureur / correspondant, l’hypothèse de responsabilité, les sources, les incertitudes et les contradictions.
9. Amender le dossier ou demander une investigation complémentaire. Un cas ambigu peut rester bloqué ; il ne doit pas produire une identification inventée pour finir la démo.
10. Approuver la version exacte du dossier puis déclencher l’envoi. Dans la première démo, montrer le reçu simulé et la piste d’audit. L’envoi réel vers une boîte de test appartient au workflow cible ; toute modification matérielle impose une nouvelle approbation.

## Contenu du SMS

Le message reprend uniquement les faits déclarés pendant l’appel, présentés comme tels. Il demande les informations encore manquantes et les médias utiles pour les extraire. Une heure déduite de l’appel est indiquée comme approximative ; une couleur ou une plaque non mentionnée n’est pas ajoutée au résumé.

Exemple pour un récit où l’assuré dit avoir été heurté à République alors qu’il était arrêté, par une BMW à plaque britannique dont le conducteur est parti, sans blessure grave signalée :

> Dossier CLM-8421 ouvert. Selon votre déclaration : accident à République, votre véhicule à l’arrêt, BMW à plaque britannique partie après le choc, sans blessure grave signalée.
>
> Pour compléter le dossier :
> 1. Une photo large de votre voiture, de trois quarts, pour situer les dégâts.
> 2. Un gros plan de la zone endommagée.
> 3. Toute photo ou vidéo disponible montrant l’autre véhicule ou sa plaque.
>
> Nous y rechercherons la plaque, le modèle / la couleur, les dégâts et les positions ou trajectoires visibles encore à préciser. Corrigez le résumé et ajoutez vos pièces ici : {lien_securise_du_dossier}

Ces trois consignes sont adaptées aux pièces déjà reçues : ne pas redemander une vue déjà exploitable. Si le tiers est présent et sa plaque manque, préciser qu’une vue nette de sa plaque est utile. S’il a fui, demander seulement les médias disponibles. Les détails longs peuvent figurer sur la page de dépôt, avec un SMS raccourci conservant le résumé, les demandes essentielles et le lien ; prévoir la segmentation du message lors de l’intégration Twilio.

| Média demandé | Informations P1 à extraire si visibles |
| --- | --- |
| Vue large de trois quarts | Véhicule assuré, localisation des dégâts, contexte visuel. |
| Gros plan | Nature et étendue visibles des dégâts ; aucune cause ou estimation de coût déduite sans preuve. |
| Photo / vidéo du tiers | Plaque candidate, pays, modèle / couleur, positions, trajectoires et contexte routier. |

Toute extraction conserve sa source et son incertitude. Une information non visible reste manquante. En cas d’échec SMS, montrer le statut et donner accès au suivi dans l’interface ; ne pas afficher « livré » sur la seule acceptation de la demande d’envoi.

## Banque d’environ dix vidéos à préparer

Cinq cas simples et cinq cas limites, tous synthétiques ou créés pour la démonstration. Les vidéos ne sont pas encore fournies par ce document.

| ID | Scénario | Résultat attendu |
| --- | --- | --- |
| V01 | Choc sur véhicule arrêté, tiers UK en fuite, plaque nette | Parcours de référence, avec responsabilité formulée comme hypothèse sourcée. |
| V02 | Choc clairement visible, plaque UK lisible | Relier le tiers impliqué à sa plaque. |
| V03 | Plusieurs véhicules mais tiers clairement identifiable | Sélectionner le véhicule impliqué. |
| V04 | Vidéo après le choc, plaque nette et photos des dégâts | Identifier le tiers ; ne pas inventer la dynamique absente. |
| V05 | Fuite visible et seconde vue corroborant la plaque | Croiser les sources compatibles. |
| V06 | Plaque partielle ou floue | Conserver les caractères incertains et demander un complément. |
| V07 | Plaque nette d’un véhicule non impliqué | Ne pas l’attribuer au tiers. |
| V08 | Trajectoire contraire au récit | Exposer la contradiction à la revue. |
| V09 | Faible éclairage | Réduire la confiance et signaler les limites. |
| V10 | Aucun élément suffisant pour identifier le tiers | Maintenir l’investigation ouverte sans assureur inventé. |

Pour chaque entrée, préparer : identifiant stable, titre, miniature, fichier vidéo, provenance synthétique, contexte narratif et éventuelles photos associées. Garder les résultats attendus dans les fixtures d’évaluation, séparés des entrées remises à l’analyse. Le média choisi est rattaché au dossier avec sa provenance ; il ne remplace pas silencieusement le récit vocal.

## Vérification du parcours lors de l’implémentation

- Un récit complet déclenche le suivi sans question P1 redondante ; le SMS ne fabrique aucun détail.
- Le lien ouvre le bon dossier avec les contrôles d’accès prévus ; le juré peut corriger le résumé et choisir un média sans fournir de vidéo personnelle.
- La vidéo et les photos sélectionnées apparaissent dans les preuves avec leur provenance.
- Le cas V01 permet la revue ; V06–V10 conservent les incertitudes et les blocages pertinents.
- Aucun envoi de recours n’est possible sans approbation valide ; les actions simulées sont identifiées comme telles.
