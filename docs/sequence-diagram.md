# Workflow de claim / subrogation automobile transfrontalier

Architecture proposée pour le POC du hackathon, issue de la conversation « Expliquer Pipelex et proposer des projets ». Ce document décrit le workflow cible ; il ne décrit pas des intégrations déjà implémentées.

Pour l'implémentation du hackathon, voir [l'architecture](architecture.md) et les [contrats](contracts.md). Le diagramme ci-dessous attribue conceptuellement l'orchestration à Pipelex ; dans le premier POC, FastAPI conserve l'état du dossier et les validations humaines, tandis que Pipelex exécute une analyse bornée.

## Scénario

Un assuré français appelle après un accident en France impliquant un véhicule britannique dont le conducteur a pris la fuite. L'agent recueille son récit et les pièces, recherche des preuves complémentaires, prépare le dossier de sinistre et le recours, puis les soumet au gestionnaire de l'assureur. La responsabilité de l'autre conducteur reste une hypothèse à vérifier.

## Diagramme de séquence

```mermaid
sequenceDiagram
    autonumber
    actor C as Client assuré
    participant T as Twilio
    participant A as Vapi<br/>session, dialogue et LLM
    participant G as Passerelle Gradium<br/>STT et TTS
    participant P as Pipelex Orchestrator
    participant SMS as Twilio SMS
    participant UP as Page de dépôt du dossier
    participant VB as Banque de vidéos de démonstration
    participant V as OpenAI Vision
    participant CAM as Camérci<br/>Repérage et demande
    participant CCTV as Responsable de la caméra
    participant DB as Lookup assureur<br/>Mock POC / piste MIB
    participant BCF as BCF / annuaire correspondant
    participant D as Dust Claims / Legal Agent
    participant UI as Interface gestionnaire assureur
    participant SI as SI sinistres de l'assureur
    participant M as Gmail / Claim channel
    participant R as Correspondant / destinataire validé

    C->>T: Appelle le numéro sinistre
    T->>A: Appel sur numéro Twilio importé
    A->>G: Audio vers STT / texte vers TTS
    G-->>A: Transcript final / voix synthétisée
    A->>C: Bonjour, comment puis-je vous aider ?
    C-->>A: Récit libre de l'accident
    A->>P: Tour final, extraire les P0 et décider de la suite
    P-->>A: Question ciblée, clôture ou escalade
    Note over C,A: Cible happy path : 15–20 secondes.<br/>P0 : récit, lieu, prénom et nom de l’appelant.

    opt P0 manquant
        A->>C: Poser uniquement les questions P0 nécessaires, une à la fois
        C-->>A: Informations manquantes
    end
    Note over A,P: Accident venant de se produire : heure déduite de l'appel,<br/>avec time_source=inferred_from_call. Accident antérieur : demander l'heure approximative.<br/>Plaque, modèle, couleur et dégâts sont des P1 à rechercher dans les médias.

    alt Danger immédiat ou blessures graves signalés
        A-->>C: Orienter vers les secours et un interlocuteur humain
        P->>UI: Escalade prioritaire avec le contexte disponible
        Note over A,UI: Sortie du parcours normal ; reprise documentaire après prise en charge humaine.
    else Parcours documentaire normal
        P->>P: Gate 1 - Vérifier les P0 et créer le dossier de travail
        A-->>C: Annoncer le SMS de suivi et terminer l'appel
        P->>SMS: Préparer et envoyer le suivi personnalisé lié au dossier
        SMS-->>C: Résumé + P1 manquants + consignes photo + lien sécurisé
        Note over P,SMS: Ne pas redemander les informations déjà recueillies.<br/>Distinguer déclarations et faits vérifiés ; ne pas inventer un champ absent.
        C->>UP: Ouvrir le lien associé au dossier
        UP-->>C: Résumé à corriger, informations manquantes et dépôt de pièces
        opt Parcours jury
            UP->>VB: Afficher environ 10 vidéos synthétiques préparées
            C->>UP: Choisir une vidéo et, si besoin, des photos fournies
            VB-->>UP: Média sélectionné avec identifiant et provenance de démonstration
        end
        opt Pièces supplémentaires disponibles
            C->>UP: Ajouter photos / vidéos et compléter les informations manquantes
        end
        UP->>P: Soumettre les pièces sélectionnées et les compléments
        P->>P: Valider formats, lisibilité, provenance,<br/>horodatages et rattachement au dossier
    end
    Note over P,UI: Les étapes suivantes concernent uniquement un dossier ayant passé la Gate 1,<br/>hors urgence, avec des pièces reçues ; sinon attendre les compléments.

    par Analyse des pièces reçues
        P->>V: Photos, constat et images extraites des vidéos
        V-->>P: Faits observables, plaques candidates, dégâts,<br/>chronologie et références aux pièces / images
    and Recherche de caméras proches
        P->>CAM: Lieu + créneau de l'accident<br/>Identifier caméras et préparer demande
        CAM-->>P: Caméras, responsable et demande préparée si disponibles
    end

    opt Images CCTV pertinentes et manquantes
        P->>UI: Vérifier périmètre, destinataire et autorisation de demande
        alt Demande validée et responsable identifié
            UI-->>P: Autoriser la demande d'accès
            P->>CCTV: Transmettre la demande préparée avec Camérci
            Note over P,CCTV: Production : réponse asynchrone, sans garantie de disponibilité.<br/>POC : vidéo préchargée, réception explicitement simulée.
            alt Vidéo obtenue
                CCTV-->>P: Vidéo + provenance et conditions d'utilisation
                P->>V: Images de la vidéo CCTV + contexte temporel
                V-->>P: Trajectoires, plaque candidate, séquence du choc<br/>et incertitudes rattachées aux images
            else Refus, absence d'images ou délai dépassé
                P->>UI: Signaler la preuve indisponible et proposer une alternative
            end
        else Demande non autorisée ou responsable inconnu
            P->>UI: Consigner le blocage et poursuivre avec les pièces disponibles
        end
    end

    P->>P: Gate 2 - Contrôler cohérence des preuves,<br/>plaque, pays, lieu et date
    alt Plaque exploitable et suffisamment corroborée
        P->>DB: lookup_uk_insurance(plate, accident_date)
        DB-->>P: Résultat sourcé ou absence de correspondance<br/>Assureur et couverture à la date du sinistre
        Note over P,DB: POC : données fictives signalées comme mock.<br/>MIB / askMID : piste de production à qualifier selon les accès autorisés.<br/>Une plaque ne prouve pas l'identité du conducteur.
        alt Assureur identifié de façon cohérente
            P->>BCF: Rechercher le correspondant français de l'assureur UK
            BCF-->>P: Correspondant et source, ou absence de résultat
        else Assureur inconnu ou couverture incohérente
            P->>UI: Investigation complémentaire / orientation BCF à valider
        end
    else Plaque absente, ambiguë ou contradictoire
        P->>UI: Demander de nouvelles preuves / revue manuelle
    end

    P->>D: Dossier, preuves, contrat, résultats lookup / BCF<br/>et corpus juridique / procédures de l'assureur
    D-->>P: Régime et route de recours proposés, analyse responsabilité,<br/>montant justifié, sources, hypothèses et pièces manquantes
    Note over P,D: Vérifier notamment le fondement du recours et de la subrogation,<br/>les éléments d'indemnisation, le destinataire et le droit applicable.<br/>Aucune conclusion juridique définitive n'est déduite de la seule vidéo.
    P->>P: Gate 3 - Chaque affirmation importante est sourcée<br/>ou marquée comme hypothèse, contradictions explicites

    loop Tant que les gates échouent ou que le gestionnaire demande des compléments
        P->>UI: Présenter les lacunes et les investigations proposées
        UI-->>P: Corriger / demander pièces, vérification ou nouvelle analyse
        P->>P: Relancer les outils pertinents et revalider les gates
        P->>D: Réévaluer le dossier enrichi
        D-->>P: Analyse mise à jour avec sources et incertitudes
    end

    P->>UI: Dossier + brief assureur + preuves + rationnel<br/>Montant, destinataire et brouillon du recours
    UI->>UI: Gate 4 - Revue humaine des faits, droit applicable,<br/>responsabilité, montant, pièces et destinataire
    alt Dossier refusé ou laissé en attente
        UI-->>P: Refus / attente motivé
        P->>P: Conserver le dossier sans envoyer de recours
    else Version du dossier approuvée
        UI-->>P: APPROVE avec gestionnaire, date et version
        P->>SI: Créer / mettre à jour le dossier sinistre validé
        SI-->>P: Référence assureur ou erreur à traiter
        alt Référence assureur confirmée et approbation toujours valide
            P->>M: send_claim() avec la version approuvée et ses pièces
            Note over P,M: Envoi bloqué sans human_approved=true pour cette version.<br/>Toute modification du contenu, des pièces ou du destinataire impose une nouvelle revue.
            M->>R: Envoyer le recours au destinataire validé
            alt Envoi confirmé
                M-->>P: Identifiant du message et horodatage
                P-->>UI: Claim sent + référence assureur + piste d'audit
            else Échec ou statut d'envoi inconnu
                M-->>P: Erreur / statut à réconcilier
                P-->>UI: Intervention requise, éviter un envoi en double
            end
        else Création échouée ou approbation devenue obsolète
            P-->>UI: Corriger / revalider avant envoi
        end
    end
```

## Rôle des outils et périmètre du POC

| Composant | Rôle proposé | Limite / validation |
| --- | --- | --- |
| Twilio + Vapi + Gradium | Twilio fournit le numéro entrant importé dans Vapi ; Vapi gère la session, le dialogue et le LLM ; une passerelle relie Vapi aux services STT/TTS de Gradium. Voir [S08](specs/08-appel-et-intake.md). | L'API décide du triage et conserve les déclarations sourcées ; aucune valeur vocale n'est présentée comme fait vérifié. |
| Twilio SMS | Après l’appel, transmettre le résumé, les P1 manquants, les consignes photo et le lien sécurisé du dossier. | Envoi cible réel ; aperçu simulé dans la première démo. Échec de livraison visible avec possibilité de retrouver le lien dans l’interface. |
| Dépôt et banque vidéo | Recueillir les pièces ; permettre au jury de choisir parmi environ dix vidéos synthétiques. | Conserver la provenance ; les vidéos restent à préparer et ne constituent pas des preuves réelles. |
| Pipelex | Orchestration, état du dossier, analyses en parallèle, conditions et reprises. | Conserver les sources, les résultats des gates et les versions approuvées. |
| OpenAI Vision | Extraire les faits visibles des photos, documents et images vidéo. | Référencer les pièces ; conserver les ambiguïtés de plaque et les limites d'observation. |
| Camérci | Repérer les caméras et préparer une demande au responsable concerné. | Pas de récupération instantanée supposée ; réception CCTV simulée pour la démo. |
| Lookup assureur | Associer une plaque et une date à un résultat d'assurance fictif. | Mock explicite ; accès MIB / askMID et conditions d'utilisation à qualifier avant production. Ne pas inventer d'identité de conducteur. |
| BCF / correspondant | Rechercher le correspondant et préparer la route de recours à faire valider. | Source publique si disponible ; tout résultat de démonstration doit être identifié comme tel. Aucun endpoint API n'est présumé. |
| Dust claims / legal | Analyser preuves, responsabilité, contrat et procédure à partir d'un corpus ciblé. | Sources pour les affirmations importantes, hypothèses explicites et validation du gestionnaire. |
| Interface assureur / SI sinistres | Revue, amendements, approbation et création / mise à jour du sinistre. | Adaptateur SI simulable pour le POC ; garder une référence distincte du dossier de travail. |
| Gmail / claim send | Préparer puis transmettre le dossier approuvé. | Pour la démo, destinataire de test contrôlé ; conserver la preuve d'envoi et éviter les doublons. |

## Parcours de démonstration

Appel court → résumé et consignes par SMS → lien de dépôt → choix d’une vidéo parmi environ dix pour le jury + photos ciblées → recherche Camérci → réception CCTV simulée → vision et plaque corroborée → lookup assureur mock → correspondant BCF → analyse Dust → revue du gestionnaire → dossier assureur → envoi de test approuvé.

Voir le [parcours jury et les consignes SMS](jury-path.md) pour les étapes de test et les dix scénarios proposés.

Les intégrations et procédures de production restent à qualifier. Les validations ci-dessus sont des exigences de conception du POC, pas une implémentation ni un avis juridique. Cette documentation ne déclenche aucun appel, aucune demande CCTV et aucun envoi de claim.
