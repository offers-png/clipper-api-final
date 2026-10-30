"""Startup must survive non-UTF-8 output (Windows cp1252 consoles and log files)."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BACKEND = Path(__file__).resolve().parents[1]


class StartupOutputTests(unittest.TestCase):
    def test_app_imports_with_cp1252_output_redirected_to_a_file(self):
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONUTF8', 'PYTHONIOENCODING')}
        env['PYTHONIOENCODING'] = 'cp1252'
        with tempfile.TemporaryFile() as log:
            result = subprocess.run([sys.executable, '-c', 'import app; print("\U0001f525 startup ok")'],
                                    cwd=BACKEND, env=env, stdout=log, stderr=log, timeout=120)
            log.seek(0)
            output = log.read().decode('cp1252', errors='replace')
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('startup ok', output)


if __name__ == '__main__':
    unittest.main()
