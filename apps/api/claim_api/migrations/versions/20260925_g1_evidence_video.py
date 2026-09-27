"""Add G1 evidence roles and a separate signed MP4 upload policy."""

from alembic import op


revision = "20260925_g1_evidence_video"
down_revision = "20260925_analysis_inputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table public.evidence add column role text, add column display_order integer")
    op.execute("alter table public.evidence add constraint evidence_display_order_positive check (display_order > 0)")
    op.execute("""update public.evidence set
        role = case
            when storage_path like '%/synthetic-g1/photo-ensemble.png' then 'vue_ensemble'
            when storage_path like '%/synthetic-g1/photo-detail.png' then 'detail_degats'
            when storage_path like '%/synthetic-g1/video-g1.mp4' then 'video_g1' end,
        display_order = case
            when storage_path like '%/synthetic-g1/photo-ensemble.png' then 1
            when storage_path like '%/synthetic-g1/photo-detail.png' then 2
            when storage_path like '%/synthetic-g1/video-g1.mp4' then 3 end
        where source_kind = 'synthetic_g1'""")
    op.execute("""create unique index evidence_g1_role_case_unique
        on public.evidence(case_id, role) where source_kind = 'synthetic_g1'""")
    op.execute("""alter table public.evidence_upload_intents
        drop constraint evidence_upload_intents_kind_check,
        drop constraint evidence_upload_intents_mime_type_check,
        drop constraint evidence_upload_intents_byte_size_check""")
    op.execute("""alter table public.evidence_upload_intents
        add constraint evidence_upload_intents_kind_check
            check (kind in ('scene_photo', 'vehicle_photo', 'damage_photo', 'document', 'other', 'scene_video')),
        add constraint evidence_upload_intents_mime_type_check
            check (mime_type in ('image/jpeg', 'image/png', 'image/webp', 'application/pdf', 'video/mp4')),
        add constraint evidence_upload_intents_byte_size_check
            check (byte_size > 0 and ((mime_type = 'video/mp4' and kind = 'scene_video' and byte_size <= 10000000)
                or (mime_type <> 'video/mp4' and kind <> 'scene_video' and byte_size <= 5242880)))""")


def downgrade() -> None:
    op.execute("""alter table public.evidence_upload_intents
        drop constraint evidence_upload_intents_kind_check,
        drop constraint evidence_upload_intents_mime_type_check,
        drop constraint evidence_upload_intents_byte_size_check""")
    op.execute("""alter table public.evidence_upload_intents
        add constraint evidence_upload_intents_kind_check
            check (kind in ('scene_photo', 'vehicle_photo', 'damage_photo', 'document', 'other')),
        add constraint evidence_upload_intents_mime_type_check
            check (mime_type in ('image/jpeg', 'image/png', 'image/webp', 'application/pdf')),
        add constraint evidence_upload_intents_byte_size_check
            check (byte_size > 0 and byte_size <= 5242880)""")
    op.execute("drop index public.evidence_g1_role_case_unique")
    op.execute("alter table public.evidence drop column role, drop column display_order")
