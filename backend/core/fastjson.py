"""json.loads / a bytes-returning dumps that use orjson when it is installed.

The stream processor parses every frame and the whale relay encodes/decodes
one line per quote or trade, tens of thousands a second at the open; orjson is
several times faster than the standard library for that (2026-10-06 profile:
json was ~11% of the processor and ~8% in the relay encode). Falls back to the
standard library, so a machine without orjson still works.
"""

from __future__ import annotations

import json
from typing import Any

try:
    import orjson
except ImportError:  # pragma: no cover - exercised only where orjson is absent
    orjson = None  # type: ignore[assignment]


def loads(data: str | bytes) -> Any:
    if orjson is not None:
        return orjson.loads(data)
    return json.loads(data)


def dumps_line(payload: Any) -> bytes:
    """One JSON object followed by a newline, as bytes (the relay wire format)."""
    if orjson is not None:
        return orjson.dumps(payload) + b"\n"
    return (json.dumps(payload) + "\n").encode("utf-8")
