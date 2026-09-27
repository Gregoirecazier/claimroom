# Évaluations — agents et UX séparés

Les 42 cas sont répartis dans deux matrices indépendantes. Les IDs historiques et leur lien avec les corrigés sont conservés ; chaque cas appartient à une seule matrice. Il s’agit d’un plan de couverture, pas de 42 tests déjà automatisés ou réussis.

| Matrice | Question évaluée | IDs | Nombre |
| --- | --- | --- | --- |
| [Évaluation des agents](evaluation-agents.md) | L’agent comprend-il, extrait-il et raisonne-t-il correctement à partir des données reçues ? | V01–V06, S01–S03, I01–I06, N01–N06, L01–L03, C01–C02 | 26 |
| [Évaluation de l’UX et du parcours applicatif](evaluation-ux.md) | L’utilisateur peut-il consulter, corriger, valider et transmettre le bon dossier, avec les contrôles serveur attendus ? | S04–S05, U01–U07, A01–A07 | 16 |

## Frontière entre les deux matrices

- La qualité du SMS relève des agents (S01–S03) ; sa livraison et le rattachement du dépôt relèvent de l’application (S04–S05).
- La compréhension des dommages et la détection d’une contradiction relèvent des agents ; l’affichage des preuves et les actions humaines relèvent de l’UX.
- L’agent doit signaler un devis absent (C01) ; l’application doit appliquer le blocage du dossier final chiffré prévu (U05). Ce dernier est une cible produit distincte du brouillon actuel, où devis et montant sont facultatifs.
- Une synthèse exploitable pour revue relève de C02 ; le statut serveur `review_ready` et son affichage relèvent de U01. Une sortie d’agent ne peut pas suffire à autoriser l’envoi.

Conserver deux bilans : qualité des agents et bon fonctionnement de l’application. Les tests UX utilisent des données de référence figées pour ne pas dépendre de la variabilité des modèles. Les contrôles serveur sont explicitement indiqués dans la matrice UX ; les 16 cas ne sont pas tous des tests visuels.

## Médias et références

G1 et les deux photos sont intégrés dans l’interface. Les trois vidéos fournies durent environ 5,04 secondes. Les fichiers G2/G3 et quatre photos synthétiques sont versionnés dans les répertoires [G2](../../apps/api/claim_api/fixture_media/g2/README.md) et [G3](../../apps/api/claim_api/fixture_media/g3/README.md) ; leur intégration aux dossiers applicatifs reste à brancher. Les [corrigés G1–G3](corriges-videos-g1-g2-g3.md) reprennent désormais les observations détaillées de Greg, avec timecodes et points incertains à préciser. G2 présente une plaque Opel ambiguë ; l'inspection du fichier reçu montre finalement `XY34 ZTR` sur la voiture bleue non impliquée, ce qui remplace l'annotation initiale « non visible ». Les photos générées sont des reconstructions, pas des observations indépendantes de la vidéo. Une seule scène sert à plusieurs evals. G2 couvre déjà I02/I03/N05 sans nouvelle génération ; une copie entièrement floutée reste optionnelle. Le jeu de réponses de référence reste hors contexte des agents évalués.

## Exécution et résultats

Chaque matrice contient sa fiche de résultats et les commandes pertinentes. Pour les agents, conserver notamment versions de modèle/prompt, entrées, attendu, sortie réelle et sources. Pour l’UX, conserver version de l’application, dossier/rôle, actions, réponse API et état persistant. Utiliser les verdicts réussi / échoué / non testé / non applicable, avec des comptes séparés.

Le [runner d’analyse actuel](../../apps/api/evals/README.md) propose cinq cas simulés ; il n’exécute ni l’extraction visuelle G1/G2/G3 ni la matrice agents complète. Les tests API et le build web ne remplacent pas les parcours UX. Le [guide UX](../insurer-review-ux.md) décrit l’application authentifiée actuelle.
