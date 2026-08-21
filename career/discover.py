"""Work out what we can, instead of asking.

Every field in the config file is a question the user has to answer, and most
of them have an answer the machine already knows. `authors` is the worst
offender: it is asking someone to retype `git config user.name`.

What genuinely cannot be discovered stays in the config -- which repositories
count as "your work" is a judgement, not a fact on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SEARCH_DIRS = ["~/Downloads", "~/Desktop", "~/Documents", "."]


def _git(*args: str, cwd: Path | None = None) -> str:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10,
                           cwd=str(cwd) if cwd else None)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def git_identity(repo: Path | None = None) -> list[str]:
    """Your name and email as git knows them, local config first."""
    found: list[str] = []
    for scope in ([], ["--global"]):
        for key in ("user.name", "user.email"):
            value = _git("config", *scope, "--get", key, cwd=repo)
            if value and value not in found:
                found.append(value)
    return found


def looks_like_x_archive(path: Path) -> bool:
    return (path / "data" / "tweets.js").exists() or bool(list(path.glob("tweet*.js")))


def looks_like_notion_export(path: Path) -> bool:
    """Notion stamps a 32-hex id onto every exported filename."""
    import re
    pattern = re.compile(r"\s[0-9a-f]{32}\.(?:md|csv)$")
    for depth, entry in enumerate(path.rglob("*")):
        if depth > 2000:
            break
        if entry.is_file() and pattern.search(entry.name):
            return True
    return False


def _candidates(hints: list[str] | None = None) -> list[Path]:
    out: list[Path] = []
    for raw in (hints or SEARCH_DIRS):
        base = Path(raw).expanduser()
        if not base.is_dir():
            continue
        out.append(base)
        try:
            out.extend(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            continue
    return out


def find_export(kind: str, hints: list[str] | None = None) -> Path | None:
    """Look for an unzipped export in the places downloads actually land."""
    test = {"x": looks_like_x_archive, "notion": looks_like_notion_export}[kind]
    for path in _candidates(hints):
        try:
            if test(path):
                return path
        except OSError:
            continue
    return None


def detect_sources(config_connectors: dict | None = None) -> dict:
    """Everything discoverable, in one call, for `init` and `doctor`."""
    from .connectors import REGISTRY

    out: dict = {"identity": git_identity(), "connectors": {}}
    for name, connector in REGISTRY.items():
        conf = (config_connectors or {}).get(name, {})
        if not conf.get("path") and name in ("notion-export", "x-archive"):
            guess = find_export("notion" if name.startswith("notion") else "x")
            if guess:
                conf = {**conf, "path": str(guess)}
        ok, note = connector.available(conf)
        out["connectors"][name] = {"ready": ok, "note": note,
                                   "discovered_path": conf.get("path")}
    return out
