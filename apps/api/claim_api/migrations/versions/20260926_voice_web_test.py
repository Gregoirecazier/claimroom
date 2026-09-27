"""Accept Vapi browser calls alongside Twilio calls."""

from alembic import op

revision = "20260926_voice_web_test"
down_revision = "20260925_insured_portal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table public.voice_sessions drop constraint voice_sessions_telephony_provider_check")
    op.execute("""alter table public.voice_sessions add constraint voice_sessions_telephony_provider_check
                  check (telephony_provider in ('twilio', 'web'))""")


def downgrade() -> None:
    op.execute("alter table public.voice_sessions drop constraint voice_sessions_telephony_provider_check")
    op.execute("""alter table public.voice_sessions add constraint voice_sessions_telephony_provider_check
                  check (telephony_provider = 'twilio')""")
