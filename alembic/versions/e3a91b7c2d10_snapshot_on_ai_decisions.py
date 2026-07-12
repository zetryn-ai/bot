"""token-data snapshot on ai_decisions

Revision ID: e3a91b7c2d10
Revises: b7d4e2c91a55
Create Date: 2026-07-12 14:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e3a91b7c2d10'
down_revision: Union[str, Sequence[str], None] = 'b7d4e2c91a55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'ai_decisions',
        sa.Column(
            'snapshot',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='{}',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ai_decisions', 'snapshot')
