"""Creates the shared key of the NinjaTrader 'Convexa Ancla' feed (POST /api/v1/feeds/futures-anchor) if it does not exist yet.

The key is 32 random bytes (secrets.token_urlsafe), written to settings.ninjatrader_anchor_key_file on the server; the same file is copied to
the laptop (%USERPROFILE%\\.convexa\ninjatrader_anchor_key.txt) for the indicator. It is never printed: this script only says whether it wrote it.
Usage (server): python -m backend.scripts.make_ninjatrader_anchor_key"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

from backend.core.settings import get_settings


def main() -> int:
    path = Path(get_settings().ninjatrader_anchor_key_file)
    if path.exists() and path.read_text(encoding="ascii").strip():
        print(f"exists: {path}")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secrets.token_urlsafe(32), encoding="ascii")
    print(f"written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
