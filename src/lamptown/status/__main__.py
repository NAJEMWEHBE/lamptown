"""Allow ``python -m lamptown.status``."""

import sys

from lamptown.status.cli import main

if __name__ == "__main__":
    sys.exit(main())
