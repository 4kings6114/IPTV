#!/usr/bin/env python3
"""Double-click launcher companion. All runtime files live next to this file."""

import sys

if sys.version_info < (3, 10):  # noqa: UP036 -- explain the requirement on older user PCs
    print("Python 3.10 or newer is required. Install Python and try again.")
    raise SystemExit(1)

from iptv_bot.app import main

if __name__ == "__main__":
    raise SystemExit(main())
