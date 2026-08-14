#!/usr/bin/env python3
"""SkipLink launcher - one executable, two faces.

  No arguments  ->  one-click GUI (Tkinter)
  URL / flags   ->  command line mode

This is the entry point used by the PyInstaller builds (see
.github/workflows/build.yml). Running `python launcher.py` behaves
identically to `python gui.py` (no args) or `python main.py` (with args).
"""

import sys


def main():
    if len(sys.argv) <= 1:
        from skiplink.gui import run

        run()
    else:
        from skiplink.cli import main as cli_main

        sys.exit(cli_main())


if __name__ == "__main__":
    main()
