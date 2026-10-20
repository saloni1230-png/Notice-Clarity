"""Durable, page-scoped model processing checkpoints."""

from alembic import op
import sqlalchemy as sa

revision = "c21e20261020"
down_revision = "b17e20261020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("questions", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("questions", "revision", server_default=None)
    op.add_column(
        "documents", sa.Column("analysis_version", sa.String(40), nullable=False, server_default="")
    )
    op.add_column("documents", sa.Column("generation", sa.Integer(), nullable=False, server_default="1"))
    op.execute("UPDATE documents SET analysis_version = version")
    op.alter_column("documents", "analysis_version", server_default=None)
    op.alter_column("documents", "generation", server_default=None)
    op.create_table(
        "analysis_chunks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("page_id", sa.String(36), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_analysis_chunks_page_id", "analysis_chunks", ["page_id"])


def downgrade():
    op.drop_table("analysis_chunks")
    op.drop_column("questions", "revision")
    op.drop_column("documents", "generation")
    op.drop_column("documents", "analysis_version")
