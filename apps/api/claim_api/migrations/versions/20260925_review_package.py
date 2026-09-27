"""Version the transmitted package, handler permission, and immutable mock receipt."""

from alembic import op


revision = "20260925_review_package"
down_revision = "20260925_camera_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table public.cases add column organization_id uuid not null default gen_random_uuid()")
    op.execute("""create table public.case_memberships (
        case_id uuid not null references public.cases(id) on delete restrict,
        organization_id uuid not null,
        user_id uuid not null references auth.users(id) on delete restrict,
        role text not null check (role in ('claims_handler', 'viewer')),
        can_approve boolean not null default false,
        primary key (case_id, user_id)
    )""")
    op.execute("""insert into public.case_memberships (case_id, organization_id, user_id, role, can_approve)
        select id, organization_id, created_by_user_id, 'claims_handler', true from public.cases""")
    op.execute("alter table public.case_memberships enable row level security")
    op.execute("alter table public.drafts add column package_json jsonb")
    op.execute("alter table public.drafts add column transmission_comment text not null default ''")
    op.execute("alter table public.drafts add column created_by_user_id uuid references auth.users(id) on delete restrict")
    op.execute("alter table public.actions add column envelope_json jsonb")


def downgrade() -> None:
    op.execute("alter table public.actions drop column envelope_json")
    op.execute("alter table public.drafts drop column created_by_user_id")
    op.execute("alter table public.drafts drop column transmission_comment")
    op.execute("alter table public.drafts drop column package_json")
    op.execute("drop table public.case_memberships")
    op.execute("alter table public.cases drop column organization_id")
