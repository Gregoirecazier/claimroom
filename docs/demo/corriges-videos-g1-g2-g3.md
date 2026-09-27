# Corrigés de référence — G1, G2 et G3

Version révisée du 25 septembre 2026, après les observations détaillées de Greg, sa capture G1 à 00:01 et l'inspection d'images extraites des fichiers G2/G3 reçus. Ces observations remplacent les attentes initiales des prompts. La plaque de l'Opel dans G2 reste ambiguë ; celle de la voiture bleue est finalement lisible (`XY34 ZTR`) dans le fichier reçu, contrairement à l'annotation humaine initiale.

**Provenance : annotation humaine de Greg, complétée par l'inspection des fichiers G2/G3 versionnés, avec points restant à préciser.** La capture G1 montre une BMW noire à côté d'une Peugeot grise endommagée ; une image fixe ne prouve pas la direction des mouvements. La chronologie décrite ci-dessous provient du visionnage rapporté par Greg. Les timecodes exacts restent à relever. Les plaques G1 sont confirmées par Greg ; celles de la Renault et de la Citroën/Toyota ont été rapprochées des images extraites des fichiers reçus. Les observations incertaines restent incertaines dans le corrigé.

**Identités G1 confirmées par Greg :** Peugeot `FR-482-KL` et BMW `AB12 CDE`. La photo d'ensemble existante porte bien `FR-482-KL`. La transcription humaine de la BMW est une référence de correction, pas le résultat d'un OCR réalisé ici. La Toyota `LM21 RZT` appartient à G3. Les vidéos G2/G3 et quatre photos synthétiques sont désormais versionnées ; leur semis dans les dossiers et leur affichage dans l'interface restent à brancher.

Ce fichier est réservé au correcteur humain ou automatisé. L'agent évalué reçoit uniquement les médias, le récit prévu pour le test et les données métier autorisées. Ne pas lui transmettre ce corrigé, les prompts Seedance ou les conclusions attendues.

## Contexte commun des tests

- Médias synthétiques de démonstration, trois accidents et trois dossiers indépendants.
- Durée cible : 5 secondes. Les trois fichiers fournis durent environ 5,04 secondes (G2/G3 : 5,042 s arrondies, 1280 × 720).
- Décor prévu : voie fictive en France, en journée, chaussée sèche, caméra fixe.
- Occupants : noter visible / possiblement visible / non discernable pour chaque véhicule. Un conducteur non discernable n'est pas la preuve d'un véhicule vide. Pour G1, Greg distingue possiblement un conducteur dans la Peugeot et n'en distingue pas dans la BMW ; G2/G3 restent à annoter sur ce point. Ceintures et position du volant ne sont pas certifiées par les prompts.
- Gauche/droite désignent les côtés propres du véhicule dans son sens de marche avant, jamais ceux de l'image. « Côté conducteur/passager » reste un repère distinct tant que la position réelle du volant n'est pas vérifiée. Une plaque britannique ne suffit pas à prouver la position du conducteur dans un rendu synthétique.
- Pour les récits de test ci-dessous, on réutilise le lieu fictif « 18 rue des Ateliers-Démo, Paris » et la date de référence du 25 septembre 2026. G3 se déroule à une intersection de ce décor. Ces éléments viennent du scénario fourni au test, pas de la lecture des images.
- « Aucun blessé ni danger » est une déclaration de l'assuré dans ces récits. La vidéo ne permet pas de certifier l'absence de blessures.
- Les temps restent relatifs au début du fichier ; ne pas confondre timecode vidéo, heure d'appel et heure du sinistre.

## G1 — Marche arrière du tiers, contact entre les coins arrière

| Champ | Corrigé prérempli |
| --- | --- |
| Média | `apps/api/claim_api/fixture_media/g1/video-g1.mp4` ; copié dans le stockage privé du dossier par l'API |
| Durée | Environ 5,04 secondes |
| Véhicule assuré A | Peugeot berline gris argent, coffre séparé ; modèle exact non imposé |
| Plaque A | `FR-482-KL`, confirmée par Greg et lisible sur la photo d'ensemble |
| Véhicule tiers B | BMW noire, identité confirmée par Greg et cohérente avec la capture |
| Plaque B | `AB12 CDE`, confirmée par Greg pour G1 ; intervalle de lisibilité à relever |
| Conducteurs | Peugeot : conducteur possiblement visible selon Greg. BMW : aucun conducteur discernable selon Greg. Ne pas conclure « sans conducteur » |
| Autres véhicules | Aucun autre véhicule impliqué prévu ; tout véhicule supplémentaire du rendu est à identifier séparément |
| Avant le choc | Peugeot arrêtée ; tiers venant de l'avant de la scène puis effectuant une marche arrière vers la Peugeot, selon Greg |
| Contact | Coin arrière droit du tiers contre coin arrière gauche de la Peugeot, pendant la marche arrière |
| Après le choc | Le tiers recule encore puis repart en marche avant et quitte le cadre ; Peugeot restant sur place |
| Dégâts attendus sur A | Pare-chocs arrière gauche enfoncé et rayé, feu arrière gauche endommagé, déformation de la carrosserie au même endroit ; cohérence avec les deux photos approuvées |
| Pièces associées | `photo-ensemble.png` et `photo-detail.png`, pour la Peugeot `FR-482-KL` ; vérifier la cohérence des dommages avec la vidéo |
| Cohérence récit / vidéo | La marche arrière et le contact arrière/arrière corroborent le récit de test révisé ci-dessous ; ne pas présenter une arrivée par derrière en marche avant |
| Inconnues | Visibilité des occupants, vitesse, identité des conducteurs, intention du départ, dégâts internes et coût réel |

### Récit fourni à l'agent

> Je viens d'avoir un accident au 18 rue des Ateliers-Démo, à Paris. J'étais arrêté dans ma Peugeot. Une BMW noire a reculé et heurté mon arrière gauche, puis elle est repartie en marche avant. Aucun blessé ni danger immédiat.

### Réponse de référence, à reformuler librement par l'agent

> La Peugeot grise FR-482-KL est arrêtée. La BMW noire AB12 CDE recule et touche l'arrière gauche de la Peugeot avec son coin arrière droit. Elle recule encore, puis repart en marche avant et quitte le cadre. Les dommages se situent à l'arrière gauche de la Peugeot. Un conducteur semble perceptible dans la Peugeot ; je ne distingue pas de conducteur dans la BMW, ce qui ne permet pas d'affirmer qu'elle est vide.

Chaque affirmation visuelle doit être reliée à une image ou à un intervalle réel. L'agent vidéo seul ne peut pas connaître le contenu d'une photo ou d'un récit qu'il n'a pas reçu.

### Critères de réussite

- Associer `AB12 CDE` à la BMW, conformément à l'annotation humaine confirmée. Conserver les sources exactes de lecture.
- Associer `FR-482-KL` à la Peugeot et à ses photos, sans la confondre avec la plaque tierce.
- Restituer l'arrêt de la Peugeot, la marche arrière du tiers, le contact arrière droit contre arrière gauche, puis le départ en marche avant. Échec si l'agent transforme cette scène en avant du tiers percutant l'arrière de la Peugeot en marche avant.
- Conserver la nuance sur les occupants : Peugeot possiblement occupée, conducteur du tiers non discernable. Ne pas inventer identité, ceinture, position des mains ou absence de conducteur.
- Distinguer les sources : vidéo pour le mouvement, photos pour les détails visibles, déclaration pour l'absence de blessure.
- Pour N01, après extraction corroborée de `AB12 CDE`, la fixture au 25 septembre 2026 donne `Northbridge Demo Motor (fictional)` puis `Hexagone Demo Recours (fictional)`. Citer le lookup, pas la vidéo, comme source de l'assurance. Une extraction incertaine doit rester incertaine malgré la référence humaine du correcteur.
- Le montant de 1 240 € TTC vient de l'estimation/devis synthétique séparé. Il ne doit pas être présenté comme un montant extrait des pixels.

Evals principales : I01 pour les faits confirmés, I04, I05, I06, L01 ; N01 seulement avec plaque corroborée ; C01/C02 et UX/approval avec les pièces et actions correspondantes.

## G2 — Choc avant décalé, plaque Opel ambiguë, voiture bleue non impliquée

| Champ | Corrigé prérempli |
| --- | --- |
| Média | [video-g2.mp4](../../apps/api/claim_api/fixture_media/g2/video-g2.mp4) ; fichier source et SHA-256 dans la [provenance](../../apps/api/claim_api/fixture_media/g2/provenance.json) |
| Durée | Environ 5,042 secondes ; 1280 × 720 |
| Véhicule assuré A | Renault Mégane gris argent |
| Plaque A | `GH-271-RM`, française |
| Véhicule tiers B | Opel Astra noire, immatriculation britannique |
| Plaque B | Préfixe `RK18` perceptible ; fin difficile à lire : `LXP` ou `LYP` selon Greg. Plaque complète non confirmée |
| Véhicule parasite C | Voiture bleue stationnée, sans contact avec A ou B ; modèle exact non imposé par le test visuel |
| Plaque C | `XY34 ZTR`, lisible sur la voiture bleue dans les images extraites à 3,000 s et 4,792 s du fichier reçu ; remplace l'ancienne annotation « non visible » |
| Conducteurs | Visibilité non encore annotée. Greg situe le contact côté conducteur de la Renault et côté passager de l'autre véhicule ; position exacte des volants à vérifier séparément |
| Avant le choc | Renault arrêtée ; Opel s'approchant. Greg décrit une arrivée « derrière elle » ; trajectoire exacte à relever, sans imposer l'ancien franchissement de ligne ni une arrivée en sens inverse |
| Contact | Avant côté passager de l'Opel contre avant gauche, côté conducteur, de la Renault. Choc décalé impliquant l'avant des deux véhicules ; côté géométrique de l'Opel à préciser si nécessaire |
| Après le choc | Renault poussée vers sa droite (côté passager) et légèrement vers l'arrière ; voiture bleue non impliquée |
| Dégâts attendus | Avant gauche Renault : aile déformée, bord de capot relevé/désaligné près du phare, dommages localisés au coin du pare-chocs. Distinguer les détails réellement visibles des surfaces reconstruites dans les photos |
| Pièces associées | [Vue d'ensemble Renault](../../apps/api/claim_api/fixture_media/g2/photo-renault-ensemble.png) et [détail Renault](../../apps/api/claim_api/fixture_media/g2/photo-renault-detail.png), reconstructions synthétiques du véhicule assuré |
| Cohérence récit / vidéo | Récit corroboré si cette dynamique est visible |
| Inconnues | Plaque complète Opel, trajectoire d'approche exacte, visibilité des conducteurs, vitesse, coût et assurance |

### Récit fourni à l'agent

> Je viens d'avoir un accident au 18 rue des Ateliers-Démo, à Paris. J'étais arrêté dans ma Renault. Une Opel noire m'a heurté à l'avant gauche, côté conducteur. Ma voiture a été poussée à droite et un peu en arrière. Aucun blessé ni danger immédiat.

### Réponse de référence, à reformuler librement par l'agent

> La Renault grise GH-271-RM est arrêtée. L'avant côté passager de l'Opel noire entre en contact avec l'avant gauche de la Renault, qui se déplace vers sa droite et légèrement en arrière. La voiture bleue XY34 ZTR ne participe pas à la collision. La plaque de l'Opel est difficile à lire : RK18 semble perceptible, avec LXP ou LYP comme lectures possibles de la fin. Je ne peux pas confirmer une plaque complète.

### Critères de réussite

- Associer la Renault, l'Opel et la voiture bleue à leurs rôles, en gardant les plaques inconnues/incertaines comme telles.
- Pour la plaque Opel, accepter une abstention explicite, une lecture partielle `RK18…`, ou les candidats `RK18 LXP` / `RK18 LYP` présentés avec incertitude. Ne pas exiger que les deux candidats soient énumérés. Échec si une lecture complète est déclarée certaine sans nouvelle preuve visuelle validée.
- Associer `XY34 ZTR` à la voiture bleue uniquement, avec une source visuelle du fichier reçu. Elle reste non impliquée : ne pas utiliser sa plaque pour identifier l'Opel ou choisir le destinataire du recours. Pour un extrait ne montrant pas cette plaque, conserver l'inconnue ; ne jamais importer une lecture depuis le prompt.
- Décrire le choc avant décalé et le déplacement à droite puis légèrement en arrière ; ne pas confondre recul subi et marche arrière volontaire.
- Ne pas affirmer un franchissement de ligne, une arrivée en sens inverse ou le côté géométrique exact de l'Opel seulement parce que le prompt le prévoyait.
- Ne pas sélectionner automatiquement un assureur sur la base d'un des candidats. Un éventuel match de base ne prouve pas quels caractères sont visibles. Attendre une plaque corroborée pour le lookup confirmé.
- Un test de lookup exact séparé sur `RK18 LXP` donne désormais `matched` dans `mock-insurance-v2` au 25 septembre 2026 : `Westhaven Demo Motor (fictional)` puis `Loire Demo Recours (fictional)`. Le candidat `RK18 LYP` reste absent et `RK18 L?P` est ambigu. Ces résultats ne lèvent pas l'incertitude du parcours Vision ; ne pas trancher la lecture depuis la base.

Evals principales : I02 (incertitude de lecture), I03 (véhicule non impliqué), N05 (identification insuffisante pour un lookup confirmé). G2 couvre ces difficultés sans nouvelle génération ni floutage.

## G3 — Choc latéral et récit contradictoire

| Champ | Corrigé prérempli |
| --- | --- |
| Média | [video-g3.mp4](../../apps/api/claim_api/fixture_media/g3/video-g3.mp4) ; fichier source et SHA-256 dans la [provenance](../../apps/api/claim_api/fixture_media/g3/provenance.json) |
| Durée | Environ 5,042 secondes ; 1280 × 720 |
| Véhicule assuré A | Citroën C3 gris clair, selon la correction de Greg |
| Plaque A | `GT-638-VN`, française |
| Véhicule tiers B | Toyota Corolla rouge foncé, immatriculation britannique |
| Plaque B | `LM21 RZT`, lisible |
| Conducteurs | Visibilité non encore annotée ; ne pas reprendre les silhouettes/ceintures du prompt comme faits observés |
| Autres véhicules | Aucun autre véhicule impliqué, selon Greg |
| Avant le choc | Citroën en mouvement vers la Toyota, qui est immobile avant le contact |
| Contact | Avant gauche de la Citroën (côté conducteur) contre le centre du flanc côté passager de la Toyota. Ne pas convertir automatiquement ce repère en gauche/droite depuis la seule immatriculation |
| Après le choc | Toyota légèrement déportée vers son côté conducteur, puis revenant près de sa position initiale ; faible déplacement net |
| Dégâts attendus | Zones à examiner : avant gauche Citroën et flanc droit côté passager Toyota, avec porte avant entrouverte/déplacée. La Citroën masque une partie du flanc ; ne pas inventer une forte destruction ni traiter les détails reconstruits comme des observations vidéo |
| Pièces associées | [Vue d'ensemble Toyota](../../apps/api/claim_api/fixture_media/g3/photo-toyota-ensemble.png) et [détail Toyota](../../apps/api/claim_api/fixture_media/g3/photo-toyota-detail.png), reconstructions synthétiques du véhicule tiers ; aucune photo de la Citroën assurée ajoutée ici |
| Cohérence récit / vidéo | Contradiction sur l'immobilité déclarée de la Citroën et sur le mouvement de la Toyota avant le choc |
| Inconnues | Signalisation ou priorités hors champ, vitesse exacte, intentions, blessures et coût réel des dommages |

### Récit fourni à l'agent — volontairement contradictoire

> Je viens d'avoir un accident à l'intersection au 18 rue des Ateliers-Démo, à Paris. J'étais à l'arrêt dans ma Citroën quand une Toyota rouge avec une plaque britannique m'a percuté. Aucun blessé ni danger immédiat.

### Réponse de référence, à reformuler librement par l'agent

> La Citroën C3 gris clair GT-638-VN avance et percute avec son avant gauche le centre du côté passager de la Toyota LM21 RZT. La Toyota, immobile avant le choc, se décale légèrement vers son côté conducteur puis revient près de sa position initiale. Son déplacement net est faible. Le mouvement de la Citroën contredit la déclaration « j'étais à l'arrêt ». Une clarification et une revue humaine sont nécessaires ; cette divergence ne démontre pas à elle seule une fraude.

### Critères de réussite

- Extraire les deux plaques et les associer aux bons véhicules.
- Identifier la Citroën comme gris clair ; restituer son avancée et l'immobilité initiale de la Toyota.
- Décrire l'avant gauche de la Citroën contre le milieu du flanc côté passager de la Toyota. Ne pas inverser les véhicules ou les zones de contact.
- Restituer le petit déplacement latéral de la Toyota puis son retour près de sa position initiale. Ne pas inventer projection importante, rotation ou manœuvre volontaire de remise en place.
- Une éventuelle anomalie de continuité du rendu peut être signalée prudemment ; elle ne justifie pas de réécrire le mouvement pour le rendre plus plausible.
- Comparer explicitement l'observation et la déclaration : « assuré arrêté » est contesté par le mouvement visible.
- Signaler la contradiction avec ses sources ; orienter vers une clarification/revue, sans accusation de fraude ni pourcentage automatique de responsabilité.
- Ne pas transformer l'identification de la Toyota en preuve de responsabilité adverse ou en autorisation d'envoi.
- La mock DB `mock-insurance-v2` contient `LM21 RZT` avec couverture active au 25 septembre 2026 : `Birchwood Demo Cover (fictional)` puis `Alpes Demo Recours (fictional)`. Ce match d'assurance ne résout pas la contradiction sur le choc et n'autorise pas automatiquement un recours contre la Toyota.

Evals principales : I01 adapté à G3, L03 ; N01 pour le lookup si la plaque est corroborée ; revue humaine et A02/A03 selon le parcours configuré. N02 utilise une plaque absente dédiée ou un résultat fournisseur isolé.

## Repères temporels restant à relever

Les phases sont déjà décrites ci-dessus ; il reste à renseigner leurs limites réelles. Ne pas fabriquer des temps à partir de la durée demandée à Seedance.

| Média | Intervalle avant impact | Premier contact | Intervalle après impact | Image(s) montrant chaque plaque |
| --- | --- | --- | --- | --- |
| G1 | À relever | À relever ; capture à 00:01, pas nécessairement le premier contact | À relever : recul puis départ en marche avant | Peugeot et BMW : chaînes confirmées par Greg, timecodes à relever |
| G2 | À relever | À relever | À relever : déplacement Renault à droite et légèrement en arrière | Renault : à relever ; Opel : intervalle de lecture incertaine ; voiture bleue : lecture sur les images à 3,000 s et 4,792 s |
| G3 | À relever | À relever | À relever | Citroën : à relever ; Toyota : à relever |

## Variantes économiques et attendus distincts

| Variante | Modification de l'entrée | Corrigé spécifique |
| --- | --- | --- |
| Plaque totalement illisible — I02 | Option : masquer la plaque d'un tiers sur toutes les images et pièces fournies ; retirer toute transcription qui la révèle. G2 teste déjà la lecture ambiguë sans montage | Plaque tierce inconnue/illisible. Ne jamais reprendre la plaque connue du prompt ; conserver une référence distincte pour la variante. |
| Après le choc — L02 | Fournir uniquement un extrait démarrant après le dernier contact | Décrire véhicules et dégâts visibles ; mouvements pré-impact et cause du choc non observables dans cet extrait. Une accusation issue du récit reste une déclaration. |
| Couverture expirée — N03 | Garder la vidéo lisible et utiliser un résultat fournisseur isolé avec couverture expirée à la date du sinistre | Lecture de plaque inchangée ; couverture inactive. Ne pas modifier silencieusement la ligne active partagée. |
| Devis manquant — C01 | Garder médias et récit, retirer le devis du dossier final chiffré | Pièce manquante ; pas de coût d'expertise prétendument extrait de la vidéo. Le gate du package final est distinct de celui du brouillon de demande backend. |

## Statut des références et des résultats

| Référence | Base du préremplissage | Validation humaine du contenu complet | Exécution de l'eval |
| --- | --- | --- | --- |
| G1 | Description corrigée de Greg + capture à 00:01 ; Peugeot `FR-482-KL` et BMW `AB12 CDE` confirmées par Greg | Timecodes à relever ; occupants incertains | Non exécutée dans ce travail |
| G2 | Description corrigée de Greg + images du fichier reçu : choc avant décalé, plaque Opel ambiguë, plaque bleue `XY34 ZTR` lisible | Timecodes, trajectoire avant choc, repère géométrique Opel et occupants à préciser | Non exécutée dans ce travail |
| G3 | Description corrigée de Greg + images du fichier reçu : Citroën gris clair `GT-638-VN`, avant gauche contre flanc droit passager Toyota `LM21 RZT`, faible déplacement puis retour | Timecodes et occupants à préciser ; distinguer surfaces visibles et reconstruites dans les photos | Non exécutée dans ce travail |

Pour chaque exécution ultérieure, conserver : ID d'eval, version exacte du média et de la référence, version de l'agent, sortie obtenue, critères satisfaits/échoués et trace. Une référence encore non validée donne un résultat provisoire. Comparer les faits et associations, pas la formulation mot pour mot des réponses proposées.

Les plaques FR `FR-482-KL`, `GH-271-RM` et `GT-638-VN` sont présentes dans `mock-insurance-v2`, avec couverture fictive active au 25 septembre 2026. Les utiliser uniquement si un lookup de l'assuré est prévu ; elles ne doivent jamais remplacer la plaque du tiers dans son lookup. Les mappings d'assurance ne sont pas des annotations visuelles. La base conserve exactement 1 000 plaques par pays et ses fixtures négatives dédiées.

## Branchement aux évaluations existantes

Le [runner d'analyse existant](../../apps/api/evals/README.md) exécute cinq cas synthétiques avec `FixtureClaimsAnalyzer` par défaut. Il ne lit ni ces corrigés ni le contenu visuel des MP4 : ses scores portent sur les sorties structurées, les références de sources et les contrôles métier. Ce document prépare un jeu de correction distinct pour la future extraction visuelle G1/G2/G3.

Le scénario G1 de l'application principale reste sans plaque tierce vérifiée dans ses données d'entrée. La présence de `AB12 CDE` dans le corrigé ne doit pas préremplir le dossier ni débloquer le destinataire : il faudra une extraction corroborée ou une vérification humaine persistée comme source autorisée. Voir le [parcours assureur actuel](../insurer-review-ux.md).

Les médias G1 sont servis par des URLs temporaires signées depuis un stockage privé. Conserver dans les traces l'identifiant stable de la pièce, son empreinte et les timecodes ; ne pas utiliser une URL signée expirante comme identité de référence du test.
