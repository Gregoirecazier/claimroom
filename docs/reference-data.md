# Référentiels de démonstration

La base PostgreSQL de Claimroom contient deux référentiels utilisés par le parcours automatique. Aucune caméra municipale ni registre d’assurance réel n’est sollicité.

## Plaques, conducteurs et assureurs

`public.mock_vehicle_registry` contient les 2 000 plaques du jeu existant : 1 000 françaises et 1 000 britanniques. Chaque plaque possède un véhicule, un conducteur fictif (`driver_name`, `driver_email`), un assureur (`insurer_id`, `insurer_name`) et son contact sinistres (`insurer_contact_name`, `insurer_email`). Les emails sont fictifs, sous des domaines `.test`.

Le parcours Astra recherche les plaques lisibles dans cette table et conserve le contact trouvé avec sa proposition. Le destinataire du recours est `insurer_email`. En mode d’envoi réel de démonstration, l’application conserve la redirection vers l’adresse de test configurée ; elle n’envoie pas aux domaines fictifs.

La recherche normalise les espaces, les tirets et la casse, sans compléter une plaque partielle. La date du sinistre doit correspondre à la couverture. Les 100 lignes de test sans police restent sans couverture établie, même si elles disposent désormais d’un assureur associé.

## Vidéos simulant les archives de la ville

`public.accident_video_catalogue` répertorie les trois fichiers fournis :

| Archive | Fichier d’origine | UUID |
| --- | --- | --- |
| G1 | `9275878b-acf4-4a60-9f27-df34d226a68d.mp4` | `0d4c4f9d-2202-5f89-ace9-c3e078b26d83` |
| G2 | `cea24e58-62ab-4fb7-8cd0-e1f32f50bf92.mp4` | `4b8b8419-ba43-58b2-a8da-e3f84752065c` |
| G3 | `98ea44f9-0ac7-46b8-8a08-79cf32cbde1a.mp4` | `16e532a7-faaa-540e-8924-67c7adcc91c6` |

Ces fichiers sont identiques aux MP4 déjà livrés avec l’API : leur empreinte SHA-256 a été comparée aux originaux fournis. Ils sont référencés sans dupliquer les fichiers. La base conserve le chemin du média privé, le nom d’origine, l’empreinte, la taille, la durée (5,04 secondes), le type MIME et 20 images horodatées par vidéo. Les fichiers binaires restent dans les ressources déployées de l’API, et ne sont pas stockés dans des colonnes PostgreSQL.

`source_kind=simulated_city_camera` et `provenance_note` indiquent explicitement la simulation. `active` contrôle les vidéos proposées pour les nouveaux rapprochements ; une vidéo désactivée reste consultable dans une proposition existante.

Le parcours compare les photos de l’assuré aux images des vidéos actives. Il vérifie la cohérence entre les métadonnées en base et les fichiers déployés avant l’analyse ou la lecture. Les noms de scénarios et les données du registre ne sont pas fournis au modèle pour choisir la vidéo. Les horodatages servent aux citations de l’analyse ; aucune correspondance forte et unique signifie une demande de précisions.

## Import et vérification

La migration `20260926_reference_data`, après `20260926_merge_astra_voice`, crée et peuple le registre et enrichit le catalogue existant. Son jeu de données est figé dans `claim_api/migrations/data/20260926_reference_data.json.gz` pour rendre l’import reproductible. Les contraintes d’unicité portent sur le pays et la plaque, ainsi que sur l’empreinte vidéo.

Depuis `apps/api`, avec `MIGRATION_DATABASE_URL` configurée et TLS activé :

```sh
uv run --locked alembic upgrade head
```

Relancer la commande ne crée aucun doublon. Vérification SQL :

```sql
select country, count(*) from public.mock_vehicle_registry group by country;
select label, original_filename, byte_size, duration_seconds,
       jsonb_array_length(frames) as images_horodatees, active
from public.accident_video_catalogue order by label;
```

API, avec authentification gestionnaire :

- `GET /v1/reference-data/vehicles?plate=AB12%20CDE&country=UK` : recherche exacte et contacts.
- `GET /v1/reference-data/vehicles?limit=50&offset=0` : registre paginé, maximum 200 lignes par page.
- `GET /v1/reference-data/videos` : les métadonnées des trois vidéos.
- `GET /v1/reference-data/videos/{uuid}/content` : lecture du MP4 privé, sans cache public.

Les tables ne sont pas exposées directement aux rôles Supabase `anon` et `authenticated` ; les routes vérifient la session avant d’interroger la base.
