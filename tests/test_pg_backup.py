from datetime import datetime
from zoneinfo import ZoneInfo

from backend.scripts.pg_backup import dump_name, dumps_to_delete, in_market_hours

ET = ZoneInfo("America/New_York")


def test_keeps_the_newest_and_only_deletes_our_complete_dumps():
    names = [
        "convexa_20261020_020000.dump",
        "convexa_20261021_020000.dump",
        "convexa_20261022_020000.dump",
        "convexa_20261023_020000.dump",
        "convexa_20261023_030000.dump.partial",
        "convexa.dump",  # the 2026-09-25 migration file has another name: never touched
        "notes.txt",
    ]
    assert dumps_to_delete(names, keep=2, just_written="convexa_20261023_020000.dump") == [
        "convexa_20261020_020000.dump",
        "convexa_20261021_020000.dump",
    ]


def test_never_deletes_the_file_just_written():
    names = ["convexa_20261023_020000.dump"]
    assert dumps_to_delete(names, keep=0, just_written="convexa_20261023_020000.dump") == []


def test_market_hours_guard():
    assert in_market_hours(datetime(2026, 10, 23, 10, 0, tzinfo=ET))
    assert not in_market_hours(datetime(2026, 10, 23, 2, 0, tzinfo=ET))
    assert not in_market_hours(datetime(2026, 10, 24, 11, 0, tzinfo=ET))  # Saturday
    assert not in_market_hours(datetime(2026, 10, 23, 16, 20, tzinfo=ET))


def test_dump_name_uses_eastern_time():
    assert (
        dump_name(datetime(2026, 10, 24, 6, 0, tzinfo=ZoneInfo("UTC")))
        == "convexa_20261024_020000.dump"
    )
