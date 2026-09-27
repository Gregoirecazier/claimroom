"""Shared synthetic reference tables used by matching and handler read APIs."""
from uuid import UUID

from claim_api.analysis import AnalysisError
from claim_api.astra_analysis import catalogue
from claim_api.mock_insurance import insurance_lookup, normalize_plate


class ReferenceData:
    def __init__(self, repository):
        self.repository = repository

    def vehicle(self, country, plate):
        with self.repository._connection() as conn:
            row = conn.execute('select * from public.mock_vehicle_registry where country=%s and plate_normalized=%s',
                               (country,plate)).fetchone()
        if row:
            for field in ('coverage_start','coverage_end'):
                if row[field]: row[field] = row[field].isoformat()
        return row

    def insurance_lookup(self, plate, country, incident_date):
        return insurance_lookup(plate,country,incident_date,record_lookup=self.vehicle)

    def vehicles(self, *, plate=None, country=None, limit=50, offset=0):
        country = 'UK' if country == 'GB' else country
        normalized = normalize_plate(plate) if plate else None
        where = '(%s::text is null or country=%s) and (%s::text is null or plate_normalized=%s)'
        params = (country,country,normalized,normalized)
        with self.repository._connection() as conn:
            total = conn.execute('select count(*) as n from public.mock_vehicle_registry where '+where,params).fetchone()['n']
            rows = conn.execute('select * from public.mock_vehicle_registry where '+where+
                                ' order by country,plate_normalized limit %s offset %s',(*params,limit,offset)).fetchall()
        return {'synthetic':True,'total':total,'items':rows,'limit':limit,'offset':offset}

    def videos(self):
        with self.repository._connection() as conn:
            rows = conn.execute('''select id,label,original_filename,sha256,mime_type,byte_size,
                duration_seconds,source_kind,provenance_note,synthetic,active,jsonb_array_length(frames) as frame_count
                from public.accident_video_catalogue order by label,id''').fetchall()
        return {'synthetic':True,'total':len(rows),'items':rows}

    def video_catalogue(self, video_id: UUID | None = None):
        with self.repository._connection() as conn:
            rows = conn.execute('''select * from public.accident_video_catalogue
                where (%s::uuid is null and active) or id=%s order by label,id''',(video_id,video_id)).fetchall()
        bundled = {entry['id']:entry for entry in catalogue()}
        entries = []
        for row in rows:
            entry = bundled.get(str(row['id']))
            if (entry is None or row['sha256'] != entry['sha256'] or row['media_path'] != entry['path']
                    or row['frames'] != entry['frames'] or row['synthetic'] is not True):
                raise AnalysisError('catalogue_out_of_sync','Le catalogue en base ne correspond pas aux fichiers vidéo déployés.')
            # Use only verified local assets. No scenario, policy or contact is given to Astra.
            entries.append({**entry,'label':row['label'],'original_filename':row['original_filename'],
                            'byte_size':row['byte_size'],'duration_seconds':row['duration_seconds'],
                            'source_kind':row['source_kind'],'provenance_note':row['provenance_note']})
        return entries
