"""Allow an empty voice session while the caller is still speaking."""

from alembic import op

revision = "20260926_voice_call_started"
down_revision = "20260926_sms_portal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table public.voice_session_events drop constraint voice_session_events_event_type_check")
    op.execute("""alter table public.voice_session_events
                  add constraint voice_session_events_event_type_check
                  check (event_type in ('call_started', 'final_turn', 'end_of_call_report', 'call_failed'))""")


def downgrade() -> None:
    op.execute("""do $$ begin
                  if exists (select 1 from public.voice_session_events where event_type = 'call_started') then
                    raise exception 'Cannot downgrade while call_started events exist';
                  end if;
                  end $$""")
    op.execute("alter table public.voice_session_events drop constraint voice_session_events_event_type_check")
    op.execute("""alter table public.voice_session_events
                  add constraint voice_session_events_event_type_check
                  check (event_type in ('final_turn', 'end_of_call_report', 'call_failed'))""")
