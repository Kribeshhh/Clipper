"""index source exports and analysis queue

Revision ID: 10643340759a
Revises: 01e5ff66581c
Create Date: 2026-11-16 19:57:27.046197

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "10643340759a"
down_revision: Union[str, Sequence[str], None] = "01e5ff66581c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_processing_jobs_video_created",
        "processing_jobs",
        ["video_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_video_analysis_state_created",
        "video_analysis",
        ["state", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_video_analysis_state_created", table_name="video_analysis")
    op.drop_index("ix_processing_jobs_video_created", table_name="processing_jobs")
