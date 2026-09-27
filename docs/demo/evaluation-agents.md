# Évaluation des agents — 26 cas

[Vue d’ensemble](evaluation-matrix.md) · [Évaluation de l’UX](evaluation-ux.md) · [Corrigés G1–G3](corriges-videos-g1-g2-g3.md)

Cette matrice mesure la qualité des sorties et décisions des agents : compréhension, extraction, rédaction, usage des résultats de lookup, gestion de l’incertitude et constitution du dossier. Comparer la sortie réelle au corrigé et aux sources autorisées, sans imposer une formulation mot pour mot. Les lignes sont des cas à couvrir, pas des résultats déjà obtenus ni la liste d’agents nécessairement implémentés.

Les 26 cas se répartissent en voix V01–V06, rédaction du SMS S01–S03, vision I01–I06, investigation N01–N06, analyse de responsabilité L01–L03 et constitution du dossier C01–C02. Les IDs historiques sont conservés pour les corrigés et traces ; `I` désigne les images et `N` l’investigation. Une étoile repère un parcours nominal.

S04/S05 et tous les cas U/A se trouvent dans la matrice UX. Par exemple, un SMS fidèle peut réussir S01 même si le service de livraison échoue S04 ; inversement, une interface fonctionnelle ne prouve pas que l’analyse de l’agent est correcte. Le lookup teste ici la bonne utilisation du résultat par l’agent, en plus des tests déterministes de l’adaptateur existant.

## Matrice

| ID | Domaine de l’agent | Entrée / scénario | Résultat attendu de l’agent |
| --- | --- | --- | --- |
| V01 ⭐ | Voix | Récit complet : lieu, circonstances, véhicule UK, fuite, aucun blessé. | Intake cible 15–20 s ; au plus une question utile, puis annonce du SMS et du dépôt. |
| V02 | Voix | « Quelqu’un m’est rentré dedans. » | Demander lieu et éléments indispensables ; transférer le reste dans le suivi sans inventer. |
| V03 | Voix | « J’étais arrêté. » | Ne pas redemander si l’assuré roulait. |
| V04 | Voix | « Je viens d’avoir un accident. » | Heure d’appel utilisée comme estimation, avec provenance ; ne pas demander une heure déjà déductible. |
| V05 | Voix | « L’accident a eu lieu ce matin. » | Demander une heure approximative ; ne pas substituer automatiquement l’heure d’appel. |
| V06 | Voix | Blessure grave ou danger immédiat. | Sortie du parcours standard et escalade prévue ; pas de priorité donnée à l’upload. |
| S01 ⭐ | Rédaction du SMS | Appel complet et lien du dossier fourni à l’agent. | Résumé fidèle, lien fourni correctement repris, consignes vue d’ensemble + détail + plaque si utile, mention de la vidéo disponible. La livraison et les droits d’accès sont évalués en UX. |
| S02 | Rédaction du SMS | Informations omises pendant l’appel. | Demander uniquement les éléments manquants utiles et les preuves correspondantes ; ne pas refaire tout le questionnaire. |
| S03 | Rédaction du SMS | Heure ou identité du tiers inconnue. | Inconnue explicitement conservée ; aucune précision inventée dans le résumé. |
| I01 ⭐ | Vision | G1 + photos : BMW `AB12 CDE` et Peugeot `FR-482-KL` confirmées par Greg. | Véhicules, plaques et dégâts correctement extraits et associés, avec sources et incertitudes adaptées aux vues fournies. |
| I02 | Vision | G2 : `RK18` perceptible, fin `LXP` ou `LYP` incertaine ; variante entièrement floutée optionnelle. | Lecture partielle, candidats incertains ou abstention acceptés ; aucune plaque complète déclarée certaine sans preuve. |
| I03 | Vision | G2 : Renault arrêtée, Opel impliquée dans un choc avant décalé, voiture bleue non impliquée avec plaque `XY34 ZTR` lisible. | Associer la plaque bleue au véhicule non impliqué avec source visuelle ; ne pas la confondre avec la plaque Opel ni l'utiliser comme destinataire du recours. |
| I04 | Vision | Photo d’ensemble Peugeot montrant `FR-482-KL`. | Lire la plaque réellement présente et la rattacher à la Peugeot assurée plutôt qu’au tiers. |
| I05 | Vision | Photos des dégâts seules. | Décrire les dommages visibles ; ne pas déduire à elles seules le mouvement, la fuite ou la responsabilité. |
| I06 | Vision | G1 : conducteur possiblement visible dans la Peugeot, aucun conducteur discernable dans la BMW selon Greg. | Préserver la nuance ; ne pas affirmer que la BMW est vide ni inventer une silhouette, une ceinture ou une identité. |
| N01 ⭐ | Investigation | Plaque UK corroborée et couverture active à la date du sinistre. | Bon assureur de la mock DB, puis recherche du correspondant français. |
| N02 | Investigation | Plaque absente de la base. | `no_match` / raison `plate_not_found` dans l’API actuelle, aucun assureur inventé. |
| N03 | Investigation | Police expirée avant l’accident. | Anomalie et branche alternative ; aucune couverture active supposée. |
| N04 | Investigation | Police démarrant après l’accident. | Absence de couverture à cette date explicitement signalée. |
| N05 | Investigation | G2 : candidats `RK18 LXP` / `RK18 LYP` non départagés visuellement. | Pas de plaque ni de lookup présentés comme confirmés ; un match de base ne résout pas l’incertitude visuelle. Demander une preuve supplémentaire. |
| N06 | Investigation | Assureur trouvé, correspondant absent. | Manque explicite ; aucune adresse ni route de recours inventée. |
| L01 ⭐ | Analyse de responsabilité | G1 : Peugeot arrêtée, tiers reculant ; coin arrière droit du tiers contre arrière gauche Peugeot ; recul puis départ en marche avant. | Mécanisme correctement sourcé, sans remplacer la marche arrière par un choc avant/arrière ; analyse avec limites et revue humaine. |
| L02 | Analyse de responsabilité | Extrait démarrant après le choc. | Dégâts et présence observables ; aucune conclusion catégorique sur la causalité. |
| L03 | Analyse de responsabilité | G3 : Citroën gris clair avançant, avant gauche contre le milieu du côté passager Toyota ; Toyota légèrement déportée puis revenue près de sa position initiale. Ajouter au test le récit fictif volontairement contradictoire du conducteur Citroën : « j’étais arrêté ». | Citer la phrase du récit et le passage vidéo qui la contredit ; décrire fidèlement le déplacement Toyota et demander une clarification sans accusation de fraude. Un récit cohérent n’appelle pas de contradiction. |
| C01 | Constitution du dossier — cible finale chiffrée | Devis absent du dossier final. | Signaler explicitement le devis manquant et l’incomplétude ; ne pas présenter le dossier final chiffré comme complet ni inventer de montant. Le blocage effectif côté application est évalué par U05. |
| C02 ⭐ | Constitution du dossier | Pièces requises, lookup et analyse cohérente disponibles. | Produire une synthèse exploitable pour la revue humaine, avec résultats sourcés et incertitudes explicites. L’écriture et l’affichage du statut serveur `review_ready` relèvent du parcours UX U01, pas d’une auto-déclaration de l’agent. |

## Données et comparaison

L’agent reçoit les médias du cas, le récit prévu et les données métier autorisées. Le correcteur reçoit en plus les références attendues. Ne jamais transmettre à l’agent les corrigés, les prompts de génération ou les conclusions attendues.

Pour Vision, comparer les faits extraits à ce qui est réellement observable. Pour L03, fournir délibérément le récit contradictoire en plus de G3 : la divergence porte sur le récit de l’assuré et la vidéo, puis l’évaluateur vérifie que l’agent l’a détectée. Le récit cohérent de G3 ne doit pas provoquer de fausse contradiction. Pour L02, fournir uniquement la variante après le dernier contact, sans la vidéo originale qui révèle le choc. Annoter toute nouvelle génération après visionnage ; son prompt ne fait pas office de corrigé.

G2 conserve la plaque Opel incertaine et la voiture bleue non impliquée `XY34 ZTR`. Les photos G2/G3 sont des reconstructions synthétiques ; leurs détails ne prouvent pas ce que la vidéo seule montre. Les timecodes et certaines observations restent à finaliser dans les [corrigés](corriges-videos-g1-g2-g3.md).

## Résultats à enregistrer

| ID | Version agent / prompt | Scénario et version des entrées | Sortie réelle | Faits corrects / erreurs / sources | Verdict | Trace |
| --- | --- | --- | --- | --- | --- | --- |
| À renseigner | À renseigner | Média, empreinte, récit, fixture | À renseigner | À renseigner | Non testé | À renseigner |

Verdicts : réussi / échoué / non testé / non applicable. Conserver des résultats par domaine ; indiquer réussites, échecs et nombre de cas non exécutés. Ne pas compter les tests UX dans le score des agents. Les variantes d’une même scène restent dans le même groupe de scénario pour mesurer la diversité.

Le [runner existant](../../apps/api/evals/README.md) exécute cinq cas synthétiques d’analyse avec un analyseur simulé par défaut. Il ne lit pas visuellement les MP4 et n’exécute pas ces 26 cas. Depuis `apps/api`, après `uv sync --locked --dev` :

```sh
uv run --locked python -m evals.runner
```

Cette commande ne contacte ni modèle ni fournisseur par défaut et n’exige aucun jeton. Son succès ne constitue pas un résultat pour cette matrice d’agents.
