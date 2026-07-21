"""Static architecture dependency guards."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ytdlp_bot"

# Approved dependency direction:
#   adapters -> ports -> domain
#   adapters -> application -> ports/domain
#   application -> ports, domain
#   ports -> domain (+ port-owned DTOs)
#   domain -> (nothing outside domain)
FORBIDDEN = {
    "domain": (
        "ytdlp_bot.adapters",
        "ytdlp_bot.application",
        "ytdlp_bot.ports",
        "aiogram",
        "discord",
        "aiohttp",
        "aiosqlite",
        "yt_dlp",
    ),
    "application": (
        "ytdlp_bot.adapters",
        "aiogram",
        "discord",
        "aiohttp",
        "aiosqlite",
        "yt_dlp",
    ),
    "ports": (
        "ytdlp_bot.adapters",
        "ytdlp_bot.application",
        "aiogram",
        "discord",
        "aiohttp",
        "aiosqlite",
        "yt_dlp",
        "pathlib",
    ),
}


def _iter_py_files(package_dir: Path) -> list[Path]:
    return sorted(p for p in package_dir.rglob("*.py") if p.is_file())


def _module_layer(path: Path) -> str | None:
    rel = path.relative_to(SRC)
    parts = rel.parts
    if not parts:
        return None
    top = parts[0]
    if top in {"domain", "application", "ports", "adapters"}:
        return top
    return None


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _is_forbidden(layer: str, imported: str) -> bool:
    prefixes = FORBIDDEN.get(layer, ())
    return any(imported == prefix or imported.startswith(prefix + ".") for prefix in prefixes)


def test_no_forbidden_imports_in_layers() -> None:
    violations: list[str] = []
    for path in _iter_py_files(SRC):
        layer = _module_layer(path)
        if layer is None or layer == "adapters":
            continue
        for imported in _imported_modules(path):
            if _is_forbidden(layer, imported):
                violations.append(
                    f"{path.relative_to(ROOT)} imports {imported!r} "
                    f"(forbidden in {layer}). "
                    "Allowed: adapters→ports/application/domain; "
                    "application→ports/domain; ports→domain; domain→domain only."
                )
    assert violations == [], "\n".join(violations)


def test_domain_has_no_io_or_time_calls() -> None:
    """Domain must not call time.time, datetime.now, os.environ, open, pathlib."""
    banned_attrs = {
        ("time", "time"),
        ("time", "monotonic"),
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("os", "environ"),
        ("os", "getenv"),
        ("pathlib", "Path"),
    }
    violations: list[str] = []
    domain_dir = SRC / "domain"
    for path in _iter_py_files(domain_dir):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                pair = (node.value.id, node.attr)
                if pair in banned_attrs:
                    violations.append(f"{path.name}:{node.lineno} uses {pair[0]}.{pair[1]}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"open", "input"}
            ):
                violations.append(f"{path.name}:{node.lineno} calls {node.func.id}()")
    assert violations == [], "\n".join(violations)


def test_forbidden_fixture_imports_detected() -> None:
    """Prove the guard rejects representative illegal imports via AST check helper."""
    sample = "import ytdlp_bot.adapters.platform.telegram\nfrom aiogram import Bot\n"
    tree = ast.parse(sample)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    assert any(_is_forbidden("domain", n) for n in names)
    assert any(_is_forbidden("application", n) for n in names)


def test_no_package_cycles_among_top_layers() -> None:
    """Top-level layers must not form import cycles (coarse check)."""
    edges: dict[str, set[str]] = {
        "domain": set(),
        "application": set(),
        "ports": set(),
        "adapters": set(),
    }
    for path in _iter_py_files(SRC):
        layer = _module_layer(path)
        if layer is None:
            continue
        for imported in _imported_modules(path):
            if not imported.startswith("ytdlp_bot."):
                continue
            parts = imported.split(".")
            if len(parts) < 2:
                continue
            other = parts[1]
            if other in edges and other != layer:
                edges[layer].add(other)

    # Expected edges only.
    allowed = {
        ("adapters", "ports"),
        ("adapters", "application"),
        ("adapters", "domain"),
        ("application", "ports"),
        ("application", "domain"),
        ("ports", "domain"),
    }
    actual = {(a, b) for a, targets in edges.items() for b in targets}
    unexpected = actual - allowed
    assert unexpected == set(), f"unexpected layer edges: {unexpected}"
