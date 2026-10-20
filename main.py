#!/usr/bin/env python3
"""Vanguard entry point.

    python3 main.py --demo        run the whole pipeline end to end on synthetic data (no setup, keys or Docker)
    python3 main.py <command>     any vanguard command, e.g. `python3 main.py rank --top 25` (needs vanguard/private/)

See README.md and vanguard/README.md.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if args[0] in ("--demo", "demo"):
        script, rest = "demo.py", args[1:]
    else:
        script, rest = "vanguard.py", args
    return subprocess.call([sys.executable, os.path.join(HERE, "vanguard", script)] + rest)


if __name__ == "__main__":
    sys.exit(main())
