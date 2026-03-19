from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Keep test imports stable regardless of where pytest is invoked from.
# Supports both:
#   - repo root:   pytest tests
#   - runtime dir: pytest ../tests
REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO_ROOT / "runtime"
SAMPLES_DIR = RUNTIME_DIR / "samples"

for p in (REPO_ROOT, RUNTIME_DIR):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip sample_fixtures tests when the required files are not present.

    The runtime/samples directory lists real documents in .gitignore because they
    cannot be committed.  Tests marked sample_fixtures expect those files to exist
    and will be skipped with a clear message when they are absent, rather than
    failing with an AssertionError.
    """
    skip_marker = pytest.mark.skip(
        reason=(
            "sample fixture files are not present in runtime/samples "
            "(they are .gitignored and must be copied manually)"
        )
    )
    for item in items:
        if "sample_fixtures" not in item.keywords:
            continue
        # Collect every Path(...).exists() call would require importing the test
        # module; instead skip the whole test if *any* parametrize value that
        # looks like a filename (str ending in a known extension) is missing.
        missing = []
        if hasattr(item, "callspec"):
            for val in item.callspec.params.values():
                if isinstance(val, str) and "." in val:
                    candidate = SAMPLES_DIR / val
                    if not candidate.exists():
                        missing.append(str(candidate))
        # Also catch non-parametrised sample_fixtures tests: skip if samples dir
        # itself is empty (no non-.gitignore files).
        if not missing and not any(
            f for f in SAMPLES_DIR.iterdir() if f.name != ".gitignore"
        ):
            missing.append(str(SAMPLES_DIR))
        if missing:
            item.add_marker(skip_marker)
