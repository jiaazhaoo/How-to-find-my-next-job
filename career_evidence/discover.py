"""Work out what we can, instead of asking.

Every field in the config file is a question the user has to answer, and most
of them have an answer the machine already knows. `authors` is the worst
offender: it is asking someone to retype `git config user.name`.

What genuinely cannot be discovered stays in the config -- which repositories
count as "your work" is a judgement, not a fact on disk.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SEARCH_DIRS = ["~/Downloads", "~/Desktop", "~/Documents", "."]

# Places where "look around here" produces noise rather than evidence. Run
# from /tmp, discovery cheerfully offered a test fixture as the user's X
# archive and a vendored copy of rbenv as their work -- complete with its
# maintainers listed as candidate identities for them.
NOT_A_WORKSPACE = {"/", "/tmp", "/var", "/usr", "/etc", "/opt", "/bin", "/root"}


def _usable_cwd_hints(raw_hints: list[str]) -> list[str]:
    """Drop "." / ".." when the current directory is not somewhere work lives."""
    home = str(Path.home())
    out = []
    for hint in raw_hints:
        if hint not in (".", ".."):
            out.append(hint)
            continue
        try:
            resolved = str(Path(hint).resolve())
        except OSError:
            continue
        if resolved in NOT_A_WORKSPACE or resolved == home:
            continue
        if "/tmp/" in resolved + "/" or resolved.startswith("/var/folders"):
            continue
        out.append(hint)
    return out


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
    for raw in _usable_cwd_hints(list(hints or SEARCH_DIRS)):
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


def identities_in_repos(hints: list[str] | None = None,
                        limit: int = 8) -> list[tuple[str, str, int, str]]:
    """Who wrote the commits in the repos we can see, and where.

    The repository name matters: discovery can reach a vendored dependency or
    someone else's clone, and a bare list of names invites the user to paste a
    stranger's identity into their config.
    """
    tally: dict[tuple[str, str], int] = {}
    where: dict[tuple[str, str], set] = {}
    for repo in find_repos(hints=hints, identity=[], only_mine=False):
        for name, email, count in repo.top_authors:
            key = (name, email)
            tally[key] = tally.get(key, 0) + count
            where.setdefault(key, set()).add(repo.name)
    ranked = sorted(tally.items(), key=lambda kv: -kv[1])[:limit]
    return [(name, email, count, ", ".join(sorted(where[(name, email)])[:3]))
            for (name, email), count in ranked]


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


# --------------------------------------------------------------------------
# Repository discovery.
#
# "Which repositories are mine" reads like a judgement call, and it is not:
# git recorded the answer at the time. Every commit carries an author, so
# authorship is a measurement. An agent guessing from repository names would
# be strictly worse than counting -- it would miss the fork you did all your
# work in and confidently claim the tutorial you cloned once.
#
# What is left over genuinely does need a person: whether work you did under
# a different identity counts, and whether a repo you contributed two commits
# to belongs in your professional story.
# --------------------------------------------------------------------------
# "." and its parent come first: if you are running this from inside a
# repository, that repository is obviously a candidate, and on machines where
# HOME is not where work lives it may be the only one we can see.
REPO_SEARCH_DIRS = [".", "..", "~/code", "~/dev", "~/develop", "~/Developer", "~/projects",
                    "~/src", "~/repos", "~/work", "~/git", "~/Documents", "~/Desktop", "~"]

SKIP_WALK = {"node_modules", "vendor", ".venv", "venv", "Library", "Applications",
             ".Trash", ".cache", "go", ".rustup", ".cargo", "site-packages", ".git"}


@dataclass
class RepoInfo:
    path: str
    name: str
    my_commits: int
    total_commits: int
    my_share: float
    last_mine: str
    last_any: str
    authors: int
    # Kept even when nothing matched: "no repos found" is a dead end unless it
    # can also say who *did* write the commits. A configured git identity that
    # differs from commit authorship is the normal case, not an edge case --
    # a work laptop with a work email, personal repos committed under another,
    # or a per-repo override set years ago.
    top_authors: list[tuple[str, str, int]] = field(default_factory=list)

    @property
    def is_mine(self) -> bool:
        return self.my_commits > 0


def _find_git_dirs(root: Path, max_depth: int, budget: list[int]) -> list[Path]:
    found: list[Path] = []
    stack = [(root, 0)]
    while stack and budget[0] > 0:
        current, depth = stack.pop()
        budget[0] -= 1
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        if any(e.name == ".git" for e in entries):
            found.append(current)
            continue          # do not descend into a repo looking for more
        if depth >= max_depth:
            continue
        for e in entries:
            if e.is_dir() and not e.is_symlink() and e.name not in SKIP_WALK \
                    and not e.name.startswith("."):
                stack.append((e, depth + 1))
    return found


def repo_authorship(repo: Path, identity: list[str]) -> RepoInfo | None:
    """Count commits by author. One `git shortlog` pass per repo."""
    out = _git("shortlog", "-sne", "--all", "--no-merges", cwd=repo)
    if not out:
        return None
    import re
    mine = {i.strip().lower() for i in identity if i.strip()}
    my_commits = total = authors = 0
    seen: list[tuple[str, str, int]] = []
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*?)\s+<(.+?)>", line)
        if not m:
            continue
        count, name, email = int(m.group(1)), m.group(2), m.group(3)
        total += count
        authors += 1
        seen.append((name, email, count))
        if any(token == name.lower() or token == email.lower() for token in mine):
            my_commits += count
    if not total:
        return None
    return RepoInfo(
        top_authors=sorted(seen, key=lambda a: -a[2])[:5],
        path=str(repo), name=repo.name, my_commits=my_commits, total_commits=total,
        my_share=round(my_commits / total, 3), authors=authors,
        # No identity means "match nobody" -- used when we only want to know
        # who the authors are. Passing an empty --author would match everyone.
        last_mine=(_git("log", "-1", "--format=%aI", f"--author={identity[0]}",
                        cwd=repo)[:10] if identity else ""),
        last_any=_git("log", "-1", "--format=%aI", cwd=repo)[:10],
    )


def find_repos(hints: list[str] | None = None, identity: list[str] | None = None,
               max_depth: int = 3, scan_budget: int = 4000,
               only_mine: bool = True) -> list[RepoInfo]:
    """Every git repo you have actually committed to, most-yours first.

    ``only_mine=False`` returns the repos that were scanned but matched
    nobody, so a caller can show which identities do appear there.
    """
    identity = identity or git_identity()
    budget = [scan_budget]
    seen: set[Path] = set()
    repos: list[RepoInfo] = []
    for raw in _usable_cwd_hints(list(hints or REPO_SEARCH_DIRS)):
        base = Path(raw).expanduser()
        if not base.is_dir():
            continue
        # "~" last and shallow: it is the catch-all, not a place to spend the budget
        depth = 1 if base == Path.home() else max_depth
        for repo in _find_git_dirs(base, depth, budget):
            resolved = repo.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            info = repo_authorship(repo, identity)
            if info and (info.is_mine or not only_mine):
                repos.append(info)
    repos.sort(key=lambda r: (-r.my_commits, r.last_mine), reverse=False)
    repos.sort(key=lambda r: (r.my_commits, r.last_mine), reverse=True)
    return repos
