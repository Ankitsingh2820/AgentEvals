"""phase 2 pricing registry and costs

Revision ID: 05baf313d45e
Revises: 55f07831b387
Create Date: 2026-09-24 20:02:41.566700

Adds the model/tool pricing registry, prompt-cache token counts, stored latency totals and
estimated-cost columns. Existing runs get their latency totals backfilled from their
steps; their costs stay NULL (they ran before any pricing existed).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "05baf313d45e"
down_revision: str | Sequence[str] | None = "55f07831b387"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(precision=20, scale=10)
PRICE = sa.Numeric(precision=14, scale=6)


def upgrade() -> None:
    op.create_table(
        "model_pricing",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_price_per_mtok", PRICE, nullable=False),
        sa.Column("output_price_per_mtok", PRICE, nullable=False),
        sa.Column("cache_write_price_per_mtok", PRICE, nullable=True),
        sa.Column("cache_read_price_per_mtok", PRICE, nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "model", "effective_date"),
    )
    op.create_table(
        "tool_pricing",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("price_per_call", PRICE, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tool_name", "effective_date"),
    )

    zero = sa.text("0")
    for table in ("llm_calls", "runs"):
        op.add_column(
            table,
            sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False,
                      server_default=zero),
        )
        op.add_column(
            table,
            sa.Column("cache_read_input_tokens", sa.Integer(), nullable=False,
                      server_default=zero),
        )

    op.add_column("llm_calls", sa.Column("pricing_id", sa.String(length=36), nullable=True))
    op.add_column("llm_calls", sa.Column("pricing_snapshot", sa.JSON(), nullable=True))
    for col in ("input_cost", "output_cost", "cache_write_cost", "cache_read_cost",
                "estimated_cost"):
        op.add_column("llm_calls", sa.Column(col, MONEY, nullable=True))
    op.create_foreign_key(
        "fk_llm_calls_pricing_id", "llm_calls", "model_pricing", ["pricing_id"], ["id"]
    )

    op.add_column("runs", sa.Column("llm_latency_ms", sa.Float(), nullable=False,
                                    server_default=zero))
    op.add_column("runs", sa.Column("tool_latency_ms", sa.Float(), nullable=False,
                                    server_default=zero))
    for col in ("estimated_llm_cost", "estimated_tool_cost", "estimated_total_cost"):
        op.add_column("runs", sa.Column(col, MONEY, nullable=True))
    op.add_column("runs", sa.Column("currency", sa.String(length=3), nullable=True))
    op.add_column("runs", sa.Column("cost_status", sa.String(length=20), nullable=True))

    op.add_column("tool_calls", sa.Column("pricing_id", sa.String(length=36), nullable=True))
    op.add_column("tool_calls", sa.Column("estimated_cost", MONEY, nullable=True))
    op.create_foreign_key(
        "fk_tool_calls_pricing_id", "tool_calls", "tool_pricing", ["pricing_id"], ["id"]
    )

    # Backfill stored latency totals for runs recorded before this migration.
    op.execute(
        """
        UPDATE runs SET
          llm_latency_ms = COALESCE((SELECT SUM(s.latency_ms) FROM run_steps s
                                     WHERE s.run_id = runs.id AND s.type = 'llm_call'), 0),
          tool_latency_ms = COALESCE((SELECT SUM(s.latency_ms) FROM run_steps s
                                      WHERE s.run_id = runs.id AND s.type = 'tool_call'), 0)
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_tool_calls_pricing_id", "tool_calls", type_="foreignkey")
    op.drop_column("tool_calls", "estimated_cost")
    op.drop_column("tool_calls", "pricing_id")
    for col in ("cost_status", "currency", "estimated_total_cost", "estimated_tool_cost",
                "estimated_llm_cost", "tool_latency_ms", "llm_latency_ms",
                "cache_read_input_tokens", "cache_creation_input_tokens"):
        op.drop_column("runs", col)
    op.drop_constraint("fk_llm_calls_pricing_id", "llm_calls", type_="foreignkey")
    for col in ("estimated_cost", "cache_read_cost", "cache_write_cost", "output_cost",
                "input_cost", "pricing_snapshot", "pricing_id", "cache_read_input_tokens",
                "cache_creation_input_tokens"):
        op.drop_column("llm_calls", col)
    op.drop_table("tool_pricing")
    op.drop_table("model_pricing")
