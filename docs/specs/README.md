# Specs de réalisation par tranche — parcours sinistre G1

État fonctionnel du prototype au 26 septembre 2026. Le parcours G1 est intégré à l’application principale. Source fonctionnelle : [diagramme cible](../sequence-diagram.md), [parcours jury](../jury-path.md), [contrats](../contracts.md) et [ERD](../erd.md).

**Fusionné :** S01–S16, avec S10 découpée en droit de dépôt, portail assuré et galerie G2/G3. S08 a traité un appel réel Twilio–Vapi–Gradium, conservé deux segments de transcription et stocké l'audio original privé ; la qualité du dialogue reste à améliorer. S09 persiste l'aperçu et le suivi SMS mais l'envoi applicatif reste simulé. S12 fournit des observations sourcées en mode mock opt-in, sans adaptateur Vision facturé. S13 (caméras) et S14 (lookup) sont également simulées. S16 a un harnais hors ligne ; la recette du parcours réel reste à faire.

**En cours :** S17, test de l'agent vocal depuis `/voice-test`. **Prochaines tranches candidates :** envoi SMS réel à partir de S09, adaptateur Vision facturé avec contrôle de réutilisation S11, puis recette bout en bout des parcours G1–G3 et appel vocal. Le remplacement des données caméra/lookup simulées demande un choix de source et un cadrage d'accès.

## Lecture de l'existant

Le tableau conserve la comparaison qui a servi à rédiger les premières specs ; l'avancement courant figure ci-dessus.

| Zone | Base de la comparaison initiale | Démo PR 13 | Écart initial |
| --- | --- | --- | --- |
| Dossiers | Auth Supabase, liste et détail API, scénario G1 et deux scénarios historiques, corrections d'intake, audit | Liste d'un seul dossier G1 local, accessible sans connexion | Champs assurée/véhicule/plaque structurés et recherche de la liste actuelle. |
| Pièces | Deux photos + G1 semés en Storage privé, SHA vérifiés, URLs signées ; upload gestionnaire images/PDF 5 Mio ; CCTV pixel-art mock | Médias publics, vidéo de remplacement locale, devis en mémoire | Upload vidéo utilisateur, rôles des pièces, devis associé et renouvellement des liens. |
| Analyse | Pipelex texte réel, `AnalysisInputV1`/`OutputV1`, gates 1–3, brouillon si elles passent ; cinq evals d'analyse | Compte rendu, dommages et coût 1 240 € écrits dans le front | Vision sourcée, réutilisation des vidéos, coût justifié et compte rendu composé des sources. |
| Recherche | Fixtures historiques injectées à la création ; base FR/UK de 2 000 plaques callable mais non branchée | Destinataire `Northbridge Demo Motor` et email `.example` codés en dur | Lookup après identification corroborée ; adapter le contrat du correspondant. |
| Revue / envoi | Brouillons immuables, approbation et actions simulées/idempotentes côté API ; reçus persistants | Amendement, approbation, aperçu et reçu simulé dans l'onglet | Devis/gates/role de revue, aperçu et reçu détaillé téléchargeable. |
| Amont | Pas d'appel vocal, service SMS ni dépôt assuré | Aucun de ces écrans | Intake d'appel, SMS, lien de dépôt et choix des médias. |

Limites à ne pas masquer : `complete` sur `main` est Lille, 14 juin 2025, `UK-SYN-482` ; G1 est Paris, 25 septembre 2026, Peugeot `FR-482-KL`, BMW `AB12 CDE` dans le corrigé humain mais sans extraction Vision branchée. G1 est actuellement **bloqué à la gate contrepartie**, même après lecture de sa vidéo. Le dataset `mock-insurance-v2` inclut les plaques de G1–G3, mais son contrat de correspondant (`insurer_id`, `accident_country`) diffère de celui que les gates actuelles lisent (`insurer`, `insurer_country`, `country`). Une simple substitution de données ne suffit pas. G1 dure environ 5,04 s ; les médias et corrigés G2/G3 sont versionnés par la PR 20, sans semis ni affichage dans l'application.

## Ordre d'attribution conseillé

Chaque fichier est une tâche autonome à transmettre à un agent. Les dépendances indiquent le premier moment où la tranche peut être intégrée ; le travail de préparation peut démarrer plus tôt. S01/S02/S05/S06/S07 décrivent **ce qui reste après PR 19 et PR 14**, sans réimplémenter les fondations déjà fusionnées. Un agent doit livrer code, migration éventuelle, tests ciblés et mise à jour des contrats touchés, sans modifier les autres tranches par anticipation.

| ID | Tranche et livrable | Dépend de |
| --- | --- | --- |
| [S01](01-dossier-g1.md) | Compléter identité assurée/véhicule et recherche dans la liste API | `main` actuel |
| [S02](02-pieces-g1.md) | Upload vidéo et métadonnées/lecture des médias déjà privés | `main` actuel |
| [S03](03-compte-rendu.md) | Compte rendu et provenance des faits | `main` actuel, enrichi ensuite par S12 |
| [S04](04-devis-et-cout.md) | Estimation et devis versionnés | `main` actuel (upload PDF déjà disponible) |
| [S05](05-amendements.md) | Unifier les amendements déjà versionnés avec récit/coût/devis | S03, S04 |
| [S06](06-approbation.md) | Compléter approbation API avec rôle, devis et revue explicite | S04, S05, S15 |
| [S07](07-transmission.md) | Aperçu et reçu détaillé des actions simulées déjà persistantes | S05, S06 |
| [S08](08-appel-et-intake.md) | Intake d'appel et triage P0 | S01 pour le raccord au dossier |
| [S09](09-sms.md) | Suivi SMS lié au dossier | S08, service de droit minimal S10 |
| [S10](10-depot-assure.md) | Droit de dépôt, puis page assuré et compléments | S02 ; droit minimal avant S09, portail après S09 |
| [S11](11-reutilisation-videos.md) | Empreintes vérifiées et réutilisation des analyses vidéo | `main` actuel (G1 déjà semé) |
| [S12](12-vision.md) | Observations sourcées sur médias | S11 ; S10 pour les dépôts assurés |
| [S13](13-cctv.md) | Recherche/demande caméra et réception simulée | S08, S12 |
| [S14](14-lookup.md) | Lookup assureur/correspondant après preuve | S12 |
| [S15](15-analyse.md) | Analyse responsabilité, gates et brouillon | S03, S04, S14 |
| [S16](16-evals-parcours.md) | Étendre les cinq evals d'analyse au parcours jury | S07–S15 |
| [S17](17-voice-test-web.md) | Tester le même agent vocal dans le navigateur, sans Twilio | S08 |

S08 utilise Twilio, Vapi et Gradium sur un vrai numéro ; l'envoi SMS reste simulé. S06 peut être développé contre les gates actuelles, mais sa validation G1 exige S15. Implémenter S11 avant tout appel Vision facturé sur vidéo. Vision, Camérci, Dust, SI et mail restent simulés tant qu'une intégration réelle n'est pas définie ; Pipelex texte est live.

Pour les prochains agents : intégrer l'envoi SMS réel dans le suivi S09, brancher les observations Vision sur des médias privés en réutilisant S11, puis exécuter la recette de production à partir du harnais S16. Tester le déclenchement de l'analyse, les gates et la revue G1–G3 dans l'interface gestionnaire, y compris l'accès aux médias déposés.

## Contrat commun pour tous les agents

- Toujours lire le dossier par `case_id` et vérifier l'accès côté API. `state_version` sert à détecter les éditions concurrentes ; `content_revision` change si le contenu matériel change. Un `409 stale_case` renvoie la version courante et force le rafraîchissement avant réessai.
- Une source a un identifiant persistant et une provenance (`caller_statement`, `evidence`, `provider_result`, `handler_edit` ou équivalent). Ne pas confondre déclaration, observation visuelle, résultat de lookup et hypothèse du modèle. Une plaque visible sur un autre véhicule ne peut identifier le tiers impliqué.
- Les pièces sont des objets privés ; le navigateur reçoit des URLs signées courtes, pas des chemins publics ni des secrets. Les annotations de référence des evals restent hors des entrées des agents évalués.
- Les erreurs, états `unavailable`/`ambiguous`/`unknown` et transitions bloquées sont affichés comme tels. Aucune valeur par défaut ne crée artificiellement un assureur, un montant, une responsabilité ou un envoi réussi.
- Toute modification matérielle après approbation la révoque. L'API recontrôle approbation, version, rôle et package au moment de l'enregistrement et de l'envoi. Le front n'est qu'une interface.
- Respecter le scénario synthétique et conserver `mode=mock|live` sur chaque résultat ou action. Aucun acteur extérieur ne doit être contacté par ces tranches sans cadrage explicite.
