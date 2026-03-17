from __future__ import annotations

import sys
from pathlib import Path

# Keep test imports stable regardless of where pytest is invoked from.
# Supports both:
#   - repo root:   pytest tests
#   - runtime dir: pytest ../tests
REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO_ROOT / "runtime"

for p in (REPO_ROOT, RUNTIME_DIR):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
