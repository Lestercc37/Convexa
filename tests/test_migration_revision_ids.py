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


DOWN_REVISION = re.compile(r'^down_revision\s*=\s*(?:"([^"]+)"|None)', re.MULTILINE)


def test_the_migrations_form_a_single_chain_with_one_head() -> None:
    """Two migrations that both revise the same parent leave alembic with two heads and `alembic upgrade head` refuses
    to run. Found 2026-10-15: 0040_whale_alerts_conditions (written 10-08) and 0040_option_quote_minutes (merged
    10-14) both revised 0039."""
    revisions, parents = {}, {}
    for path in sorted(MIGRATIONS.glob("[0-9]*.py")):
        text = path.read_text(encoding="utf-8")
        revisions[path.name] = REVISION.search(text).group(1)
        parent = DOWN_REVISION.search(text)
        assert parent, f"{path.name}: no down_revision found"
        parents[path.name] = parent.group(1)
    referenced = {parent for parent in parents.values() if parent}
    heads = [name for name, revision in revisions.items() if revision not in referenced]
    assert len(heads) == 1, f"alembic would see {len(heads)} heads: {heads}"
    children = [parent for parent in parents.values() if parent]
    assert len(children) == len(set(children)), "two migrations revise the same parent"
