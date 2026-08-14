#!/usr/bin/env python3
"""Root entry point: python main.py <short-link>"""

import sys

from skiplink.cli import main

if __name__ == "__main__":
    sys.exit(main())
