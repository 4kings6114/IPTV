#!/usr/bin/env python3
"""Start the interactive IPTV Telegram bot; importing does not start it."""

import sys

if __name__ == "__main__":
    if sys.version_info < (3, 10):  # noqa: UP036 - fail clearly before importing 3.10 syntax
        print("Python 3.10 or newer is required.", file=sys.stderr)
        sys.exit(2)
    from iptv_bot.cli import main

    sys.exit(main())
