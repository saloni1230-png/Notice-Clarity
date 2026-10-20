"""English application content, preserving original documents and user-entered data."""

from alembic import op

revision = "b17e20261020"
down_revision = "a045c0fc2383"
branch_labels = None
depends_on = None


def upgrade():
    from notice.legacy import migrate_english_v1

    migrate_english_v1(op.get_bind())


def downgrade():
    # Source-language restoration would rewrite user-visible data without a valid source.
    pass
