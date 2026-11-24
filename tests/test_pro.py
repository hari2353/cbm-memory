"""Pro tier v1: offline ed25519 license gate (RFC 8032, stdlib only),
free-tier fleet cap + unlock, watch tick, architecture_report, secure preset."""

import json

from git_fixtures import run_git

from cbm_memory import licensing, mcp_server
from cbm_memory.graph import GitNexusGraph
from cbm_memory.repoindex import index_repo


def make_repo(tmp_path, files: dict[str, str], commit: str = "feat: initial"):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    run_git(root, "init", "-q", "-b", "main")
    for path, text in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", commit)
    return root


# ---------- ed25519 core: RFC 8032 section 7.1 vectors ----------

SEED1 = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
PUB1 = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
MSG1 = b""
SIG1 = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)

SEED3 = bytes.fromhex(
    "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7"
)
PUB3 = bytes.fromhex("fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025")
MSG3 = bytes.fromhex("af82")
SIG3 = bytes.fromhex(
    "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
    "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"
)


def test_ed25519_rfc8032_vectors_sign_and_verify():
    assert licensing.ed25519_sign(SEED1, MSG1) == SIG1
    assert licensing.ed25519_sign(SEED3, MSG3) == SIG3
    assert licensing.ed25519_verify(PUB1, MSG1, SIG1) is True
    assert licensing.ed25519_verify(PUB3, MSG3, SIG3) is True


def test_ed25519_rejects_tampered_and_malformed():
    bad = bytearray(SIG3)
    bad[0] ^= 1
    assert licensing.ed25519_verify(PUB3, MSG3, bytes(bad)) is False
    assert licensing.ed25519_verify(PUB3, b"other", SIG3) is False
    assert licensing.ed25519_verify(PUB3, MSG1, SIG1) is False  # wrong key


# ---------- license format: payload.sig, offline verify ----------


def test_license_issue_and_check_roundtrip():
    seed = SEED1
    pub = licensing.secret_to_public(seed)
    lic = licensing.issue({"tier": "pro", "note": "test@x"}, seed)
    out = licensing.check(lic, pub)
    assert out == {"tier": "pro", "note": "test@x"}
    # tampered payload -> invalid
    body, sig = lic.rsplit(".", 1)
    raw = bytearray(licensing._b64d(body))
    raw[0:1] = b"2" if raw[0:1] != b"2" else b"3"
    assert licensing.check(licensing._b64e(bytes(raw)) + "." + sig, pub) is None
    # wrong verification key -> invalid
    assert licensing.check(lic, licensing.secret_to_public(SEED3)) is None
    # garbage input -> invalid, never raises
    assert licensing.check("garbage", pub) is None
    assert licensing.check("", pub) is None


def test_license_expiry_enforced():
    seed = SEED1
    pub = licensing.secret_to_public(seed)
    past = licensing.issue({"tier": "pro", "exp": 1000000000}, seed)
    future = licensing.issue({"tier": "pro", "exp": 4000000000}, seed)
    assert licensing.check(past, pub) is None  # expired
    assert licensing.check(future, pub)["tier"] == "pro"
    assert licensing.check(licensing.issue({"tier": "pro"}, seed), pub)["tier"] == "pro"


def test_license_rejects_non_canonical_base64():
    # same decoded sig bytes, dirty low bits in the last b64 char -> invalid
    # (RFC 4648 canonical encoding; blocks meaningless-but-malleable strings)
    pub = licensing.secret_to_public(SEED1)
    lic = licensing.issue({"tier": "pro", "note": "m"}, SEED1)
    alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    sraw = licensing._b64d(lic.rsplit(".", 1)[1])
    idx = alpha.index(lic[-1])
    tampered = lic[:-1] + alpha[idx ^ 1]  # high 2 bits (the valid ones) unchanged
    assert licensing._b64d(tampered.rsplit(".", 1)[1]) == sraw  # identical bytes
    assert licensing.check(tampered, pub) is None


# ---------- fleet cap: free truncates with notice, license unlocks ----------


def test_free_cap_truncates_at_five_with_notice(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CBM_LICENSE", raising=False)
    repos = [
        make_repo(tmp_path / f"r{i}", {"f.py": f"def s{i}():\n    pass\n"})
        for i in range(6)
    ]
    g = mcp_server.index_repositories(
        [str(r) for r in repos], cache_dir=str(tmp_path / "cache")
    )
    have = g.db.execute("SELECT COUNT(*) AS c FROM repo_meta").fetchone()[0]
    assert have == licensing.FREE_MAX_REPOS == 5
    assert "CBM_LICENSE" in capsys.readouterr().err


def test_license_unlocks_unlimited_repos(tmp_path, monkeypatch):
    seed = SEED1
    monkeypatch.setattr(licensing, "PUBKEY", licensing.secret_to_public(seed))
    monkeypatch.setenv("CBM_LICENSE", licensing.issue({"tier": "pro"}, seed))
    repos = [
        make_repo(tmp_path / f"r{i}", {"f.py": f"def s{i}():\n    pass\n"})
        for i in range(6)
    ]
    g = mcp_server.index_repositories(
        [str(r) for r in repos], cache_dir=str(tmp_path / "cache")
    )
    have = g.db.execute("SELECT COUNT(*) AS c FROM repo_meta").fetchone()[0]
    assert have == 6


def test_invalid_license_behaves_as_free(tmp_path, monkeypatch):
    monkeypatch.setattr(licensing, "PUBKEY", licensing.secret_to_public(SEED1))
    monkeypatch.setenv("CBM_LICENSE", "not.a.license")
    repos = [
        make_repo(tmp_path / f"r{i}", {"f.py": "def s():\n    pass\n"})
        for i in range(6)
    ]
    g = mcp_server.index_repositories(
        [str(r) for r in repos], cache_dir=str(tmp_path / "cache")
    )
    assert g.db.execute("SELECT COUNT(*) AS c FROM repo_meta").fetchone()[0] == 5


# ---------- architecture_report (Pro tool body) ----------


def test_architecture_report_finds_cycles_and_god_file():
    files = {
        "cycle/a.py": "import cycle.b\n\ndef fa():\n    pass\n",
        "cycle/b.py": "import cycle.a\n\ndef fb():\n    pass\n",
        "hub.py": "def core():\n    pass\n",
    }
    consumers = {
        f"c{i}.py": "from hub import core\n\ndef u%d():\n    return core()\n" % i
        for i in range(4)
    }
    repo = make_repo_tmp(files | consumers)
    g = GitNexusGraph()
    g.load(index_repo(repo))
    out = g.architecture_report("*")
    assert out["repo"] and out["files"] > 0
    assert out["cycles"] and any("a.py" in c["path"] for c in out["cycles"])
    assert out["god_files"][0]["path"] == "hub.py"
    assert {"path", "symbols", "rank"} <= set(out["god_files"][0])
    assert out["layers"] and {"dir", "files", "imports"} <= set(out["layers"][0])
    assert out["verdict"] in ("ok", "tangled")


def test_architecture_report_clean_repo_is_ok():
    repo = make_repo_tmp(
        {"a.py": "def one():\n    pass\n", "b.py": "def two():\n    pass\n"}
    )
    g = GitNexusGraph()
    g.load(index_repo(repo))
    out = g.architecture_report("*")
    assert out["cycles"] == [] and out["verdict"] == "ok"


def _repo_with_deps(tmp_path, files):
    root = tmp_path / "dep-repo"
    root.mkdir(parents=True, exist_ok=True)
    run_git(root, "init", "-q", "-b", "main")
    for path, text in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "feat: deps")
    return root


def make_repo_tmp(files):
    # tiny shim so the two architecture tests stay one-liners
    import tempfile, pathlib

    base = pathlib.Path(tempfile.mkdtemp(prefix="cbm-pro-"))
    return _repo_with_deps(base, files)


# ---------- Pro tool surface: build_mcp registers it only when licensed ----------


def test_build_mcp_tool_surface_gating():
    import asyncio

    async def names(mcp):
        return sorted(t.name for t in await mcp.list_tools())

    free = sorted(asyncio.run(names(mcp_server.build_mcp(pro=False))))
    assert "architecture_report" not in free and "reindex" in free
    pro = asyncio.run(names(mcp_server.build_mcp(pro=True)))
    assert "architecture_report" in pro


# ---------- watch mode: one tick reindexes changed repos only ----------


def test_watch_tick_reindexes_only_changed(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "def one():\n    pass\n"})
    g = mcp_server.index_repositories([str(repo)], cache_dir=str(tmp_path / "cache"))

    assert mcp_server.watch_tick(g, [str(repo)]) == []  # clean: no-op

    (repo / "two.py").write_text("def two():\n    pass\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-q", "-m", "feat: two")
    reindexed = mcp_server.watch_tick(g, [str(repo)])
    assert reindexed == [str(repo)]
    hits = g.search_codebase_memory("*", "two")
    assert any("two.py" in r["path"] for r in hits)


# ---------- secure preset: bili x Cloudflare AI Gateway chain ----------


def test_secure_preset_shape():
    out = mcp_server.secure_preset("ACC123", "mygw")
    assert "gateway.ai.cloudflare.com/v1/ACC123/mygw/anthropic" in out
    assert "cf-aig-authorization" in out
    assert "localhost:8787/bili/" in out
    cfg = json.loads(out)
    assert cfg["providers"]["anthropic"]["headers"]["cf-aig-authorization"]
