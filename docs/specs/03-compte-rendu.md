# S03 — Compte rendu sourcé dans la revue assureur

**Objectif.** Enrichir le compte rendu actuel de `G1App.tsx`, qui affiche surtout le récit d'intake et des gates, avec un read model sourcé issu des pièces et de l'analyse. Le texte riche codé dans `reviewModel.ts` appartient seulement à la PR 13 autonome et sert de référence UX, pas de source de vérité.

**Contrat.** Pour chaque ligne affichée : `text`, `claim_kind` (`declaration`, `observation`, `provider_result`, `hypothesis`, `handler_edit`), `source_refs[]`, `uncertainty`, `as_of_revision`. Utiliser les `AnalysisSourceRef` existants lorsque possible ; créer un champ/objet de projection si la rédaction du compte rendu ne peut être déduite sans ambiguïté des propositions. Les références pointent sur le dossier courant et sur des locators réellement renseignés. Les corrections humaines sont signées et conservent la valeur antérieure dans l'audit.

**Rendu.** Afficher lieu, heure et sa provenance (`time_source=inferred_from_call` si déduite), assuré, véhicules, récit, dégâts observés, éléments à confirmer et contradictions. La plaque `FR-482-KL` appartient à la Peugeot ; la plaque BMW reste `inconnue` tant qu'une preuve exploitable ne l'établit. Un résultat de lookup est labellisé mock, séparé d'une observation de la vidéo. Cliquer sur une source ouvre la bonne pièce ou le bon extrait/locator. Si une source a disparu ou si l'analyse est obsolète, montrer l'état obsolète et interdire de présenter la proposition comme actuelle.

**Critères d'acceptation.** La même fiche, rafraîchie, reproduit le même compte rendu à révision égale. Une modification du récit crée une nouvelle `content_revision`, marque l'analyse précédente obsolète et indique ce qui doit être relancé. Dans un cas ambigu ou une vidéo illisible, l'interface expose la lacune ; aucun texte ne complète automatiquement plaque, choc ou responsabilité. Tests API de validation des refs et test UI des quatre types de sources.

**Points d'appui.** `AnalysisInputV1/OutputV1`, `analysis.py`, `CaseView`, `G1App.tsx`. Cette tranche affiche les résultats ; l'extraction Vision est S12.
