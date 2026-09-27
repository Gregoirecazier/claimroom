"""Join the concurrent voice-start and automatic-assessment migrations."""

revision = "20260926_merge_astra_voice"
down_revision = ("20260926_astra_journey", "20260926_voice_call_started")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
