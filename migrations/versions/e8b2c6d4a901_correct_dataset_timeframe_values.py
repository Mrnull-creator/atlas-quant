"""Correct dataset timeframe constraint values.

Revision ID: e8b2c6d4a901
Revises: d7a4c91e2b60
"""

from alembic import op


revision = "e8b2c6d4a901"
down_revision = "d7a4c91e2b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_datasets_timeframe_values"),
        "datasets",
        type_="check",
    )

    op.create_check_constraint(
        op.f("ck_datasets_timeframe_values"),
        "datasets",
        """
        timeframe IS NULL
        OR timeframe IN (
            '1m',
            '5m',
            '15m',
            '30m',
            '1h',
            '4h',
            '1d',
            '1w',
            '1mo'
        )
        """,
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_datasets_timeframe_values"),
        "datasets",
        type_="check",
    )

    op.create_check_constraint(
        op.f("ck_datasets_timeframe_values"),
        "datasets",
        """
        timeframe IS NULL
        OR timeframe IN (
            'm1',
            'm5',
            'm15',
            'm30',
            'h1',
            'h4',
            'd1',
            'w1',
            'mn1'
        )
        """,
    )