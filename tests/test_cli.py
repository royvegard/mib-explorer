from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from mib_explorer import __main__ as app_main


class CommandLineTests(unittest.TestCase):
    def test_main_passes_paths_to_gui_for_startup_loading(self) -> None:
        paths = ["./mibs", "./vendor/EXTRA-MIB"]

        with patch.object(app_main.gui, "main") as gui_main:
            app_main.main(paths)

        gui_main.assert_called_once_with([Path(path) for path in paths])

    def test_main_starts_gui_without_paths(self) -> None:
        with patch.object(app_main.gui, "main") as gui_main:
            app_main.main([])

        gui_main.assert_called_once_with([])


if __name__ == "__main__":
    unittest.main()
