"""Regression coverage for dev startup with a pip-less virtual environment."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest


class DevLocalBootstrapTests(unittest.TestCase):
    def run_bootstrap(self, use_uv):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copyfile(Path(__file__).parents[1] / "scripts/dev-local.sh", root / "scripts/dev-local.sh")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            # Keep tool discovery deterministic, independent of host uv/pip installs.
            for tool in ("bash", "dirname", "mkdir", "chmod"):
                (bin_dir / tool).symlink_to(shutil.which(tool))
            python = root / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text('#!/bin/bash\nif [[ "$1" == "-m" && "$2" == "ensurepip" ]]; then echo bootstrap-ensurepip; exit 42; fi\nexit 1\n')
            python.chmod(0o755)
            if use_uv:
                uv = bin_dir / "uv"
                uv.write_text('#!/bin/bash\nprintf "uv-args: %s\\n" "$*"\nexit 42\n')
                uv.chmod(0o755)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                free_port = str(sock.getsockname()[1])
            env = {"PATH": str(bin_dir), "HOME": directory, "HOJA_TAILSCALE_IP": "100.64.0.1",
                   "HOJA_DEV_BACKEND_PORT": free_port, "HOJA_DEV_PORT": free_port,
                   "HOJA_DEV_HOST": "127.0.0.1"}
            result = subprocess.run([shutil.which("bash"), str(root / "scripts/dev-local.sh"), "--tailscale"], env=env, capture_output=True, text=True)
            return result

    def test_uv_installs_into_existing_pipless_venv(self):
        result = self.run_bootstrap(True)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn("uv-args: pip install --python", result.stdout)
        self.assertIn(".[server,mcp,agent]", result.stdout)
        self.assertNotIn("bootstrap-ensurepip", result.stdout)

    def test_without_uv_bootstraps_pip_in_venv(self):
        result = self.run_bootstrap(False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("bootstrap-ensurepip", result.stdout)
        self.assertIn("Install uv or enable Python ensurepip", result.stderr)


if __name__ == "__main__":
    unittest.main()
