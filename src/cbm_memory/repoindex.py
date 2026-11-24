"""Index a local git repo: commits, tree files, symbols (tree-sitter with
regex fallback), call sites, imports, and file text for full-text search.

Deliberately cheap: one pass, stdlib + tree-sitter grammars, SQLite upstream.
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import re
import subprocess

import cbm_memory.symbols_ts as symbols_ts

SYMBOL_RULES: dict[str, list[tuple[re.Pattern, str]]] = {}
for suffix, pattern, kind in (
    (".py", r"(?:async\s+)?def\s+([A-Za-z_]\w*)", "function"),
    (".py", r"\bclass\s+([A-Za-z_]\w*)", "class"),
    (".ts", r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", "function"),
    (".ts", r"(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", "class"),
    (".md", r"^#{1,6}\s+(.{1,120}?)(?:\s*\{[^}]*\})?\s*$", "heading"),
):
    SYMBOL_RULES.setdefault(suffix, []).append((re.compile(pattern, re.M), kind))

LANGS = {".py", ".ts", ".js", ".md", ".toml"}
# never index vendored/build/cache junk, tracked or not (T09)
IGNORE_PARTS = {
    "build",
    "dist",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    ".cbm",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".eggs",
    ".tox",
    "site-packages",
    ".obsidian",
}
MAX_TEXT_BYTES = 256_000  # skip full-text (FTS) for huge files

ALL_LANG_EXTS = set(LANGS) | set(symbols_ts.LANG_BY_EXT)


def _git(repo: pathlib.Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout


def head_and_dirty(repo: str | pathlib.Path) -> tuple[str, bool]:
    """(HEAD sha, working tree has uncommitted changes) - cache freshness check."""
    r = pathlib.Path(repo)
    sha = _git(r, "rev-parse", "HEAD").strip()
    dirty = bool(_git(r, "status", "--porcelain").strip())
    return sha, dirty


def _unquote(p: str) -> str:
    """git quotes exotic paths (C-style, inside double quotes); decode them."""
    if len(p) > 1 and p.startswith('"') and p.endswith('"'):
        body = p[1:-1]
        return re.sub(
            r"\\(?:([0-7]{3})|(.))",
            lambda m: (
                chr(int(m.group(1), 8))
                if m.group(1)
                else {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(
                    m.group(2), m.group(2)
                )
            ),
            body,
        )
    return p


def list_files(repo: pathlib.Path) -> list[str]:
    # core.quotePath=false keeps non-ASCII names raw; _unquote still handles
    # paths containing quotes/backslashes/control chars
    ls = _git(
        repo, "-c", "core.quotePath=false", "ls-files", "-co", "--exclude-standard"
    )
    out = []
    for p in ls.splitlines():
        p = _unquote(p)
        if p and not (set(pathlib.PurePosixPath(p).parts) & IGNORE_PARTS):
            out.append(p)
    return out


def scan_symbols(path: str, text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    for pattern, kind in SYMBOL_RULES.get(suffix, []):
        for m in pattern.finditer(text):
            out.append((kind, m.group(1).strip()))
    seen, uniq = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def _resolve_import(mod: str, from_path: str, files: set[str]) -> str | None:
    """Best-effort module string -> repo file path (relative js/ts, python paths)."""
    if not mod:
        return None
    from_posix = pathlib.PurePosixPath(from_path)
    if mod.startswith("."):  # js/ts relative import: ./x or ../x/y
        base = list(from_posix.parts[:-1])
        for seg in mod.split("/"):
            if seg == "..":
                if base:
                    base = base[:-1]
            elif seg in (".", ""):
                continue
            else:
                base.append(seg)
        stem = pathlib.PurePosixPath(*base)
        cands = [str(stem)]
        for ext in (".ts", ".tsx", ".js", ".jsx", ".py"):
            cands += [str(stem) + ext, str(pathlib.PurePosixPath(*base, "index" + ext))]
    elif from_path.endswith(".py"):  # python: "pkg.mod", ".mod", "..pkg.mod"
        lead = len(mod) - len(mod.lstrip("."))
        parts = [p for p in mod.lstrip(".").split(".") if p]
        base = list(from_posix.parts[:-1])
        for _ in range(max(0, lead - 1)):
            if base:
                base = base[:-1]
        cands: list[str] = []
        if not lead:  # absolute: resolve from the repo root first, file dir as fallback
            for i in range(len(parts), 0, -1):
                stem = pathlib.PurePosixPath(*parts[:i])
                cands += [
                    str(stem) + ".py",
                    str(pathlib.PurePosixPath(*stem.parts, "__init__.py")),
                ]
        for i in range(len(parts), 0, -1):  # deepest package first
            stem_parts = base + parts[:i]
            stem = pathlib.PurePosixPath(*stem_parts)
            cands += [
                str(stem) + ".py",
                str(pathlib.PurePosixPath(*stem_parts, "__init__.py")),
            ]
    else:
        return None
    for c in cands:
        if c in files and c != from_path:
            return c
    return None


def index_one(repo: pathlib.Path, path: str) -> dict:
    """Per-file pipeline shared by full index and reindex (T06)."""
    p = repo / path
    suffix = p.suffix.lower()
    entry: dict = {
        "path": path,
        "size": p.stat().st_size,
        "lang": suffix.lstrip(".") or "text",
        "symbols": [],
        "calls": [],
        "imports": [],
    }
    if not p.is_file():  # tracked dirs/symlinked dirs (e.g. serde/src/core) are skipped
        return entry
    if p.stat().st_size > 8_000_000:  # never read huge/binary blobs
        return entry
    raw = p.read_bytes()
    if p.stat().st_size <= MAX_TEXT_BYTES:
        entry["text"] = raw.decode("utf-8", errors="ignore")
    if suffix not in ALL_LANG_EXTS:
        return entry
    src = entry.get("text", raw.decode("utf-8", errors="ignore"))
    syms: list[tuple[str, str]]
    if suffix in symbols_ts.LANG_BY_EXT and symbols_ts.available():
        try:
            syms = symbols_ts.extract_symbols(path, raw)
            entry["calls"] = [
                {"caller": caller, "callee": callee}
                for caller, callee in symbols_ts.extract_calls(path, raw)
            ]
            entry["imports"] = symbols_ts.extract_imports(path, raw)
        except Exception:
            syms = scan_symbols(path, src)
    else:
        syms = scan_symbols(path, src)
    entry["symbols"] = [{"kind": k, "name": n} for k, n in syms]
    return entry


def index_repo(repo: str | pathlib.Path) -> dict:
    repo = pathlib.Path(repo).resolve()
    log = _git(
        repo, "log", r"--pretty=format:%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1e", "--name-only"
    )
    commits: list[dict] = []
    # Stream layout: HEAD\x1e\n<files of commit k>\nHEAD(k+1)\x1e\n<files(k+1)>...
    # i.e. each pretty header is terminated by \x1e and the files that follow
    # belong to the header seen BEFORE them; final trailing files close the last.
    last_head: tuple[str, list[str]] | None = None
    for chunk in log.split("\x1e"):
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            if "\x1f" in line:
                if last_head is not None:
                    head, files = last_head
                    sha, an, ae, at, subject = (head.split("\x1f") + [""])[
                        :5
                    ]  # empty subject: strip() drops the trailing \x1f
                    commits.append(
                        {
                            "sha": sha,
                            "author_name": an,
                            "author_email": ae,
                            "occurred_at": at,
                            "subject": subject,
                            "files_changed": "\n".join(files),
                        }
                    )
                last_head = (line, [])
            elif last_head is not None:
                last_head[1].append(line)
    if last_head is not None:
        head, files = last_head
        sha, an, ae, at, subject = (head.split("\x1f") + [""])[
            :5
        ]  # empty subject: strip() drops the trailing \x1f
        commits.append(
            {
                "sha": sha,
                "author_name": an,
                "author_email": ae,
                "occurred_at": at,
                "subject": subject,
                "files_changed": "\n".join(files),
            }
        )

    symbols: list[dict] = []
    tree: list[dict] = []
    texts: dict[str, str] = {}
    calls: list[dict] = []
    raw_imports: list[tuple[str, str]] = []  # (from_path, module string)
    files_set = set(list_files(repo))
    for path in sorted(files_set):
        entry = index_one(repo, path)
        tree.append(
            {
                "path": entry["path"],
                "size": entry["size"],
                "lang": entry["lang"],
                "symbols": entry["symbols"],
            }
        )
        if entry.get("text") is not None:
            texts[path] = entry["text"]
        for s in entry["symbols"]:
            symbols.append({"path": path, "kind": s["kind"], "name": s["name"]})
        calls.extend({"path": path, **c} for c in entry["calls"])
        raw_imports.extend((path, m) for m in entry["imports"])

    imports: list[dict] = []
    for from_path, mod in raw_imports:
        target = _resolve_import(mod, from_path, files_set)
        if target:
            imports.append({"path": from_path, "target": target})

    return {
        "repo": str(repo),
        "commits": commits,
        "tree": tree,
        "symbols": symbols,
        "calls": calls,
        "imports": imports,
        "texts": texts,
        "head_sha": _git(repo, "rev-parse", "HEAD").strip(),
        "indexed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
