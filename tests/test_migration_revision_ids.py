"""alembic_version.version_num is varchar(32): a longer revision id passes review and tests but fails at `alembic upgrade`
("value too long for type character varying(32)") when it tries to record itself. Caught on 2026-10-08 by migrating
Convexa_test with a 35-character id (0040_whale_alerts_condition_premium); this keeps it from recurring."""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "db" / "migrations"
REVISION = re.compile(r'^revision\s*=\s*"([^"]+)"', re.MULTILINE)


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    files = sorted(MIGRATIONS.glob("[0-9]*.py"))
    assert files, "no migration files found"
    too_long = {}
    for path in files:
        match = REVISION.search(path.read_text(encoding="utf-8"))
        assert match, f"{path.name}: no revision id found"
        if len(match.group(1)) > 32:
            too_long[path.name] = (match.group(1), len(match.group(1)))
    assert not too_long, f"revision ids longer than 32 characters: {too_long}"
