"""Entry script for the PyInstaller build: runs the same main() as the `vrec` console script."""

import sys

from vrec.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
