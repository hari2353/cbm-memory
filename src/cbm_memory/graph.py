"""Git-nexus graph: nodes = author/commit/file/symbol, edges = authored /
modified / contains / calls / imports, stdlib sqlite. Read APIs back the MCP
tools; file text powers FTS5 search (T06); PageRank over import/call edges
boosts hub files (T07); reindex() refreshes incrementally from git (T06)."""
from __future__ import annotations

import datetime as _dt
import functools
import json
import math
import pathlib
import re
import sqlite3
import subprocess
import threading

from .semantic import cosine as _cos
from .semantic import embed as _embed
from .semantic import tokenize_words as _tokwords

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  repo TEXT, kind TEXT, name TEXT, path TEXT DEFAULT '', occurred_at TEXT DEFAULT '',
  meta TEXT DEFAULT '{}', key TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS edges (
  repo TEXT, relation TEXT, src TEXT, dst TEXT, occurred_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS edges_dst_rel ON edges(dst, relation);
CREATE INDEX IF NOT EXISTS edges_src_rel ON edges(src, relation);
CREATE INDEX IF NOT EXISTS nodes_kind_name ON nodes(kind, name);
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
PRAGMA user_version=1;
"""

DAMPING, ITERS, BOOST, SEM_W = 0.85, 20, 2.0, 3.0


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
        self._sem: tuple | None = None  # (file vecs, idf) - lazy, see _sem_state

    def warm(self) -> None:
        """Recompute derived state after loading a persisted graph (cache hit):
        PageRank is rebuilt, read caches + semantic vectors reset."""
        self._cache = {}
        self._sem = None
        self._sem = None
        for r in self.db.execute("SELECT repo FROM repo_meta"):
            self._recompute_rank(r["repo"])

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
        self._sem = None
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

    def _repo_for(self, repo: str) -> str | None:
        """Resolve a client's repo arg (repo key or startup path string) to the
        stored repo key; None means "all repos" ("*" or no match)."""
        if repo in ("*", ""):
            return None
        row = self.db.execute(
            "SELECT repo FROM repo_meta WHERE repo=? OR path=?", (repo, repo)).fetchone()
        return row["repo"] if row else None

    @locked
    @cached
    def explain_file(self, repo: str, path: str) -> dict:
        rows = self.db.execute(
            "SELECT key, repo, name FROM nodes WHERE kind='file' AND name=? ORDER BY repo",
            (path,)).fetchall()
        repo_key = self._repo_for(repo)
        chosen = next((r for r in rows if r["repo"] == repo_key), None)
        if chosen is None and rows:  # deterministic pick: highest pagerank wins
            rank = self._rank.get(repo_key or "", {})
            chosen = max(rows, key=lambda r: (rank.get(r["key"], 0.0), r["repo"]))
        if not chosen:
            return {"path": path, "symbols": [], "commits": []}
        symrows = self.db.execute(
            "SELECT name, meta AS kind FROM nodes WHERE kind='symbol' AND repo=? AND path=?",
            (chosen["repo"], path)).fetchall()
        commits = [dict(r) for r in self.db.execute(
            """SELECT n.name AS subject, n.meta, e.occurred_at
               FROM edges e JOIN nodes n ON n.key = e.src
               WHERE e.relation='modified' AND e.dst = ? ORDER BY e.occurred_at DESC LIMIT 20""",
            (chosen["key"],))]  # cap 20: hot files have hundreds of commits (token budget)
        total_commits = self.db.execute(
            "SELECT COUNT(*) FROM edges WHERE relation='modified' AND dst=?",
            (chosen["key"],)).fetchone()[0]
        commits = [{**c, **json.loads(c.pop("meta"))} for c in commits]
        related: list[dict] = []
        if len(rows) > 0:
            # same-named symbols in OTHER repos (paths may differ - that's the
            # point: "who else implements handler?"), weighted so a copy in the
            # other repo's hub file outranks a peripheral one, and generic names
            # (short, underscore-prefixed, or defined in >5 places) rank low
            # (Aider's specificity heuristic x our PageRank)
            names = [s["name"] for s in symrows]
            if names:
                qmarks = ",".join("?" * len(names))
                defn: dict[str, int] = {r["name"]: r["c"] for r in self.db.execute(
                    f"SELECT name, COUNT(DISTINCT key) AS c FROM nodes "
                    f"WHERE kind='symbol' AND name IN ({qmarks}) GROUP BY name", names)}
                cand = [r for r in self.db.execute(
                    f"""SELECT DISTINCT n.repo, n.name, n.path FROM nodes n
                        WHERE n.kind='symbol' AND n.repo<>?
                          AND n.name IN ({qmarks})
                          AND n.meta NOT IN ('heading', 'md-heading')""",
                    [chosen["repo"]] + names)]
                counts = {r["repo"]: r["c"] for r in self.db.execute(
                    "SELECT repo, COUNT(*) AS c FROM nodes WHERE kind='file' GROUP BY repo")}
                for r in cand:
                    # per-repo PageRank sums to 1: scale by repo size so hub
                    # files of big repos compare fairly against tiny repos
                    pr = self._rank.get(r["repo"], {}).get(
                        f"file|{r['repo']}|{r['path']}", 0.0) * counts.get(r["repo"], 1)
                    name = r["name"]
                    mul = 1.0
                    if len(name) >= 8 and ("_" in name or any(c.isupper() for c in name)):
                        mul *= 10.0  # long/snake/camel identifiers carry intent
                    if name.startswith("_"):
                        mul *= 0.1
                    if defn.get(name, 0) > 5:
                        mul *= 0.1   # defined in many places = generic, weak signal
                    related.append({"repo": r["repo"], "name": name, "path": r["path"],
                                    "rank": round(pr * mul, 6)})
                related.sort(key=lambda x: (-x["rank"], x["repo"], x["path"]))
                related = related[:10]
        return {"path": path, "repo": chosen["repo"],
                "repos": [r["repo"] for r in rows],
                "symbols": [dict(r) for r in symrows], "commits": commits,
                "total_commits": total_commits,
                "truncated": total_commits > len(commits),
                "related": related}

    @locked
    @cached
    def team_activity(self, repo: str) -> list[dict]:
        # group by author email: the same person across several indexed repos
        # merges into one row (author nodes are keyed per repo)
        rows = self.db.execute(
            "SELECT n.name AS author_name, COUNT(*) AS commits, "
            "COUNT(DISTINCT n.repo) AS repos FROM edges e "
            "JOIN nodes n ON n.key = e.src WHERE e.relation='authored' GROUP BY n.name "
            "ORDER BY commits DESC LIMIT 50")  # top contributors, bounded (token budget)
        return [dict(r) for r in rows]

    def _sem_state(self) -> tuple:
        """Lazy semantic index over all file bodies (invalidated on mutation)."""
        if self._sem is None:
            from collections import Counter

            texts = {f"file|{r['repo']}|{r['path']}": r["content"] or "" for r in self.db.execute(
                "SELECT repo, path, content FROM filetext")}
            df: Counter = Counter()
            for t in texts.values():
                df.update(set(_tokwords(t)))
            n = max(len(texts), 1)
            idf = {w: math.log((n + 1) / (c + 1)) + 1.0 for w, c in df.items()}
            self._sem = ({k: _embed(t, idf) for k, t in texts.items()}, idf)
        return self._sem

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
        repo_key = self._repo_for(repo)
        vecs, idf = self._sem_state()
        qv = _embed(query, idf)
        scored = []
        for r in self.db.execute("SELECT name, repo FROM nodes WHERE kind='file'"):
            toks = tokens.get((r["repo"], r["name"]), set()) | {r["name"].lower()}
            lexical = sum(hits(w, t) for w in words for t in toks)
            pr = rank.get(f"file|{r['repo']}|{r['name']}", 0.0)
            boost = 1.0 + BOOST * (pr / maxpr if maxpr else 0.0)
            sim = _cos(qv, vecs.get(f"file|{r['repo']}|{r['name']}", {}))
            mt = sorted({w for w in words for t in toks if hits(w, t)})[:2]  # why it matched
            scored.append((lexical * boost + SEM_W * sim, lexical, sim, mt, r))
        # specific repo: scope top_files to it; other repos' hits become `related`
        # (cross-pollination: one repo's context answers another repo's question)
        mine = [x for x in scored if repo_key is None or x[4]["repo"] == repo_key]
        others = [x for x in scored
                  if repo_key and x[4]["repo"] != repo_key and (x[1] > 0 or x[2] >= 0.15)]
        top = [{"path": r["name"], "repo": r["repo"],
                "rank": round(s, 4),
                **({"match": "semantic"} if lex == 0 else {}),
                **({"via": mt} if mt else {})}
               for s, lex, sim, mt, r in sorted(mine, key=lambda x: -x[0])[:top_k] if s > 0]
        # compound output: ship each hit file's top symbol names inline (Aider
        # ships signatures in its map) - saves an explain_file round-trip
        for t in top:
            t["symbols"] = [r[0] for r in self.db.execute(
                "SELECT name FROM nodes WHERE kind='symbol' AND repo=? AND path=? "
                "AND meta NOT IN ('heading','md-heading') LIMIT 5",
                (t["repo"], t["path"]))]
        related = [{"path": r["name"], "repo": r["repo"], "rank": round(s, 4),
                    **({"via": mt} if mt else {})}
                    for s, lex, sim, mt, r in sorted(others, key=lambda x: -x[0])[:3]]
        recent = [dict(r) for r in self.db.execute(
            "SELECT n.name AS subject, e.occurred_at FROM edges e JOIN nodes n ON n.key = e.dst "
            "WHERE e.relation='authored' ORDER BY e.occurred_at DESC LIMIT ?", (top_k,))]
        return {"repo": repo, "top_files": top, "related": related, "recent_commits": recent}

    def _grouped(self, rows: list[dict], field: str) -> list[dict]:
        """Collapse flat rows into per-(repo,path) groups: repo paths repeat on
        every row otherwise (the biggest repeated-field cost in responses)."""
        out: list[dict] = []
        seen: dict[tuple, dict] = {}
        for r in rows:
            k = (r["repo"], r["path"])
            g = seen.get(k)
            if g is None:
                g = {"repo": r["repo"], "path": r["path"], field: []}
                seen[k] = g
                out.append(g)
            g[field].append(r["caller"] if field == "callers" else
                            {"name": r["name"], "kind": r.get("kind")})
        return out

    @locked
    @cached
    def search_codebase_memory(self, repo: str, text: str) -> list[dict]:
        rows = [dict(r) for r in self.db.execute(
            "SELECT DISTINCT name, path, repo, meta AS kind FROM nodes "
            "WHERE kind='symbol' AND name LIKE ? "
            "ORDER BY (meta='heading') ASC, name LIMIT 20", (f"%{text}%",))]
        if not rows:
            # T06: full-text over file bodies, then offline semantic similarity
            # (hashed n-gram TF-IDF) when neither symbol names nor FTS matched.
            words = [w for w in re.findall(r"\w+", text) if w]
            if words:
                try:
                    fpaths = [(r["repo"], r["path"]) for r in self.db.execute(
                        "SELECT f.repo, f.path FROM filetext_fts "
                        "JOIN filetext f ON f.id = filetext_fts.rowid "
                        "WHERE filetext_fts MATCH ? ORDER BY rank LIMIT 5",
                        (" OR ".join(w + "*" for w in words),)).fetchall()]
                except sqlite3.OperationalError:
                    fpaths = []
                for rp, p in fpaths:
                    rows += [dict(r) for r in self.db.execute(
                        "SELECT DISTINCT name, path, repo, meta AS kind FROM nodes "
                        "WHERE kind='symbol' AND repo=? AND path=?", (rp, p))]
            if not rows and text.strip():
                vecs, idf = self._sem_state()
                qv = _embed(text, idf)
                top = sorted((( _cos(qv, v), k) for k, v in vecs.items() if v),
                             key=lambda x: (-x[0], x[1]))[:5]
                for sim, k in top:
                    if sim < 0.05:
                        break
                    _, rp, p = k.split("|", 2)
                    rows += [dict(r) for r in self.db.execute(
                        "SELECT DISTINCT name, path, repo, meta AS kind FROM nodes "
                        "WHERE kind='symbol' AND repo=? AND path=?", (rp, p))]
        return self._grouped(rows[:20], "symbols")[:20]

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
                    item = {"caller": parts[3], "path": parts[2], "repo": parts[1]}
                elif parts[0] == "file":
                    item = {"caller": "", "path": parts[2], "repo": parts[1]}
                else:
                    continue
                tag = (item["caller"], item["path"], item["repo"])
                if tag not in seen:
                    seen.add(tag)
                    out.append(item)
        # cap 50: popular symbols have hundreds of callers (token budget);
        # grouped per file so repo+path aren't repeated on every caller row
        return self._grouped(
            sorted(out, key=lambda x: (x["repo"], x["path"], x["caller"]))[:50],
            "callers")

    @locked
    @cached
    def change_impact(self, repo: str, path: str) -> dict:
        # repo param is advisory (clients pass their startup string, e.g. ".");
        # resolve the file node by path, preferring the requested repo.
        rows = self.db.execute(
            "SELECT key, repo FROM nodes WHERE kind='file' AND name=? ORDER BY repo",
            (path,)).fetchall()
        repo_key = self._repo_for(repo)
        frow = next((r for r in rows if r["repo"] == repo_key),
                    max(rows, key=lambda r: (self._rank.get(repo_key or "", {}).get(r["key"], 0.0), r["repo"]))
                    if rows else None)
        if not frow:
            return {"repo": repo, "root": path, "symbols": [], "impacted": []}
        fkey, real_repo = frow["key"], frow["repo"]
        syms = [dict(r) for r in self.db.execute(
            "SELECT name, meta AS kind FROM nodes WHERE kind='symbol' AND repo=? AND path=?",
            (real_repo, path))]
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
            rows = self.db.execute(
                "SELECT key, repo FROM nodes WHERE kind='file' AND name=? ORDER BY repo",
                (path,)).fetchall()
            repo_key = self._repo_for(repo)
            frow = next((r for r in rows if r["repo"] == repo_key), rows[0] if rows else None)
            if not frow:
                out_files.append({"path": path, "symbols": [], "recent_commits": []})
                continue
            syms = [dict(r) for r in self.db.execute(
                "SELECT name, meta AS kind FROM nodes WHERE kind='symbol' AND repo=? AND path=?",
                (frow["repo"], path))]
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
        self.db.execute("DELETE FROM nodes WHERE kind='symbol' AND repo=? AND path=?",
                        (repo, path))  # scoped: another repo may own the same path
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
        changed_files: list[str] = []
        last_sha = None
        for row in rows or []:
            applied, chg_files = self._reindex_one(row["path"], row["sha"])
            total += applied
            changed_files += chg_files
            last_sha = self.db.execute(
                "SELECT sha FROM repo_meta WHERE path=?", (row["path"],)).fetchone()["sha"]
        # honest truncation: say what changed and cap the list (token budget)
        return {"repo": repo, "changed": total, "indexed_commit": last_sha,
                "changed_files": changed_files[:20],
                "truncated": len(changed_files) > 20}

    def _reindex_one(self, repo_path_str: str, old_sha: str) -> tuple[int, list[str]]:
        from .repoindex import IGNORE_PARTS, index_one, list_files

        repo_path = pathlib.Path(repo_path_str)
        repo_key = str(repo_path)
        self._cache = {}
        self._sem = None

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
        return applied, sorted(changed)

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
