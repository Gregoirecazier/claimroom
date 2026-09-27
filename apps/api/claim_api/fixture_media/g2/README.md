# Médias du scénario G2

| Fichier | Contenu |
| --- | --- |
| [video-g2.mp4](video-g2.mp4) | Vidéo synthétique fournie par Greg, copiée sans transcodage depuis `cea24e58-62ab-4fb7-8cd0-e1f32f50bf92.mp4` ; environ 5,042 s, 1280 × 720. |
| [photo-renault-ensemble.png](photo-renault-ensemble.png) | Vue d'ensemble de la Renault grise assurée `GH-271-RM`, dégâts à l'avant gauche. |
| [photo-renault-detail.png](photo-renault-detail.png) | Gros plan du même coin avant gauche : aile déformée, capot désaligné, phare déplacé et pare-chocs rayé. |

Les deux photos retenues sont des reconstructions synthétiques à partir de la vidéo, générées avec `image_gen` et approuvées par Greg. Elles changent le point de vue après séparation des voitures et ne constituent pas des observations indépendantes. Les dommages et surfaces partiellement masqués dans la vidéo sont reconstruits ; ces photos ne doivent pas servir à prouver ce que la vidéo seule montre.

Le fichier reçu montre la plaque `XY34 ZTR` sur la voiture bleue non impliquée, notamment aux images à 3,000 s et 4,792 s. Cette observation remplace l'ancienne annotation « plaque bleue non visible ». La plaque Opel reste incertaine (`RK18 LXP` / `RK18 LYP`) ; la base fictive ne tranche pas sa lecture.

Les empreintes SHA-256, dimensions, rôles et sources sont dans [provenance.json](provenance.json). Les prompts finaux sont conservés dans [prompt-ensemble.md](prompt-ensemble.md) et [prompt-detail.md](prompt-detail.md), uniquement pour la traçabilité. Les [corrigés](../../../../../docs/demo/corriges-videos-g1-g2-g3.md) restent réservés à l'évaluateur.

**Intégration : fichiers disponibles dans le dépôt, pas encore semés automatiquement dans les dossiers.** L'API continue de semer uniquement G1. Le futur branchement G2 doit copier ces pièces dans le stockage privé du bon dossier et les servir par URLs signées ; ne pas les placer dans le répertoire public Vite. Ne pas transmettre les prompts, corrigés ou conclusions attendues à l'agent évalué.
