"""route column on positions + closed_trades (strategy visibility)

Revision ID: 8c1f2a9d3b41
Revises: 70a97e16f0f4
Create Date: 2026-07-12 14:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8c1f2a9d3b41'
down_revision: Union[str, Sequence[str], None] = '70a97e16f0f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'positions',
        sa.Column('route', sa.String(length=24), nullable=False, server_default=''),
    )
    op.add_column(
        'closed_trades',
        sa.Column('route', sa.String(length=24), nullable=False, server_default=''),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('closed_trades', 'route')
    op.drop_column('positions', 'route')
