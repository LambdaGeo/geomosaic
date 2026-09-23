"""Smoke test: every script in examples/ runs to completion."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.py"))
SRC_DIR = str(Path(__file__).parent.parent / "src")


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda p: p.name)
def test_example_runs(script):
    env = dict(os.environ)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}:{existing_pp}" if existing_pp else SRC_DIR
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, check=False, env=env)
    assert result.returncode == 0, result.stderr
