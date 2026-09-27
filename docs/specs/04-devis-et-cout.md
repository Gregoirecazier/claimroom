# S04 — Estimation et devis du dossier

**Objectif.** Rendre persistants les postes de réparation et le devis du dossier G1 ; garder leur origine explicite. Les 1 240 € de la PR 13 sont une hypothèse de démonstration, pas un calcul Vision ni un prix de garage.

**Contrat.** Ajouter à un brouillon ou à une entité versionnée : postes `{id,label,amount_minor}`, `currency=EUR`, `tax_basis=TTC`, `estimate_source` (`demo_fixture`, `handler`, `quote`, éventuellement `agent_proposal`) et `source_refs`. Le total est calculé en centimes côté API ; 0 par poste possible, total strictement positif. Un devis est une pièce PDF privée avec `evidence_id`, nom, MIME, checksum, total TTC déclaré et `amount_source=handler_entered` tant qu'aucun OCR vérifié n'existe. Un devis fictif généré doit être marqué `demo_fixture` et contenir les postes de la version exacte qu'il accompagne.

**Flux.** Réutiliser l'upload signé PDF existant, puis finaliser et associer la pièce au dossier ; ne jamais fonder l'association sur un nom de fichier ou un blob local. Joindre/remplacer/retirer un devis change `content_revision` et révoque l'approbation. Si l'estimation change, garder le devis attaché mais marquer l'écart ; exiger une nouvelle pièce ou un ajustement explicite. Un montant PDF saisi manuellement n'est pas présenté comme lu dans le document.

**Critères d'acceptation.** Postes `480+180+260+240+80` donnent 124 000 centimes ; négatif, NaN, trop de décimales, autre devise ou total incohérent sont rejetés côté API. Après rechargement, postes, devis et URL de lecture autorisée sont retrouvés. Une différence entre total estimé et total du devis bloque l'approbation avec les deux valeurs visibles. L'export ne contient que la pièce associée à la version. Tests unitaires monétaires, API d'association et UI d'écart.

**Points d'appui.** `DraftView` et `drafts.attachment_ids`, `EvidenceView`, upload PDF existant, modèle de coût PR 13. Une migration est probablement requise pour les postes et métadonnées du devis.
