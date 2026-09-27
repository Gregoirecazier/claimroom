"""Allow simulated WhatsApp follow-up while retaining historical SMS rows."""

from alembic import op

revision = "20260926_whatsapp_followup"
down_revision = "20260926_voice_web_test"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table public.case_messages drop constraint case_messages_channel_check")
    op.execute("""alter table public.case_messages add constraint case_messages_channel_check
                  check (channel in ('sms', 'whatsapp'))""")
    op.execute("alter table public.case_messages alter column channel set default 'whatsapp'")


def downgrade() -> None:
    op.execute("""do $$ begin
                  if exists (select 1 from public.case_messages where channel = 'whatsapp') then
                    raise exception 'Cannot downgrade while WhatsApp messages exist';
                  end if;
                  end $$""")
    op.execute("alter table public.case_messages drop constraint case_messages_channel_check")
    op.execute("""alter table public.case_messages add constraint case_messages_channel_check
                  check (channel = 'sms')""")
    op.execute("alter table public.case_messages alter column channel set default 'sms'")
