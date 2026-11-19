"""checkpointed highlight analysis

Revision ID: 0cb7b4b4c44b
Revises: 3d7dfe90eed3
Create Date: 2026-11-16 12:21:17.135747

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0cb7b4b4c44b"
down_revision: Union[str, Sequence[str], None] = "3d7dfe90eed3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "video_analysis",
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("levels", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("samples", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("transcript", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("warning", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["videos.id"],
        ),
        sa.PrimaryKeyConstraint("video_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("video_analysis")
