"""``python -m lamptown.app``: start the desktop app (what the shortcuts run)."""

import sys

from lamptown.app.cli import main

if __name__ == "__main__":
    sys.exit(main())
