"""Git-nexus graph: nodes = author/commit/file/symbol, edges = authored /
modified / contains / calls / imports, stdlib sqlite. Read APIs back the MCP
tools; file text powers FTS5 search (T06); PageRank over import/call edges
boosts hub files (T07); reindex() refreshes incrementally from git (T06)."""
from __future__ import annotations

import datetime as _dt
import functools
import json
import pathlib
import re
import sqlite3
import subprocess
import threading

_SCHEMA = """
CREATE TABLE nodes (
  repo TEXT, kind TEXT, name TEXT, path TEXT DEFAULT '', occurred_at TEXT DEFAULT '',
  meta TEXT DEFAULT '{}', key TEXT PRIMARY KEY
);
CREATE TABLE edges (
  repo TEXT, relation TEXT, src TEXT, dst TEXT, occurred_at TEXT DEFAULT ''
);
CREATE INDEX edges_dst_rel ON edges(dst, relation);
CREATE INDEX edges_src_rel ON edges(src, relation);
CREATE INDEX nodes_kind_name ON nodes(kind, name);
CREATE TABLE IF NOT EXISTS filetext (
  id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT, path TEXT, content TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS filetext_fts USING fts5(
  content, content='filetext', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS filetext_ai AFTER INSERT ON filetext BEGIN
  INSERT INTO filetext_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS filetext_ad AFTER DELETE ON filetext BEGIN
  INSERT INTO filetext_fts(filetext_fts, rowid, content)
  VALUES ('delete', old.id, old.content);
END;
CREATE TABLE IF NOT EXISTS repo_meta (
  repo TEXT PRIMARY KEY, path TEXT, sha TEXT, indexed_at TEXT
);
"""

DAMPING, ITERS, BOOST = 0.85, 20, 2.0


def locked(fn):
    """Serialize DB access: MCP v2 runs tool bodies in worker threads."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    return wrapper


def cached(fn):
    """Memoize read results (agent context tokens: repeated identical calls
    replay the same object). Invalidated on every mutation (load/reindex)."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        key = (fn.__name__, a, tuple(sorted(kw.items())))
        if key in self._cache:
            return self._cache[key]
        out = fn(self, *a, **kw)
        self._cache[key] = out
        return out
    return wrapper


def _file_key(key: str) -> str | None:
    """Map a node key to its file key (symbol|repo|path|name -> file|repo|path)."""
    parts = key.split("|")
    if parts[0] == "file":
        return key
    if parts[0] == "symbol" and len(parts) >= 4:
        return "|".join(parts[:3])
    return None


class GitNexusGraph:
    def __init__(self, path: str = ":memory:"):
        # MCP v2 runs tool bodies in worker threads; keep one shared conn + lock
        self.db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self._rank: dict[str, dict[str, float]] = {}  # repo -> file key -> pagerank
        self._cache: dict = {}

    # ---------- write ----------
    @locked
    def load(self, index: dict) -> None:
        repo = index["repo"]
        for t in index["tree"]:
            key = f"file|{repo}|{t['path']}"
            self.db.execute(
                "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?)",
                (repo, "file", t["path"], t["path"], "", json.dumps({"size": t["size"], "lang": t["lang"]}), key))
            for s in t.get("symbols", []):
                skey = f"symbol|{repo}|{t['path']}|{s['name']}"
                self.db.execute(
                    "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?)",
                    (repo, "symbol", s["name"], t["path"], "", s["kind"], skey))
                self.db.execute("INSERT OR REPLACE INTO edges VALUES (?,?,?,?,?)",
                                (repo, "contains", key, skey, ""))
        for path, content in index.get("texts", {}).items():
            self.db.execute("INSERT INTO filetext (repo, path, content) VALUES (?,?,?)",
                            (repo, path, content))
        for c in index["commits"]:
            self._load_commit(repo, c)
        for c in index.get("calls", []):
            self._store_call(repo, c)
        for imp in index.get("imports", []):
            self._store_import(repo, imp["path"], imp["target"])
        self.db.execute("INSERT OR REPLACE INTO repo_meta VALUES (?,?,?,?)",
                        (repo, repo, index.get("head_sha", ""), index.get("indexed_at", "")))
        self._recompute_rank(repo)
        self._cache = {}
        self.db.commit()

    def _load_commit(self, repo: str, c: dict) -> None:
        ckey = f"commit|{repo}|{c['sha'][:12]}"
        akey = f"author|{repo}|{c['author_email']}"
        self.db.execute(
            "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?)",
            (repo, "commit", c["subject"], "", c["occurred_at"],
             json.dumps({"author_name": c["author_name"], "author_email": c["author_email"]}), ckey))
        self.db.execute(
            "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?)",
            (repo, "author", c["author_name"], "", "", "{}", akey))
        self.db.execute("INSERT OR REPLACE INTO edges VALUES (?,?,?,?,?)",
                        (repo, "authored", akey, ckey, c["occurred_at"]))
        for path in [p for p in c["files_changed"].split("\n") if p]:
            self.db.execute("INSERT OR REPLACE INTO edges VALUES (?,?,?,?,?)",
                            (repo, "modified", ckey, f"file|{repo}|{path}", c["occurred_at"]))

    def _store_call(self, repo: str, c: dict) -> None:
        """Name-level edge; a callee name matching several symbols links all of
        them (recall over precision — documented v1 trade-off)."""
        rows = self.db.execute(
            "SELECT key FROM nodes WHERE kind='symbol' AND name=? LIMIT 5",
            (c["callee"],)).fetchall()
        src = (f"symbol|{repo}|{c['path']}|{c['caller']}" if c.get("caller")
               else f"file|{repo}|{c['path']}")
        for r in rows:
            self.db.execute("INSERT OR REPLACE INTO edges VALUES (?,?,?,?,?)",
                            (repo, "calls", src, r["key"], ""))

    def _store_import(self, repo: str, from_path: str, target: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO edges VALUES (?,?,?,?,?)",
                        (repo, "imports",
                         f"file|{repo}|{from_path}", f"file|{repo}|{target}", ""))

    # ---------- rank (T07) ----------
    def _recompute_rank(self, repo: str) -> None:
        files = [r["key"] for r in self.db.execute(
            "SELECT key FROM nodes WHERE kind='file' AND repo=?", (repo,)).fetchall()]
        fset = set(files)
        out: dict[str, set[str]] = {k: set() for k in files}
        for rel in ("imports", "calls"):
            for r in self.db.execute(
                    "SELECT src, dst FROM edges WHERE relation=? AND repo=?", (rel, repo)):
                s, d = _file_key(r["src"]), _file_key(r["dst"])
                if s and d and s != d and s in fset and d in fset:
                    out[s].add(d)
        n = len(files)
        if not n:
            self._rank.pop(repo, None)
            return
        pr = {k: 1.0 / n for k in files}
        for _ in range(ITERS):
            new = {k: (1 - DAMPING) / n for k in files}
            for k in files:
                if out[k]:
                    share = DAMPING * pr[k] / len(out[k])
                    for d in out[k]:
                        new[d] += share
                else:
                    new[k] += DAMPING * pr[k]
            pr = new
        self._rank[repo] = pr

    def _merged_rank(self) -> dict[str, float]:
        rank: dict[str, float] = {}
        for m in self._rank.values():
            rank.update(m)
        return rank

    # ---------- read ----------
    @locked
    def counts(self) -> list[tuple[str, int]]:
        pairs = []
        for res, label in ((self.db.execute(
                "SELECT kind, COUNT(*) FROM nodes GROUP BY kind"), "node"),
                (self.db.execute(
                    "SELECT relation, COUNT(*) FROM edges GROUP BY relation"), "edge")):
            for k, n in res.fetchall():
                pairs.append((k, n))
        return sorted(pairs)

    @locked
    @cached
    def explain_file(self, repo: str, path: str) -> dict:
        frow = self.db.execute(
            "SELECT key, name FROM nodes WHERE kind='file' AND name=?", (path,)).fetchone()
        if not frow:
            return {"path": path, "symbols": [], "commits": []}
        symrows = self.db.execute(
            "SELECT name, meta AS kind, path FROM nodes WHERE kind='symbol' AND path=?",
            (path,)).fetchall()
        commits = [dict(r) for r in self.db.execute(
            """SELECT n.name AS subject, n.meta, e.occurred_at
               FROM edges e JOIN nodes n ON n.key = e.src
               WHERE e.relation='modified' AND e.dst = ? ORDER BY e.occurred_at DESC LIMIT 20""",
            (frow["key"],))]  # cap 20: hot files have hundreds of commits (token budget)
        commits = [{**c, **json.loads(c.pop("meta"))} for c in commits]
        return {"path": path, "symbols": [dict(r) for r in symrows], "commits": commits}

    @locked
    @cached
    def team_activity(self, repo: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT n.name AS author_name, COUNT(*) AS commits FROM edges e "
            "JOIN nodes n ON n.key = e.src WHERE e.relation='authored' GROUP BY e.src "
            "ORDER BY commits DESC LIMIT 50")  # top contributors, bounded (token budget)
        return [dict(r) for r in rows]

    @locked
    @cached
    def get_codebase_context(self, repo: str, query: str, top_k: int = 5) -> dict:
        words = query.lower().split()
        tokens: dict[tuple, set[str]] = {}
        for r in self.db.execute(
                "SELECT repo, name, path, meta AS kind FROM nodes WHERE kind='symbol'"):
            tokens.setdefault((r["repo"], r["path"]), set()).add(r["name"].lower())

        def hits(word: str, tok: str) -> float:
            # exact symbol-name match outranks substring containment
            if word == tok:
                return 2.0
            return 1.0 if (word in tok or tok in word) else 0.0

        rank = self._merged_rank()
        maxpr = max(rank.values()) if rank else 0.0
        scored = []
        for r in self.db.execute("SELECT name, repo FROM nodes WHERE kind='file'"):
            toks = tokens.get((r["repo"], r["name"]), set()) | {r["name"].lower()}
            lexical = sum(hits(w, t) for w in words for t in toks)
            pr = rank.get(f"file|{r['repo']}|{r['name']}", 0.0)
            boost = 1.0 + BOOST * (pr / maxpr if maxpr else 0.0)
            scored.append((lexical * boost, r))
        top = [{"path": r["name"], "rank": s}
               for s, r in sorted(scored, key=lambda x: -x[0])[:top_k] if s > 0]
        recent = [dict(r) for r in self.db.execute(
            "SELECT n.name AS subject, e.occurred_at FROM edges e JOIN nodes n ON n.key = e.dst "
            "WHERE e.relation='authored' ORDER BY e.occurred_at DESC LIMIT ?", (top_k,))]
        return {"repo": repo, "top_files": top, "recent_commits": recent}

    @locked
    @cached
    def search_codebase_memory(self, repo: str, text: str) -> list[dict]:
        rows = [dict(r) for r in self.db.execute(
            "SELECT name, path, meta AS kind FROM nodes "
            "WHERE kind='symbol' AND name LIKE ? LIMIT 20", (f"%{text}%",))]
        if rows:
            return rows
        # T06: fall through to full-text search over file bodies; return the
        # symbols of the best-matching files (same wire shape).
        words = [w for w in re.findall(r"\w+", text) if w]
        if not words:
            return []
        try:
            fpaths = [r["path"] for r in self.db.execute(
                "SELECT f.path FROM filetext_fts JOIN filetext f ON f.id = filetext_fts.rowid "
                "WHERE filetext_fts MATCH ? ORDER BY rank LIMIT 5",
                (" OR ".join(w + "*" for w in words),)).fetchall()]
        except sqlite3.OperationalError:
            return []
        out: list[dict] = []
        for p in fpaths:
            out += [dict(r) for r in self.db.execute(
                "SELECT name, path, meta AS kind FROM nodes WHERE kind='symbol' AND path=?",
                (p,))]
        return out[:20]

    @locked
    @cached
    def find_callers(self, repo: str, symbol: str) -> list[dict]:
        keys = [r["key"] for r in self.db.execute(
            "SELECT key FROM nodes WHERE kind='symbol' AND name=?", (symbol,)).fetchall()]
        seen, out = set(), []
        for k in keys:
            for r in self.db.execute(
                    "SELECT src FROM edges WHERE relation='calls' AND dst=?", (k,)):
                parts = r["src"].split("|")
                if parts[0] == "symbol" and len(parts) >= 4:
                    item = {"caller": parts[3], "path": parts[2]}
                elif parts[0] == "file":
                    item = {"caller": "", "path": parts[2]}
                else:
                    continue
                tag = (item["caller"], item["path"])
                if tag not in seen:
                    seen.add(tag)
                    out.append(item)
        # cap 50: popular symbols have hundreds of callers (token budget)
        return sorted(out, key=lambda x: (x["path"], x["caller"]))[:50]

    @locked
    @cached
    def change_impact(self, repo: str, path: str) -> dict:
        # repo param is advisory (clients pass their startup string, e.g. ".");
        # the graph is keyed by resolved path, so resolve the file node by path.
        frow = self.db.execute(
            "SELECT key, repo FROM nodes WHERE kind='file' AND name=? LIMIT 1",
            (path,)).fetchone()
        if not frow:
            return {"repo": repo, "root": path, "symbols": [], "impacted": []}
        fkey, real_repo = frow["key"], frow["repo"]
        syms = [dict(r) for r in self.db.execute(
            "SELECT name, meta AS kind FROM nodes WHERE kind='symbol' AND path=?", (path,))]
        frontier = [(fkey, 0)] + [
            (f"symbol|{real_repo}|{path}|{s['name']}", 0) for s in syms]
        seen = {k for k, _ in frontier}
        impacted: dict[str, int] = {}
        truncated = False
        qi = 0
        while qi < len(frontier):
            key, depth = frontier[qi]
            qi += 1
            if depth >= 3:
                continue
            for r in self.db.execute(
                    "SELECT src FROM edges WHERE relation IN ('calls','imports') AND dst=?",
                    (key,)):
                src = r["src"]
                if src in seen:
                    continue
                seen.add(src)
                fpath_key = _file_key(src)
                if not fpath_key:
                    continue
                fpath = fpath_key.split("|", 2)[2]
                if fpath == path:
                    continue
                if fpath not in impacted or depth + 1 < impacted[fpath]:
                    impacted[fpath] = depth + 1
                frontier.append((fpath_key, depth + 1))
        ordered = sorted(impacted.items(), key=lambda x: x[1])[:50]  # token budget
        truncated = len(impacted) > len(ordered)
        return {"repo": repo, "root": path, "symbols": syms,
                "impacted": [{"path": p, "depth": d} for p, d in ordered],
                "truncated": truncated}

    @locked
    def detect_changes(self, repo: str, changed_files: list[str]) -> dict:
        """Map a set of changed files to their symbols + recent commits (blast radius)."""
        out_files = []
        for path in changed_files:
            frow = self.db.execute(
                "SELECT key, name FROM nodes WHERE kind='file' AND name=?", (path,)).fetchone()
            if not frow:
                out_files.append({"path": path, "symbols": [], "recent_commits": []})
                continue
            syms = [dict(r) for r in self.db.execute(
                "SELECT name, meta AS kind FROM nodes WHERE kind='symbol' AND path=?",
                (path,))]
            commits = [dict(r) for r in self.db.execute(
                "SELECT n.name AS subject, e.occurred_at FROM edges e "
                "JOIN nodes n ON n.key=e.src WHERE e.relation='modified' AND e.dst=? "
                "ORDER BY e.occurred_at DESC LIMIT 5", (frow["key"],))]
            out_files.append({"path": path, "symbols": syms, "recent_commits": commits})
        return {"repo": repo, "changed": out_files,
                "total_symbols": sum(len(f["symbols"]) for f in out_files)}

    # ---------- incremental refresh (T06) ----------
    def _forget(self, repo: str, path: str) -> None:
        fk = f"file|{repo}|{path}"
        sym_like = f"symbol|{repo}|{path}|%"
        self.db.execute("DELETE FROM nodes WHERE key=?", (fk,))
        self.db.execute("DELETE FROM nodes WHERE kind='symbol' AND path=?", (path,))
        self.db.execute("DELETE FROM filetext WHERE repo=? AND path=?", (repo, path))
        self.db.execute("DELETE FROM edges WHERE relation='contains' AND src=?", (fk,))
        self.db.execute("DELETE FROM edges WHERE relation='imports' AND (src=? OR dst=?)", (fk, fk))
        self.db.execute(
            "DELETE FROM edges WHERE relation='calls' AND (src=? OR src LIKE ? OR dst LIKE ?)",
            (fk, sym_like, sym_like))
        # 'modified' history is kept: deleted files stay remembered.

    @locked
    def reindex(self, repo: str) -> dict:
        if repo in ("*", ""):
            rows = self.db.execute("SELECT path, sha FROM repo_meta").fetchall()
        else:  # match repo key exactly, or the client's startup string (path)
            rows = self.db.execute(
                "SELECT path, sha FROM repo_meta WHERE repo=? OR path=?",
                (repo, repo)).fetchall() or self.db.execute(
                "SELECT path, sha FROM repo_meta LIMIT 1").fetchall()
        total = 0
        last_sha = None
        for row in rows or []:
            total += self._reindex_one(row["path"], row["sha"])
            last_sha = self.db.execute(
                "SELECT sha FROM repo_meta WHERE path=?", (row["path"],)).fetchone()["sha"]
        return {"repo": repo, "changed": total, "indexed_commit": last_sha}

    def _reindex_one(self, repo_path_str: str, old_sha: str) -> int:
        from .repoindex import IGNORE_PARTS, index_one, list_files

        repo_path = pathlib.Path(repo_path_str)
        repo_key = str(repo_path)
        self._cache = {}

        def git(*a: str) -> str:
            return subprocess.run(["git", *a], cwd=repo_path, check=True,
                                  capture_output=True, text=True).stdout

        new_sha = git("rev-parse", "HEAD").strip()
        changed: dict[str, str] = {}
        if old_sha and old_sha != new_sha:
            for line in git("diff", "--name-status", old_sha, new_sha).splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    changed[parts[1].strip()] = parts[0]
        for line in git("status", "--porcelain").splitlines():
            code, p = line[:2], line[3:].strip()
            if p:
                changed.setdefault(p, code.strip())

        files_set = set(list_files(repo_path))
        applied = 0
        for p in list(changed):
            if set(pathlib.PurePosixPath(p).parts) & IGNORE_PARTS:
                changed.pop(p, None)
                continue
            self._forget(repo_key, p)
            if p in files_set:
                entry = index_one(repo_path, p)
                entry["path"] = p
                self._load_entry(repo_key, entry, files_set)
            applied += 1

        if old_sha != new_sha and applied:
            info = git("log", "-1", r"--pretty=%an%x1f%ae%x1f%aI%x1f%s").strip()
            an, ae, at, subject = (info.split("\x1f") + [""])[:4]
            self._load_commit(repo_key, {"sha": new_sha, "author_name": an, "author_email": ae,
                                         "occurred_at": at, "subject": subject,
                                         "files_changed": "\n".join(changed)})
        self.db.execute("UPDATE repo_meta SET sha=?, indexed_at=? WHERE path=?",
                        (new_sha, _dt.datetime.now(_dt.timezone.utc).isoformat(), repo_key))
        self._recompute_rank(repo_key)
        self.db.commit()
        return applied

    def _load_entry(self, repo: str, entry: dict, files_set: set[str]) -> None:
        """Current-state write for one file (reindex path)."""
        from .repoindex import _resolve_import

        path = entry["path"]
        fk = f"file|{repo}|{path}"
        self.db.execute(
            "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?)",
            (repo, "file", path, path, "",
             json.dumps({"size": entry["size"], "lang": entry["lang"]}), fk))
        for s in entry["symbols"]:
            sk = f"symbol|{repo}|{path}|{s['name']}"
            self.db.execute(
                "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?)",
                (repo, "symbol", s["name"], path, "", s["kind"], sk))
            self.db.execute("INSERT OR REPLACE INTO edges VALUES (?,?,?,?,?)",
                            (repo, "contains", fk, sk, ""))
        if entry.get("text") is not None:
            self.db.execute("INSERT INTO filetext (repo, path, content) VALUES (?,?,?)",
                            (repo, path, entry["text"]))
        for c in entry["calls"]:
            self._store_call(repo, {"path": path, "caller": c["caller"], "callee": c["callee"]})
        for m in entry["imports"]:
            target = _resolve_import(m, path, files_set)
            if target:
                self._store_import(repo, path, target)
