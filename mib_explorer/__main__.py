"""Run the Tkinter MIB explorer."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from mib_explorer import gui


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Explore OIDs from local MIB/SMI files.")
    parser.add_argument(
        "paths",
        metavar="PATH",
        nargs="*",
        type=Path,
        help="MIB/SMI file or directory to load at startup (directories are searched recursively)",
    )
    args = parser.parse_args(argv)
    gui.main(args.paths)


if __name__ == "__main__":
    main()
