"""Numbered SQL migrations from db/migrations (ADR 0003): each file applied once, in order.

`schema_migrations` records version, file name and SHA-256 (line endings normalised, so a
CRLF checkout matches). A changed checksum on an applied file stops startup: applied
migrations are immutable, a change needs a new numbered file. Files may not contain `;`
inside a statement or `--` inside a string literal (see `split_statements`).
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"
FILE_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_CREATE_LEDGER = text(
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    " version TEXT PRIMARY KEY,"
    " name TEXT NOT NULL,"
    " checksum TEXT NOT NULL,"
    " applied_at TIMESTAMP WITH TIME ZONE NOT NULL)"
)
_SELECT_LEDGER = text("SELECT version, checksum FROM schema_migrations")
_RECORD = text(
    "INSERT INTO schema_migrations (version, name, checksum, applied_at)"
    " VALUES (:version, :name, :checksum, :applied_at)"
).bindparams(bindparam("applied_at", type_=DateTime(timezone=True)))


class MigrationChecksumError(RuntimeError):
    """An already-applied migration file was changed after it ran."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str
    checksum: str


def split_statements(sql: str) -> list[str]:
    """Statements of one file: `--` comments removed, split on `;`, blanks dropped."""
    without_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [part.strip() for part in without_comments.split(";") if part.strip()]


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Migration files sorted by version; other files in the directory are ignored."""
    found = []
    for path in sorted(directory.glob("*.sql")):
        match = FILE_NAME.match(path.name)
        if match is None:
            logger.warning("Ignoring migration file with an invalid name: %s", path.name)
            continue
        sql = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        found.append(Migration(match.group(1), path.name, sql, checksum))
    return found


async def apply_migrations(engine: AsyncEngine, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; returns the versions applied by this call."""
    migrations = load_migrations(directory)
    async with engine.begin() as connection:
        await connection.execute(_CREATE_LEDGER)
        applied = {str(v): str(c) for v, c in await connection.execute(_SELECT_LEDGER)}
    _verify_checksums(migrations, applied)
    done = []
    for migration in migrations:
        if migration.version in applied:
            continue
        async with engine.begin() as connection:
            await _apply(connection, migration)
        logger.info("Applied migration %s", migration.name)
        done.append(migration.version)
    return done


def _verify_checksums(migrations: list[Migration], applied: dict[str, str]) -> None:
    for migration in migrations:
        recorded = applied.get(migration.version)
        if recorded is not None and recorded != migration.checksum:
            raise MigrationChecksumError(
                f"Migration {migration.name} was already applied but its content changed "
                f"(recorded sha256 {recorded[:12]}, file {migration.checksum[:12]}). "
                "Applied migrations must not be edited: restore the file and put the change "
                "in a new numbered file."
            )


async def _apply(connection: AsyncConnection, migration: Migration) -> None:
    # The ledger INSERT runs first: on SQLite it opens the transaction, so the DDL that
    # follows rolls back with it if any statement fails (pysqlite does not BEGIN for DDL).
    await connection.execute(
        _RECORD,
        {
            "version": migration.version,
            "name": migration.name,
            "checksum": migration.checksum,
            "applied_at": datetime.now(UTC),
        },
    )
    for statement in split_statements(migration.sql):
        await connection.execute(text(statement))
