# Suppression des dossiers

La liste « Tous les dossiers » propose une action « Supprimer » par ligne. La confirmation
identifie le dossier par sa référence et son identifiant court. L’annulation ne modifie
rien ; un échec conserve la ligne et permet de réessayer. Pendant la requête, les actions
de suppression sont désactivées. Après succès, la ligne et le compteur sont actualisés.

`DELETE /v1/cases/{case_id}` exige une session authentifiée. Seul le créateur du dossier
peut le supprimer. Une suppression réussie renvoie `204` ; un dossier absent ou appartenant
à un autre utilisateur renvoie `404`. Le verrouillage du dossier et la suppression de ses
données relationnelles sont effectués dans une seule transaction, en respectant les clés
étrangères. Cela comprend les pièces référencées, les brouillons, les validations,
les actions, les messages, les accès au dépôt, les sessions vocales et l’historique.

Les objets binaires dans Supabase Storage et les résultats d’analyse média partagés
ne sont pas purgés par cette opération. Les liens signés déjà émis restent valides
jusqu’à leur expiration, mais les références supprimées ne permettent plus d’en émettre
de nouveaux. Les envois déjà effectués auprès de services externes ne sont pas annulés.

L’aperçu `npm run demo` applique la même action à ses données en mémoire. Recharger cet
aperçu réinitialise les dossiers fictifs. Aucune migration SQL supplémentaire n’est requise.
