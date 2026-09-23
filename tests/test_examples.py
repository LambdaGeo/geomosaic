"""Smoke test: every script in examples/ runs to completion."""

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.py"))


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda p: p.name)
def test_example_runs(script):
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
