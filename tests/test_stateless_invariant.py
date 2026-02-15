"""Stateless invariants for edcloud.

The CLI must not persist local infra state. Resource discovery is AWS tag-based.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "edcloud"


def _python_files() -> list[Path]:
    return sorted(p for p in PACKAGE_DIR.glob("*.py") if p.is_file())


def _call_name(node: ast.Call) -> tuple[str | None, str]:
    if isinstance(node.func, ast.Name):
        return None, node.func.id
    if isinstance(node.func, ast.Attribute):
        base = node.func.value.id if isinstance(node.func.value, ast.Name) else None
        return base, node.func.attr
    return None, ""


def _open_mode(node: ast.Call) -> str | None:
    if (
        len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[1].value

    for kw in node.keywords:
        if (
            kw.arg == "mode"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


def test_no_local_state_persistence_primitives() -> None:
    """Guard against adding local state files/checkpoints in package code."""
    forbidden_module_calls = {
        ("json", "dump"),
        ("pickle", "dump"),
        ("yaml", "dump"),
        ("shelve", "open"),
        ("sqlite3", "connect"),
    }
    forbidden_methods = {"write_text", "write_bytes"}

    violations: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            base, func = _call_name(node)
            if (base, func) in forbidden_module_calls or func in forbidden_methods:
                violations.append(f"{path.name}:{node.lineno} uses forbidden call '{base}.{func}'")

            if func == "open":
                mode = _open_mode(node)
                if mode is not None and any(flag in mode for flag in ("w", "a", "x", "+")):
                    violations.append(
                        f"{path.name}:{node.lineno} uses open() with write-capable mode '{mode}'"
                    )

    assert not violations, "Local state persistence primitive detected:\n" + "\n".join(violations)
