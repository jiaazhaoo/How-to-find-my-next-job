"""Stage A of the reading funnel: turn a pile of folders into a manifest.

This stage burns zero model tokens and is where most of the volume dies.
On a real machine the ratio is brutal and that is the point -- vendored
dependencies, lockfiles, build output and binaries are 90%+ of the bytes and
~0% of the career evidence. Cutting them before anything reaches a model is
the single largest efficiency win in the whole pipeline.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .connectors.base import STAGING_DIRNAME

# Directories that never contain evidence about *you*.
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "third_party", "bower_components",
    "dist", "build", "out", "target", "bin", "obj", ".next", ".nuxt", ".output",
    "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache", ".pytest_cache",
    ".gradle", ".idea", ".vscode", "coverage", "htmlcov", ".terraform", "Pods",
    "site-packages", ".cache", ".DS_Store", "migrations/versions",
}

EXCLUDED_NAME_RE = re.compile(
    r"(?:^|[.\-_])(?:min|bundle|chunk|generated|pb|_pb2|lock)\.[a-z]+$"
    r"|^(?:package-lock|yarn|pnpm-lock|poetry|Cargo|composer|Gemfile|go)\.(?:json|lock|sum)$"
    r"|\.(?:map|snap|pyc|class|o|so|dll|dylib|a|jar|war|zip|tar|gz|bz2|xz|7z|rar|"
    r"png|jpe?g|gif|bmp|ico|svg|webp|mp4|mov|avi|mp3|wav|ttf|otf|woff2?|eot|"
    r"pdf~|db|sqlite3?|parquet|pkl|bin|onnx|safetensors|h5|ckpt)$",
    re.IGNORECASE)

TEXT_EXT = {
    ".md", ".markdown", ".rst", ".txt", ".adoc", ".org", ".tex",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".scala",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".m", ".mm",
    ".sh", ".bash", ".zsh", ".sql", ".r", ".jl", ".lua", ".pl", ".ex", ".exs",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".json", ".xml", ".html", ".css", ".scss",
    ".vue", ".svelte", ".proto", ".graphql", ".tf", ".dockerfile", ".gitignore",
}
RICH_EXT = {".pdf", ".docx", ".pptx", ".ipynb", ".xlsx"}

LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".scala": "Scala", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
    ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".sh": "Shell",
    ".bash": "Shell", ".sql": "SQL", ".r": "R", ".jl": "Julia", ".lua": "Lua",
    ".ex": "Elixir", ".exs": "Elixir", ".tf": "Terraform", ".vue": "Vue", ".svelte": "Svelte",
    ".md": "Markdown", ".rst": "Docs", ".txt": "Docs", ".tex": "Docs",
    ".ipynb": "Notebook", ".pdf": "Document", ".docx": "Document", ".pptx": "Slides",
}

MAX_BYTES = 2_000_000        # a 2MB source file is generated, not written
MIN_BYTES = 40               # empty stubs carry nothing


@dataclass
class Document:
    id: str
    path: str
    root: str
    ext: str
    lang: str
    bytes: int
    sha256: str
    mtime: float
    kind: str = "text"          # text | rich | code
    extract: str = "pending"    # ok | pending | unsupported | error
    chars: int = 0
    source_type: str = "file"    # "file" for disk material, else the connector's type
    repo: str | None = None
    authored_by_me: bool | None = None
    my_line_share: float | None = None
    commits: int = 0
    last_commit: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def _sha256(path: Path, limit: int = 4_000_000) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        h.update(fh.read(limit))
    return h.hexdigest()[:16]


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(4096)
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    if not chunk:
        return False
    printable = sum(1 for b in chunk if 9 <= b <= 13 or 32 <= b <= 126 or b >= 128)
    return printable / len(chunk) < 0.85


def _excluded(path: Path, root: Path) -> str | None:
    rel = path.relative_to(root)
    for part in rel.parts[:-1]:
        if part in EXCLUDED_DIRS:
            return f"dir:{part}"
    if EXCLUDED_NAME_RE.search(path.name):
        return "generated-or-binary"
    if path.name.startswith(".") and path.suffix not in TEXT_EXT:
        return "dotfile"
    return None


# -- text extraction --------------------------------------------------------
_TAG = re.compile(r"<[^>]+>")


def _extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    return re.sub(r"\n{3,}", "\n\n", _TAG.sub("", xml)).strip()


def _extract_pptx(path: Path) -> str:
    chunks = []
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)):
            xml = z.read(name).decode("utf-8", "ignore")
            xml = re.sub(r"</a:p>", "\n", xml)
            chunks.append(f"--- {Path(name).stem} ---\n{_TAG.sub('', xml).strip()}")
    return "\n\n".join(chunks)


def _extract_ipynb(path: Path) -> str:
    nb = json.loads(path.read_text("utf-8", errors="ignore"))
    out = []
    for cell in nb.get("cells", []):
        src = "".join(cell.get("source", []))
        if not src.strip():
            continue
        out.append(f"# [{cell.get('cell_type')}]\n{src}")
    return "\n\n".join(out)


def _extract_pdf(path: Path) -> str:
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                           capture_output=True, timeout=120)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", "ignore")
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            raise RuntimeError("no pdf extractor (install pypdf or poppler-utils)")
    return "\n\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)


def extract_text(path: Path) -> tuple[str, str]:
    """Return (text, status)."""
    ext = path.suffix.lower()
    try:
        if ext == ".docx":
            return _extract_docx(path), "ok"
        if ext == ".pptx":
            return _extract_pptx(path), "ok"
        if ext == ".ipynb":
            return _extract_ipynb(path), "ok"
        if ext == ".pdf":
            return _extract_pdf(path), "ok"
        if ext == ".xlsx":
            return "", "unsupported"
        return path.read_text("utf-8", errors="replace"), "ok"
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the scan
        return "", f"error:{type(exc).__name__}"


# -- git awareness ----------------------------------------------------------
def staged_source_type(path: Path) -> str:
    """Imported material carries its connector's type; disk material is "file".

    Read from the file's own header first, so anything staged by an agent
    through `career stage` classifies the same as a built-in connector's
    output without needing an entry in the registry.

    Downstream this drives a separate budget quota, because a year of chat
    logs would otherwise outweigh every repository put together.
    """
    from .connectors import REGISTRY, source_type_of

    connector = source_type_of(path)
    if connector == "file":
        return "file"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(400)
        m = re.search(r"<!--\s*source_type=(\w+)", head)
        if m:
            return m.group(1)
    except OSError:
        pass
    known = REGISTRY.get(connector)
    return known.source_type if known else connector


def repo_root(path: Path) -> Path | None:
    cur = path if path.is_dir() else path.parent
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _git(repo: Path, *args: str, timeout: int = 120) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout if r.returncode == 0 else ""


def ownership_map(repo: Path, authors: list[str], since: str | None = None) -> dict[str, dict]:
    """Per-file: how many commits touched it, how many lines *you* changed.

    One `git log --numstat` walk for the whole repo, so this stays cheap even
    on large histories.
    """
    args = ["log", "--no-merges", "--numstat", "--format=%H%x00%an%x00%ae%x00%aI"]
    if since:
        args += [f"--since={since}"]
    out = _git(repo, *args)
    me = [a.lower() for a in authors]
    stats: dict[str, dict] = {}
    cur_mine, cur_date = False, None
    for line in out.splitlines():
        if "\x00" in line:
            _, name, email, date = line.split("\x00")
            cur_mine = any(m in name.lower() or m in email.lower() for m in me) if me else False
            cur_date = date
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, dele, fname = parts
        if fname.endswith("}") and "=>" in fname:      # rename notation
            fname = re.sub(r"\{.*=> (.*)\}", r"\1", fname).replace("//", "/")
        s = stats.setdefault(fname, {"commits": 0, "my_lines": 0, "all_lines": 0,
                                     "my_commits": 0, "last_commit": None})
        n = (int(add) if add.isdigit() else 0) + (int(dele) if dele.isdigit() else 0)
        s["commits"] += 1
        s["all_lines"] += n
        if cur_mine:
            s["my_commits"] += 1
            s["my_lines"] += n
            if not s["last_commit"]:
                s["last_commit"] = cur_date
    return stats


def git_identities(repo: Path) -> list[tuple[str, str, int]]:
    """(name, email, commits) -- feeds both author matching and the redactor."""
    out = _git(repo, "shortlog", "-sne", "--all", "--no-merges")
    ids = []
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*?)\s+<(.+?)>", line)
        if m:
            ids.append((m.group(2), m.group(3), int(m.group(1))))
    return ids


# -- the scan ---------------------------------------------------------------
def scan(roots: list[Path], authors: list[str], since: str | None = None,
         max_bytes: int = MAX_BYTES,
         exclude: list[Path] | None = None) -> tuple[list[Document], dict]:
    """``exclude`` keeps the pipeline from eating its own output.

    This is not hygiene, it is correctness. The workspace usually sits inside
    a scanned source tree, and `03_redacted/` holds redacted *copies* of the
    originals -- different bytes, so content hashing does not deduplicate
    them. Scanned, the same passage enters the corpus under two document ids,
    becomes two "independent sources", and manufactures a `pattern` out of a
    single observation. That defeats the one rule the profile's credibility
    rests on. Staging is the deliberate exception: it is imported material,
    not output.
    """
    docs: list[Document] = []
    seen_hashes: dict[str, str] = {}
    stats = {"seen": 0, "excluded": 0, "duplicates": 0, "kept": 0, "bytes_seen": 0,
             "bytes_kept": 0, "reasons": {}}
    own_cache: dict[Path, dict] = {}
    blocked = [Path(e).expanduser().resolve() for e in (exclude or [])]

    def is_output(path: Path) -> bool:
        for base in blocked:
            try:
                rel = path.resolve().relative_to(base)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] == STAGING_DIRNAME:
                return False       # imported material, not our output
            return True
        return False

    for root in roots:
        root = Path(root).expanduser().resolve()
        if not root.exists():
            stats["reasons"]["missing-root"] = stats["reasons"].get("missing-root", 0) + 1
            continue
        rr = repo_root(root)
        if rr and rr not in own_cache:
            own_cache[rr] = ownership_map(rr, authors, since)

        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            stats["seen"] += 1
            if is_output(path):
                stats["excluded"] += 1
                stats["reasons"]["pipeline-output"] = \
                    stats["reasons"].get("pipeline-output", 0) + 1
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            stats["bytes_seen"] += size

            reason = _excluded(path, root)
            ext = path.suffix.lower()
            if reason is None and ext not in TEXT_EXT and ext not in RICH_EXT:
                reason = "unknown-extension" if ext else ("no-extension"
                                                          if _looks_binary(path) else None)
            if reason is None and not (MIN_BYTES <= size <= max_bytes):
                reason = "too-small" if size < MIN_BYTES else "too-large"
            if reason is None and ext in TEXT_EXT and _looks_binary(path):
                reason = "binary-content"
            if reason:
                stats["excluded"] += 1
                stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
                continue

            digest = _sha256(path)
            if digest in seen_hashes:
                stats["duplicates"] += 1
                continue
            seen_hashes[digest] = str(path)

            rel = str(path.relative_to(root))
            doc = Document(
                id=digest, path=str(path), root=str(root), ext=ext,
                lang=LANG_BY_EXT.get(ext, "Other"), bytes=size, sha256=digest,
                mtime=path.stat().st_mtime,
                kind="rich" if ext in RICH_EXT else ("code" if ext not in
                     {".md", ".txt", ".rst", ".adoc", ".org", ".tex"} else "text"),
                source_type=staged_source_type(path),
            )
            if rr:
                doc.repo = rr.name
                key = str(path.relative_to(rr))
                s = own_cache[rr].get(key)
                if s:
                    doc.commits = s["commits"]
                    doc.last_commit = s["last_commit"]
                    total = s["all_lines"] or 1
                    doc.my_line_share = round(s["my_lines"] / total, 3)
                    doc.authored_by_me = s["my_lines"] > 0
                else:
                    doc.notes.append("untracked-or-outside-window")
            docs.append(doc)
            stats["kept"] += 1
            stats["bytes_kept"] += size
            _ = rel
    return docs, stats


def write_manifest(docs: list[Document], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for d in docs:
            fh.write(d.to_json() + "\n")


def read_manifest(path: Path) -> list[Document]:
    return [Document(**json.loads(line)) for line in
            Path(path).read_text("utf-8").splitlines() if line.strip()]
