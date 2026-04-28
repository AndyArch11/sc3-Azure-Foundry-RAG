"""CI enforcement: logging.basicConfig must not be used outside approved scripts.

Rationale
---------
All services bootstrap logging through ``runtime.log_config.configure_logging``
or ``query_web.log_config.configure_logging``, which installs a structured JSON
handler with correlation/trace injection.  Direct calls to
``logging.basicConfig`` bypass this setup and produce unstructured output that
breaks log correlation across services.

This test is intentionally a *static* AST check so it runs fast in CI with no
imports and catches violations before code is deployed.  Adding a new file to
``_ALLOWED_FILES`` is an explicit, code-reviewed decision.
"""

from __future__ import annotations

import ast
import pathlib

# ---------------------------------------------------------------------------
# Allowlist: scripts explicitly permitted to use basicConfig.
# These are standalone CLI / ops tools that run outside the service process
# and do not need structured JSON correlation logging.
# ---------------------------------------------------------------------------
_ALLOWED_FILES: frozenset[str] = frozenset(
    [
        "ops/scripts/local/seed_local.py",
    ]
)

# Source roots to scan (relative to the workspace root, i.e. the cwd when
# pytest is invoked from the project root).
_SOURCE_ROOTS: list[str] = [
    "runtime",
    "query_web",
    "tests",
]


def _uses_basicconfig(path: pathlib.Path) -> bool:
    """Return True if *path* contains any AST-level ``logging.basicConfig`` call."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # logging.basicConfig(...)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "basicConfig"
            and isinstance(func.value, ast.Name)
            and func.value.id == "logging"
        ):
            return True
        # from logging import basicConfig; basicConfig(...)
        if isinstance(func, ast.Name) and func.id == "basicConfig":
            return True
    return False


def test_no_basicconfig_outside_allowlist() -> None:
    """Fail if any source file calls logging.basicConfig outside the allowlist.

    To silence this check legitimately, add the file path (relative, posix) to
    ``_ALLOWED_FILES`` in this module with a justification comment.
    """
    cwd = pathlib.Path.cwd()
    violations: list[str] = []

    for root_name in _SOURCE_ROOTS:
        root = cwd / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            # Skip virtual environments and compiled caches inside source roots.
            parts = path.relative_to(cwd).parts
            if any(p in (".venv", "__pycache__", "site-packages") for p in parts):
                continue
            rel = path.relative_to(cwd).as_posix()
            if rel in _ALLOWED_FILES:
                continue
            if _uses_basicconfig(path):
                violations.append(rel)

    assert not violations, (
        "logging.basicConfig used outside the shared logging bootstrap.\n"
        "Replace with configure_logging() from runtime.log_config or\n"
        "query_web.log_config, or add the path to _ALLOWED_FILES in\n"
        "tests/unit/test_logging_bootstrap_enforcement.py with a justification.\n\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )
