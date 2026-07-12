"""mark-to-market + executed TP-ladder rungs on positions

Revision ID: b7d4e2c91a55
Revises: 8c1f2a9d3b41
Create Date: 2026-07-12 12:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7d4e2c91a55'
down_revision: Union[str, Sequence[str], None] = '8c1f2a9d3b41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'positions',
        sa.Column('unrealized_pnl_pct', sa.Numeric(precision=12, scale=6), nullable=True),
    )
    op.add_column(
        'positions',
        sa.Column('marked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'positions',
        sa.Column(
            'partials',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='[]',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('positions', 'partials')
    op.drop_column('positions', 'marked_at')
    op.drop_column('positions', 'unrealized_pnl_pct')
