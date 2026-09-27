"""Load the fictional vehicle/contact registry and supplied video archive metadata."""
from datetime import date
import gzip
import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = '20260926_reference_data'
down_revision = '20260926_merge_astra_voice'
branch_labels = None
depends_on = None


def upgrade():
    # Freeze the seed with the migration: future fixture edits cannot alter this import.
    seed = json.loads(gzip.decompress((Path(__file__).parents[1] / 'data' /
                                     '20260926_reference_data.json.gz').read_bytes()))
    op.execute('''create table public.mock_vehicle_registry (
        record_id text primary key, country text not null check (country in ('FR','UK')),
        plate text not null, plate_normalized text not null,
        synthetic boolean not null check (synthetic), source_version text not null,
        vehicle_make text not null, vehicle_model text, vehicle_color text not null,
        driver_name text not null, driver_email text not null,
        insurer_id text not null, insurer_name text not null,
        insurer_contact_name text not null, insurer_email text not null,
        policy_reference text, coverage_start date, coverage_end date,
        correspondent_fr_id text, correspondent_fr_name text,
        unique(country,plate_normalized),
        check (driver_email like '%@%.test' and insurer_email like '%@%.test'),
        check (coverage_end is null or coverage_start <= coverage_end)
    );
    create index mock_vehicle_insurer on public.mock_vehicle_registry(insurer_id);
    alter table public.mock_vehicle_registry enable row level security;
    revoke all on public.mock_vehicle_registry from public;
    do $$ declare r text; begin for r in select rolname from pg_roles where rolname in ('anon','authenticated')
        loop execute format('revoke all on public.mock_vehicle_registry from %I',r); end loop; end $$;
    alter table public.accident_video_catalogue
        add column label text,
        add column original_filename text,
        add column media_path text,
        add column sha256 text check (sha256 ~ '^[a-f0-9]{64}$'),
        add column mime_type text check (mime_type = 'video/mp4'),
        add column byte_size bigint check (byte_size > 0),
        add column duration_seconds double precision check (duration_seconds > 0),
        add column frames jsonb check (jsonb_typeof(frames) = 'array'),
        add column source_kind text,
        add column provenance_note text;
    create unique index accident_video_sha256 on public.accident_video_catalogue(sha256);
    ''')
    rows = seed['vehicles']
    for row in rows:
        for key in ('coverage_start', 'coverage_end'):
            if row[key]: row[key] = date.fromisoformat(row[key])
    columns = [sa.column(key, sa.Boolean() if key == 'synthetic' else
                         sa.Date() if key in ('coverage_start','coverage_end') else sa.Text())
               for key in rows[0]]
    table = sa.table('mock_vehicle_registry', *columns, schema='public')
    op.bulk_insert(table, rows)
    for video in seed['videos']:
        # SQLAlchemy literal rendering keeps offline SQL generation and online migration identical.
        values = {key: video[key] for key in ('label','original_filename','sha256','mime_type',
                  'byte_size','duration_seconds','source_kind','provenance_note')}
        values['media_path'] = video['path']
        values['frames'] = json.dumps(video['frames'])
        assignments = ','.join(f'{key}={str(sa.literal(value).compile(compile_kwargs={"literal_binds": True}))}'
                               + ('::jsonb' if key == 'frames' else '') for key,value in values.items())
        identifier = str(sa.literal(video['id']).compile(compile_kwargs={'literal_binds':True}))
        op.execute(f'update public.accident_video_catalogue set {assignments} where id={identifier}::uuid')


def downgrade():
    op.execute('drop table public.mock_vehicle_registry')
    op.execute('drop index public.accident_video_sha256')
    for column in ('label','original_filename','media_path','sha256','mime_type','byte_size',
                   'duration_seconds','frames','source_kind','provenance_note'):
        op.execute(f'alter table public.accident_video_catalogue drop column {column}')
