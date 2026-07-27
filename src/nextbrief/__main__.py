"""``python -m nextbrief``.

The console script is the usual entry point, but a scheduled job launched by a
desktop session gets a minimal PATH and may not see it -- and ``python -m`` needs
nothing on PATH but the interpreter that already imported this package.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
