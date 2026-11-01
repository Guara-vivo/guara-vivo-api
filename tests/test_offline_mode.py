import subprocess
import sys
import unittest
from pathlib import Path

SCENARIOS = Path(__file__).with_name("offline_scenarios.py")


class OfflineModeTests(unittest.TestCase):
    def test_offline_scenarios(self):
        # OFFLINE_MODE is fixed at import time, so the scenarios run in a fresh interpreter.
        result = subprocess.run(
            [sys.executable, "-I", str(SCENARIOS)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr[-3000:]}",
        )
        self.assertIn("ALL OFFLINE SCENARIOS PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
