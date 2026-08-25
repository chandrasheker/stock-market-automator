from __future__ import annotations

import runpy
from pathlib import Path


def test_repo_launcher_adds_src_to_path() -> None:
    root = Path(__file__).resolve().parents[2]
    launcher = root / "run.py"
    assert launcher.is_file()
    ns = runpy.run_path(str(launcher), run_name="not_main")
    src = str(root / "src")
    assert src in ns["sys"].path
    assert "app" in ns
