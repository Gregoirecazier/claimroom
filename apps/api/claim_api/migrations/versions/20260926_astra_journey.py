"""One automatic accident assessment, handler review and transactional notification outbox."""
from alembic import op

revision = '20260926_astra_journey'
down_revision = '20260926_sms_portal'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''create table public.accident_video_catalogue (
        id uuid primary key, asset_key text not null unique, synthetic boolean not null default true,
        active boolean not null default true
    );
    create table public.case_accident_reviews (
        id uuid primary key default gen_random_uuid(),
        case_id uuid not null references public.cases(id) on delete cascade,
        content_revision bigint not null, model text not null,
        assessment jsonb not null, insurance_matches jsonb not null,
        blockers jsonb not null, status text not null check (status in ('needs_information','awaiting_review','approved')),
        approved_amount_minor bigint check (approved_amount_minor > 0),
        approved_by uuid references auth.users(id), approved_at timestamptz, amendment_reason text,
        created_at timestamptz not null default now(), unique(case_id,content_revision)
    );
    create table public.case_claim_notifications (
        id uuid primary key default gen_random_uuid(),
        review_id uuid not null references public.case_accident_reviews(id) on delete cascade,
        case_id uuid not null references public.cases(id) on delete cascade,
        channel text not null check (channel in ('sms','email')),
        recipient text not null, subject text not null, body text not null,
        mode text not null check (mode in ('simulated','live')),
        status text not null default 'queued' check (status in ('queued','sending','sent','simulated','failed','unknown','cancelled')),
        provider_message_id text, error_code text, started_at timestamptz,
        created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
        unique(review_id,channel)
    );
    create index claim_notification_pending on public.case_claim_notifications(status) where status='queued';
    ''')
    op.execute("""create function public.queue_corrected_accident() returns trigger language plpgsql as $$
        begin
            if new.content_revision <> old.content_revision then
                update public.case_claim_notifications set status='cancelled',error_code='case_changed'
                    where case_id=new.id and status='queued';
                if exists(select 1 from public.evidence where case_id=new.id and mime_type like 'image/%') then
                    insert into public.case_media_workflows(case_id,content_revision)
                        values(new.id,new.content_revision) on conflict(case_id,content_revision) do nothing;
                end if;
            end if;
            return new;
        end $$;
        create trigger corrected_accident_queue after update of content_revision on public.cases
            for each row execute function public.queue_corrected_accident();
    """)
    # Stable source IDs match the versioned visual catalogue; no accident ground truth is stored here.
    from uuid import uuid5, NAMESPACE_URL
    for i in range(1,4):
        identifier = str(uuid5(NAMESPACE_URL, f'claimroom:video-catalogue:v1:{i}'))
        op.execute(f"insert into public.accident_video_catalogue(id,asset_key) values ('{identifier}','{identifier}')")
    for table in ('accident_video_catalogue','case_accident_reviews','case_claim_notifications'):
        op.execute(f'alter table public.{table} enable row level security')
        op.execute(f'revoke all on public.{table} from public')
        op.execute(f"""do $$ declare r text; begin for r in select rolname from pg_roles where rolname in ('anon','authenticated')
            loop execute format('revoke all on public.{table} from %I',r); end loop; end $$""")


def downgrade():
    op.execute('drop trigger corrected_accident_queue on public.cases')
    op.execute('drop function public.queue_corrected_accident()')
    for table in ('case_claim_notifications','case_accident_reviews','accident_video_catalogue'):
        op.execute(f'drop table public.{table}')
