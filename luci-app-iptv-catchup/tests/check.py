#!/usr/bin/env python3
"""Run the bundled network-free tests against the installed package layout."""

import sys
import types
import unittest
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
scripts = types.ModuleType("scripts")
scripts.__path__ = [str(root / "root/usr/lib/iptv-catchup")]
sys.modules["scripts"] = scripts
suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py")
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
