"""portrait export settings and crop evidence

Revision ID: 021ff874aa21
Revises: 0cb7b4b4c44b
Create Date: 2026-11-16 12:53:04.915915

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "021ff874aa21"
down_revision: Union[str, Sequence[str], None] = "0cb7b4b4c44b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "clips", sa.Column("details", sa.JSON(none_as_null=True), nullable=True)
    )
    op.add_column(
        "processing_jobs", sa.Column("edit", sa.JSON(none_as_null=True), nullable=True)
    )
    op.add_column("processing_jobs", sa.Column("error", sa.String(), nullable=True))
    op.add_column(
        "video_analysis",
        sa.Column("focus_points", sa.JSON(none_as_null=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("video_analysis", "focus_points")
    op.drop_column("processing_jobs", "error")
    op.drop_column("processing_jobs", "edit")
    op.drop_column("clips", "details")
