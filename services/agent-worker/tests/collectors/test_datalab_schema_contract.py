"""Schema-contract tests for the DataLab collector.

The behavioural tests use a Fake connection, so they cannot catch a mismatch
between the columns the collector INSERTs and the columns the migrations
actually define. These tests parse the migration SQL and the collector source
and assert every INSERT targets columns that exist in the final schema.

This is the class of bug that previously slipped through: the collector wrote
`category_id` into datalab_raw_details and enqueued processing_queue with
stock_id=NULL, neither of which the old migrations supported.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import app.collectors.datalab as datalab_module

_REPO_ROOT = Path(__file__).resolve().parents[4]
_MIGRATIONS = _REPO_ROOT / "database" / "migrations"

_NON_COLUMN_KEYWORDS = {
    "CONSTRAINT",
    "PRIMARY",
    "FOREIGN",
    "UNIQUE",
    "CHECK",
}


def _table_columns(create_body: str) -> set[str]:
    """Extract column names from the body of a CREATE TABLE statement."""
    columns: set[str] = set()
    depth = 0
    current = []
    pieces: list[str] = []
    for ch in create_body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            pieces.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        pieces.append("".join(current))

    for piece in pieces:
        tokens = piece.strip().split()
        if not tokens:
            continue
        first = tokens[0].upper()
        if first in _NON_COLUMN_KEYWORDS:
            continue
        columns.add(tokens[0].strip().lower())
    return columns


def _extract_create_table(sql: str, table: str) -> str | None:
    """Return the parenthesised body of `CREATE TABLE ... <table> ( ... )`."""
    pattern = re.compile(
        rf"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+{re.escape(table)}\s*\(",
        re.IGNORECASE,
    )
    match = pattern.search(sql)
    if not match:
        return None
    start = match.end()  # just after the opening paren
    depth = 1
    for idx in range(start, len(sql)):
        ch = sql[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return sql[start:idx]
    return None


def _migration_columns(table: str) -> set[str]:
    """Final column set for a table after applying migrations in order.

    The last migration that (re)creates the table wins, so iterate in sorted
    filename order and keep the most recent CREATE TABLE body.
    """
    columns: set[str] | None = None
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        body = _extract_create_table(path.read_text(encoding="utf-8"), table)
        if body is not None:
            columns = _table_columns(body)
    return columns or set()


def _collector_insert_columns(table: str) -> set[str]:
    """Columns used in the collector's `INSERT INTO <table> ( ... )`."""
    source = Path(datalab_module.__file__).read_text(encoding="utf-8")
    pattern = re.compile(
        rf"INSERT INTO {re.escape(table)}\s*\(([^)]*)\)",
        re.IGNORECASE,
    )
    match = pattern.search(source)
    if not match:
        return set()
    return {
        col.strip().lower()
        for col in match.group(1).split(",")
        if col.strip()
    }


@unittest.skipUnless(_MIGRATIONS.is_dir(), "migrations directory not found")
class TestDataLabSchemaContract(unittest.TestCase):
    def test_raw_documents_insert_columns_exist(self):
        table_cols = _migration_columns("datalab_raw_documents")
        insert_cols = _collector_insert_columns("datalab_raw_documents")
        self.assertTrue(insert_cols, "collector should INSERT into datalab_raw_documents")
        missing = insert_cols - table_cols
        self.assertFalse(missing, f"datalab_raw_documents missing columns: {missing}")

    def test_raw_details_insert_columns_exist(self):
        table_cols = _migration_columns("datalab_raw_details")
        insert_cols = _collector_insert_columns("datalab_raw_details")
        self.assertTrue(insert_cols, "collector should INSERT into datalab_raw_details")
        missing = insert_cols - table_cols
        self.assertFalse(missing, f"datalab_raw_details missing columns: {missing}")

    def test_raw_details_is_category_based_not_stock_based(self):
        table_cols = _migration_columns("datalab_raw_details")
        self.assertIn("category_id", table_cols)
        self.assertNotIn("stock_id", table_cols)

    def test_processing_queue_insert_columns_exist(self):
        table_cols = _migration_columns("processing_queue")
        insert_cols = _collector_insert_columns("processing_queue")
        self.assertTrue(insert_cols, "collector should INSERT into processing_queue")
        missing = insert_cols - table_cols
        self.assertFalse(missing, f"processing_queue missing columns: {missing}")

    def test_processing_queue_stock_id_is_nullable(self):
        """The collector enqueues DataLab tasks with stock_id=NULL, so the
        final schema must not declare NOT NULL on processing_queue.stock_id
        (either nullable in the CREATE TABLE itself, or relaxed by a later
        ALTER ... DROP NOT NULL migration)."""
        stock_id_definition = None
        dropped_not_null = False
        for path in sorted(_MIGRATIONS.glob("*.sql")):
            text = path.read_text(encoding="utf-8")
            body = _extract_create_table(text, "processing_queue")
            if body is not None:
                for piece in body.split(","):
                    tokens = piece.strip().split()
                    if tokens and tokens[0].lower() == "stock_id":
                        stock_id_definition = piece.strip()
            if re.search(
                r"ALTER TABLE\s+processing_queue\s+ALTER COLUMN\s+stock_id\s+DROP NOT NULL",
                text,
                re.IGNORECASE,
            ):
                dropped_not_null = True
        self.assertIsNotNone(stock_id_definition, "processing_queue must define stock_id")
        declared_not_null = re.search(r"NOT\s+NULL", stock_id_definition, re.IGNORECASE)
        self.assertTrue(
            not declared_not_null or dropped_not_null,
            "processing_queue.stock_id must be nullable for NORMALIZE_DATALAB tasks",
        )


if __name__ == "__main__":
    unittest.main()
