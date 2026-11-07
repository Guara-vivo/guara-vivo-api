"""add analysis_images.image_width and image_height

Pixel size of the image the detector saw, i.e. the space of bbox_xyxy.
Nullable: rows written before this change have no value.

Revision ID: 20260610_0018
Revises: 20260609_0017
Create Date: 2026-06-10 00:18:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260610_0018"
down_revision: Union[str, None] = "20260609_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.add_column("analysis_images", sa.Column("image_width", sa.Integer(), nullable=True))
    op.add_column("analysis_images", sa.Column("image_height", sa.Integer(), nullable=True))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_column("analysis_images", "image_height")
    op.drop_column("analysis_images", "image_width")
