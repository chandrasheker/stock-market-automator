#!/usr/bin/env python3
"""Run the research CLI from a checkout without requiring PYTHONPATH.

Prefer installing the package into your venv:

    python -m pip install -e ".[dev]"
    python -m crude_research.cli doctor

This launcher is a fallback:

    python run.py doctor
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from crude_research.cli import app  # noqa: E402

if __name__ == "__main__":
    app()
