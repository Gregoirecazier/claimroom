# Chatbot de complément de déclaration

La page `/depot` commence par un récapitulatif des faits déjà enregistrés et demande à l'assuré de le confirmer ou de corriger une information. Après confirmation, l'assistant demande les informations manquantes une par une. Chaque nouvelle valeur doit être confirmée avant d'appeler `/v1/deposit/corrections`. Quand aucun champ requis ne manque, l'assistant conclut que le dossier est complet pour le moment et qu'un retour suivra dans les plus brefs délais. Le fil affiché utilise uniquement les rôles « Assistant » et « Vous » ; les anciens messages du gestionnaire ne sont plus affichés dans ce parcours.

Pipelex exécute `deposit_chat_v1.mthds` pour extraire au plus une proposition structurée à partir de la réponse de l'assuré. Pendant la collecte, l'API limite cette proposition au champ demandé ; le texte des questions et de la conclusion est fixé par l'interface. L'API vérifie la session avant d'envoyer le résumé au modèle et ne laisse jamais le modèle écrire directement dans le dossier. `OPENAI_API_KEY` est nécessaire, comme pour l'analyse Pipelex existante. Si l'appel au modèle échoue, l'assuré peut réessayer ou ouvrir le formulaire de secours qui apparaît alors.

Dust reste réservé aux spécialistes internes de revue du dossier. Ses agents et conversations actuels disposent d'un contexte de travail plus large que celui nécessaire à un assuré invité ; ils ne sont pas exposés par le lien de dépôt.

La conversation affichée est conservée dans l'onglet ouvert ; après rechargement, le chatbot recommence à partir du résumé courant. Les corrections déjà confirmées restent enregistrées dans le dossier.
