# Médias du scénario G3

| Fichier | Contenu |
| --- | --- |
| [video-g3.mp4](video-g3.mp4) | Vidéo synthétique fournie par Greg, copiée sans transcodage depuis `98ea44f9-0ac7-46b8-8a08-79cf32cbde1a.mp4` ; environ 5,042 s, 1280 × 720. |
| [photo-toyota-ensemble.png](photo-toyota-ensemble.png) | Vue d'ensemble de la Toyota rouge foncé `LM21 RZT`, porte avant droite côté passager endommagée et entrouverte. |
| [photo-toyota-detail.png](photo-toyota-detail.png) | Gros plan de la même porte côté passager : plis, désalignement et traces claires. |

**Ces photos concernent le véhicule tiers Toyota, pas la Citroën assurée `GT-638-VN`.** La vidéo montre la Citroën percutant le côté passager de la Toyota. La présence de photos de la Toyota ou d'une couverture d'assurance ne prouve pas sa responsabilité et n'autorise pas un recours automatique contre elle.

Les deux photos retenues sont des reconstructions synthétiques à partir de la vidéo, générées avec `image_gen` et approuvées par Greg. Le côté endommagé est le côté droit propre de la Toyota ; il s'étend vers la gauche de la composition, le nez de la voiture étant à droite. Les voitures sont représentées séparées pour montrer les dommages. La Citroën masque une partie du flanc dans la vidéo : les surfaces ainsi dégagées sont reconstruites, pas nouvellement observées. Le premier essai montrant le mauvais côté a été écarté et n'est pas inclus.

Les empreintes SHA-256, dimensions, rôles et sources sont dans [provenance.json](provenance.json). Les prompts finaux sont conservés dans [prompt-ensemble.md](prompt-ensemble.md) et [prompt-detail.md](prompt-detail.md), uniquement pour la traçabilité. Les [corrigés](../../../../../docs/demo/corriges-videos-g1-g2-g3.md) restent réservés à l'évaluateur.

**Intégration : fichiers disponibles dans le dépôt, pas encore semés automatiquement dans les dossiers.** L'API continue de semer uniquement G1. Le futur branchement G3 doit conserver le rôle de chaque véhicule, copier ces pièces dans le stockage privé du bon dossier et les servir par URLs signées ; ne pas les placer dans le répertoire public Vite. Ne pas transmettre les prompts, corrigés ou conclusions attendues à l'agent évalué.
