"""Allow classified CCTV/insured clips, retaining 5 MiB limits for other files."""
from alembic import op

revision = "20260926_video_evidence"
down_revision = "20260926_whatsapp_followup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for field in ("kind", "mime_type", "byte_size"):
        op.execute(f"alter table public.evidence_upload_intents drop constraint evidence_upload_intents_{field}_check")
    op.execute("""alter table public.evidence_upload_intents
        add constraint evidence_upload_intents_kind_check check (kind in
            ('scene_photo', 'vehicle_photo', 'damage_photo', 'document', 'other', 'scene_video', 'cctv_video', 'insured_video')),
        add constraint evidence_upload_intents_mime_type_check check (mime_type in
            ('image/jpeg', 'image/png', 'image/webp', 'application/pdf', 'video/mp4', 'video/quicktime', 'video/webm')),
        add constraint evidence_upload_intents_byte_size_check check
            (byte_size > 0 and byte_size <= case when mime_type like 'video/%' then 52428800 else 5242880 end),
        add constraint evidence_upload_intents_video_kind_check check
            ((mime_type like 'video/%') = (kind in ('scene_video', 'cctv_video', 'insured_video')))
    """)


def downgrade() -> None:
    # PostgreSQL rejects this downgrade if video intents still exist; do not delete evidence.
    for field in ("kind", "mime_type", "byte_size", "video_kind"):
        op.execute(f"alter table public.evidence_upload_intents drop constraint evidence_upload_intents_{field}_check")
    op.execute("""alter table public.evidence_upload_intents
        add constraint evidence_upload_intents_kind_check check
            (kind in ('scene_photo', 'vehicle_photo', 'damage_photo', 'document', 'other', 'scene_video')),
        add constraint evidence_upload_intents_mime_type_check check
            (mime_type in ('image/jpeg', 'image/png', 'image/webp', 'application/pdf', 'video/mp4')),
        add constraint evidence_upload_intents_byte_size_check check (byte_size > 0 and ((mime_type = 'video/mp4' and kind = 'scene_video' and byte_size <= 10000000) or (mime_type <> 'video/mp4' and kind <> 'scene_video' and byte_size <= 5242880)))
    """)
