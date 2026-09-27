# S12 — Observations Vision sourcées

**Objectif.** Analyser les photos et les vidéos du dossier pour produire des observations visuelles auditables, sans conclure à la responsabilité. Le chemin Vision n'est pas présent dans `main` ; les observations CCTV actuelles sont une fixture pixel-art.

**Contrat.** Un `EvidenceAnalyzer` prend les `evidence_id` finalisés et leurs URLs serveur temporaires ; il retourne pour chaque observation `media_id`, `frame/timecode` ou zone image, texte factuel, `vehicle_track_id` local au média, plaque candidate avec caractères incertains, confiance/calibration, et `status=observed|uncertain|not_visible`. Le modèle ne reçoit pas les annotations de référence. Le traitement vidéo passe par S11 : résultat intrinsèque réutilisable, puis liaison au dossier. Les sorties sont validées (IDs existants, timecodes dans la durée, aucune citation à un média étranger) et persistées comme `provider_result`, `mode=mock|live`, version de pipeline, source UUID et statut. Les erreurs restent `error/unavailable`, pas une liste vide qui semblerait concluante.

**Sémantique.** Comparer explicitement plaque et véhicule : `FR-482-KL` des photos est celle de l'assurée ; une plaque nette sur une voiture bleue non impliquée ne devient pas celle de la BMW. Photos de dégâts seules : dommages visibles, pas mouvement, fuite ou causalité. Une plaque floue garde les caractères douteux ; aucun O/0 corrigé sans preuve. Si la vidéo démarre après l'impact, ne pas inventer le choc. Contradiction récit/vidéo est une sortie distincte à revoir.

**Critères d'acceptation.** G1 + deux photos produisent observations liées aux bons IDs/timecodes ; G2 n'attribue pas `XY34 ZTR` au tiers ; G3 expose le mouvement contraire au récit sans qualifier fraude ; flou, faible lumière et extrait après choc gardent l'incertitude. Une vidéo inaccessible donne une erreur visible et n'ouvre pas de gate de preuve. Tests déterministes sur fixtures puis evals I01–I05 et L01–L03 sur médias annotés. Ne pas déclarer ces evals réussies tant que les médias/annotations ne sont pas prêts.

**Points d'appui.** `mock_vision` de `cctv_fixture.py`, `AnalysisSourceRef`, matrice PR 13. L'adaptateur live éventuel doit respecter le même schéma que le mock.

**Implémentation.** Le chemin API/UI opt-in, la validation et la persistance sont décrits dans [vision-s12-implementation.md](../vision-s12-implementation.md). Le fournisseur Vision réel et les évaluations sur médias annotés restent à brancher.
