"""Remember the local source explicitly selected for a provider link."""

import sqlalchemy as sa
from alembic import op

revision = "b2096ee28a40"
down_revision = "7feefd6b295c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("recording_links", sa.Column("video_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_recording_link_video",
        "recording_links",
        "videos",
        ["video_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_column("recording_links", "video_id")
