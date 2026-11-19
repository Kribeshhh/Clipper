"""operator providers and live segment buffer

Revision ID: 01e5ff66581c
Revises: 021ff874aa21
Create Date: 2026-11-16 17:33:37.114898

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "01e5ff66581c"
down_revision: Union[str, Sequence[str], None] = "021ff874aa21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "live_feeds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("account", sa.String(), nullable=False),
        sa.Column("broadcast", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("secret_hash", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("last_seen", sa.Float(), nullable=False),
        sa.Column("latest", sa.Integer(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("last_highlight_end", sa.Float(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "provider_connections",
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("account", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sealed", sa.String(), nullable=False),
        sa.Column("checked", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("provider"),
    )
    op.create_table(
        "broadcast_recordings",
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("account", sa.String(), nullable=False),
        sa.Column("broadcast", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["videos.id"],
        ),
        sa.PrimaryKeyConstraint("video_id"),
    )
    op.create_table(
        "live_segments",
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("start", sa.Float(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["feed_id"],
            ["live_feeds.id"],
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["videos.id"],
        ),
        sa.PrimaryKeyConstraint("feed_id", "sequence"),
        sa.UniqueConstraint("video_id"),
    )
    op.create_table(
        "live_moments",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("start", sa.Float(), nullable=False),
        sa.Column("end", sa.Float(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["feed_id"],
            ["live_feeds.id"],
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["processing_jobs.id"],
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        op.f("ix_live_moments_feed_id"), "live_moments", ["feed_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_live_moments_feed_id"), table_name="live_moments")
    op.drop_table("live_moments")
    op.drop_table("live_segments")
    op.drop_table("broadcast_recordings")
    op.drop_table("provider_connections")
    op.drop_table("live_feeds")
