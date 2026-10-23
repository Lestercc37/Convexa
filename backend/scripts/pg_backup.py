"""Nightly backup of the Convexa Postgres database to ANOTHER physical disk, and a restore test.

Why: checked 2026-10-23, there was no backup at all: the only dump was the 2026-09-25 laptop->server migration file,
on the same NVMe disk (V:) as the live database; archive_mode off; no Windows backup. A failure of that one disk would
lose every stored chain, level and study. Lester approved a nightly dump on 2026-10-23.

What it does (backup):
  * pg_dump in custom format (-Fc, compressed) of the database Settings.database_url points to, into --dest
    (default C:\\ConvexaBackups, the other SSD), first as <name>.dump.partial, renamed to <name>.dump only when pg_dump
    exits 0 AND `pg_restore --list` reads the file back;
  * keeps the newest --keep complete dumps (default 7) and deletes older ones; only files named convexa_*.dump in
    --dest are ever deleted, and never the one just written;
  * refuses to run during market hours (09:30-16:15 ET, Mon-Fri) unless --force;
  * the password goes to pg_dump through the PGPASSWORD environment of the child process only: it is never printed,
    logged or written to disk. Output is sizes, counts and durations.

Restore test (--restore-test FILE): restores FILE into a NEW database (default Convexa_restore_test; refuses if it
exists or if the name is the live database) with pg_restore -j 4, then compares row counts of the key tables with the
live database and prints them. The test database is left in place for review; dropping it is a separate, manual step.

Usage (scheduled task ConvexaPgBackup, daily 02:00 ET):
  python -m backend.scripts.pg_backup [--dest C:\\ConvexaBackups] [--keep 7] [--pg-bin V:\\Convexa\\postgres\\18\\bin] [--force]
  python -m backend.scripts.pg_backup --restore-test C:\\ConvexaBackups\\convexa_YYYYMMDD_HHMMSS.dump [--force]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_BLOCK_UNTIL = dtime(16, 15)
DEFAULT_DEST = r"C:\ConvexaBackups"
DEFAULT_PG_BIN = r"V:\Convexa\postgres\18\bin"
DUMP_PREFIX = "convexa_"
DUMP_SUFFIX = ".dump"
RESTORE_TEST_DB = "Convexa_restore_test"
KEY_TABLES = (
    "option_chain_snapshots",
    "option_contracts",
    "gamma_aggregates",
    "gamma_aggregate_items",
    "market_snapshots",
    "whale_alerts",
    "daily_bars",
    "users",
)


def in_market_hours(now: datetime) -> bool:
    local = now.astimezone(EASTERN)
    return local.weekday() < 5 and MARKET_OPEN <= local.time() < MARKET_BLOCK_UNTIL


def dump_name(now: datetime) -> str:
    return f"{DUMP_PREFIX}{now.astimezone(EASTERN):%Y%m%d_%H%M%S}{DUMP_SUFFIX}"


def dumps_to_delete(names: list[str], keep: int, just_written: str) -> list[str]:
    """Complete dumps beyond the newest `keep`, oldest first; never the file just written, never anything that is not
    one of ours (convexa_*.dump, so .partial files and unrelated files are left alone)."""
    ours = sorted(n for n in names if n.startswith(DUMP_PREFIX) and n.endswith(DUMP_SUFFIX))
    excess = ours[: max(0, len(ours) - keep)]
    return [n for n in excess if n != just_written]


def _connection() -> tuple[dict[str, str], dict[str, str]]:
    """(libpq args, child environment). The password only ever lives in the child's environment."""
    from sqlalchemy.engine import make_url

    from backend.core.settings import Settings

    url = make_url(Settings().database_url)
    args = {
        "host": url.host or "localhost",
        "port": str(url.port or 5432),
        "user": url.username or "postgres",
        "dbname": url.database or "",
    }
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    return args, env


def _log(message: str) -> None:
    print(f"{datetime.now(EASTERN):%Y-%m-%d %H:%M:%S} {message}", flush=True)


def backup(dest: Path, keep: int, pg_bin: Path) -> int:
    conn, env = _connection()
    dest.mkdir(parents=True, exist_ok=True)
    name = dump_name(datetime.now(EASTERN))
    partial = dest / (name + ".partial")
    final = dest / name
    started = time.monotonic()
    _log(f"pg_backup: dumping database '{conn['dbname']}' to {final}")
    result = subprocess.run(
        [
            str(pg_bin / "pg_dump.exe"),
            "-h",
            conn["host"],
            "-p",
            conn["port"],
            "-U",
            conn["user"],
            "-d",
            conn["dbname"],
            "-Fc",
            "-Z",
            "6",
            "--no-password",
            "-f",
            str(partial),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        _log(f"pg_backup: FAILED, pg_dump exit {result.returncode}: {result.stderr.strip()[:500]}")
        partial.unlink(missing_ok=True)
        return 1
    listing = subprocess.run(
        [str(pg_bin / "pg_restore.exe"), "--list", str(partial)],
        capture_output=True,
        text=True,
        check=False,
    )
    entries = sum(1 for line in listing.stdout.splitlines() if line and not line.startswith(";"))
    if listing.returncode != 0 or entries == 0:
        _log(
            f"pg_backup: FAILED, the dump does not read back (pg_restore --list exit {listing.returncode}, {entries} entries)"
        )
        partial.unlink(missing_ok=True)
        return 1
    partial.replace(final)
    size_gb = final.stat().st_size / 1e9
    _log(
        f"pg_backup: OK {final.name} {size_gb:.2f} GB, {entries} archive entries, {time.monotonic() - started:.0f} s"
    )
    for old in dumps_to_delete([p.name for p in dest.iterdir() if p.is_file()], keep, final.name):
        (dest / old).unlink()
        _log(f"pg_backup: removed old dump {old}")
    return 0


def restore_test(dump: Path, pg_bin: Path, target_db: str) -> int:
    import psycopg

    conn, env = _connection()
    if target_db == conn["dbname"]:
        _log("pg_backup: refusing to restore over the live database")
        return 2
    base = {k: v for k, v in conn.items() if k != "dbname"}
    password = env.get("PGPASSWORD")
    with psycopg.connect(dbname="postgres", password=password, autocommit=True, **base) as admin:
        exists = admin.execute(
            "select 1 from pg_database where datname = %s", (target_db,)
        ).fetchone()
        if exists:
            _log(
                f"pg_backup: database {target_db} already exists; drop it first (manual step) or pick another name"
            )
            return 2
        admin.execute(f'CREATE DATABASE "{target_db}"')
    started = time.monotonic()
    _log(f"pg_backup: restoring {dump.name} into {target_db}")
    result = subprocess.run(
        [
            str(pg_bin / "pg_restore.exe"),
            "-h",
            conn["host"],
            "-p",
            conn["port"],
            "-U",
            conn["user"],
            "-d",
            target_db,
            "-j",
            "4",
            "--no-owner",
            "--no-password",
            str(dump),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    _log(
        f"pg_backup: pg_restore exit {result.returncode} in {time.monotonic() - started:.0f} s; stderr lines {len(result.stderr.splitlines())}"
    )
    for line in result.stderr.splitlines()[:10]:
        _log(f"   {line[:200]}")
    mismatches = 0
    with (
        psycopg.connect(dbname=conn["dbname"], password=password, **base) as live,
        psycopg.connect(dbname=target_db, password=password, **base) as copy,
    ):
        for table in KEY_TABLES:
            try:
                a = live.execute(f'select count(*) from "{table}"').fetchone()[0]
                b = copy.execute(f'select count(*) from "{table}"').fetchone()[0]
            except psycopg.Error as error:
                live.rollback()
                copy.rollback()
                _log(f"   {table}: not compared ({type(error).__name__})")
                continue
            # The live database keeps writing after the dump started, so live >= copy is expected for growing tables.
            note = "OK" if b <= a and (a - b) <= max(1, a // 50) else "CHECK"
            mismatches += note != "OK"
            _log(f"   {table}: live {a:,} | restored {b:,} | {note}")
    _log(
        f"pg_backup: restore test {'PASSED' if result.returncode == 0 and mismatches == 0 else 'NEEDS A LOOK'}; {target_db} left in place for review"
    )
    return 0 if result.returncode == 0 and mismatches == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dest", default=DEFAULT_DEST)
    parser.add_argument("--keep", type=int, default=7)
    parser.add_argument("--pg-bin", default=DEFAULT_PG_BIN)
    parser.add_argument("--restore-test", default="")
    parser.add_argument("--restore-db", default=RESTORE_TEST_DB)
    parser.add_argument("--force", action="store_true", help="run even during market hours")
    args = parser.parse_args(argv)
    if in_market_hours(datetime.now(EASTERN)) and not args.force:
        _log("pg_backup: market hours (09:30-16:15 ET): not running (use --force to override)")
        return 3
    if args.keep < 1:
        parser.error("--keep must be at least 1")
    if args.restore_test:
        return restore_test(Path(args.restore_test), Path(args.pg_bin), args.restore_db)
    return backup(Path(args.dest), args.keep, Path(args.pg_bin))


if __name__ == "__main__":
    sys.exit(main())
