"""Initial migration with all models

Revision ID: 577189d69fe0
Revises:
Create Date: 2026-10-14 21:58:08.185125

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '577189d69fe0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # This revision was originally generated empty because the tables already
    # existed locally. It now creates the schema as it stood at this revision
    # (before aeb8eee804c4 added repositories.updated_at and replaced the url
    # unique constraint with an index) so a fresh database can be migrated.
    # Databases already past this revision never re-run it.
    op.create_table('repositories',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('local_path', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('url', name='repositories_url_key')
    )
    op.create_table('repository_files',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('repository_id', sa.Integer(), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('language', sa.String(length=50), nullable=True),
    sa.Column('file_size', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('code_imports',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('file_id', sa.Integer(), nullable=False),
    sa.Column('imported_module', sa.String(length=500), nullable=False),
    sa.ForeignKeyConstraint(['file_id'], ['repository_files.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('code_symbols',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('file_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('symbol_type', sa.String(length=50), nullable=False),
    sa.Column('qualified_name', sa.String(length=500), nullable=True),
    sa.Column('start_line', sa.Integer(), nullable=False),
    sa.Column('end_line', sa.Integer(), nullable=False),
    sa.Column('signature', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['file_id'], ['repository_files.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('code_calls',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('symbol_id', sa.Integer(), nullable=False),
    sa.Column('called_name', sa.String(length=500), nullable=False),
    sa.ForeignKeyConstraint(['symbol_id'], ['code_symbols.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('code_chunks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('repository_id', sa.Integer(), nullable=False),
    sa.Column('file_id', sa.Integer(), nullable=False),
    sa.Column('symbol_id', sa.Integer(), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('start_line', sa.Integer(), nullable=False),
    sa.Column('end_line', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['file_id'], ['repository_files.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['symbol_id'], ['code_symbols.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('code_chunks')
    op.drop_table('code_calls')
    op.drop_table('code_symbols')
    op.drop_table('code_imports')
    op.drop_table('repository_files')
    op.drop_table('repositories')
