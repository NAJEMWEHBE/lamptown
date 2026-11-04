"""Allow ``python -m lamptown``."""

import sys

from lamptown.cli import main

if __name__ == "__main__":
    sys.exit(main())
