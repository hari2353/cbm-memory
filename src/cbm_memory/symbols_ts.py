"""Tree-sitter symbol/call/import extraction (T04/T05/T07 source of truth).

One grammar pack for all languages. Every function is fail-soft: repoindex
falls back to its regex scan when this module is unavailable or a language
parse/query fails, so the uvx zero-setup path never breaks.
"""

from __future__ import annotations

LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
}

# Capture scheme per language: @fn/@cls = definition name, @deffn/@defcls =
# whole definition node (used to attribute calls to their enclosing symbol),
# @callee = called name, @mod = imported module/string.
_DEF_QUERIES = {
    "python": """(function_definition name: (identifier) @fn) @deffn
                 (class_definition name: (identifier) @cls) @defcls""",
    "javascript": """(function_declaration name: (identifier) @fn) @deffn
                     (function_expression name: (identifier) @fn) @deffn
                     (class_declaration name: (identifier) @cls) @defcls
                     (method_definition name: (property_identifier) @fn) @deffn""",
    "typescript": """(function_declaration name: (identifier) @fn) @deffn
                     (function_signature name: (identifier) @fn) @deffn
                     (class_declaration name: (identifier) @cls) @defcls
                     (method_definition name: (property_identifier) @fn) @deffn
                     (abstract_class_declaration name: (identifier) @cls) @defcls""",
    "tsx": """(function_declaration name: (identifier) @fn) @deffn
              (class_declaration name: (identifier) @cls) @defcls
              (method_definition name: (property_identifier) @fn) @deffn""",
    "go": """(function_declaration name: (identifier) @fn) @deffn
             (method_declaration name: (field_identifier) @fn) @deffn
             (type_spec name: (type_identifier) @cls) @defcls""",
    "rust": """(function_item name: (identifier) @fn) @deffn
               (struct_item name: (type_identifier) @cls) @defcls
               (enum_item name: (type_identifier) @cls) @defcls
               (impl_item type: (type_identifier) @cls) @defcls""",
    "java": """(class_declaration name: (identifier) @cls) @defcls
               (interface_declaration name: (identifier) @cls) @defcls
               (method_declaration name: (identifier) @fn) @deffn""",
    "c": """(function_definition declarator: (function_declarator
                declarator: (identifier) @fn)) @deffn
            (struct_specifier name: (type_identifier) @cls) @defcls
            (enum_specifier name: (type_identifier) @cls) @defcls""",
    "cpp": """(function_definition declarator: (function_declarator
                declarator: (identifier) @fn)) @deffn
              (function_definition declarator: (function_declarator
                declarator: (field_identifier) @fn)) @deffn
              (struct_specifier name: (type_identifier) @cls) @defcls
              (class_specifier name: (type_identifier) @cls) @defcls""",
    "ruby": """(method name: (identifier) @fn) @deffn
               (singleton_method name: (identifier) @fn) @deffn
               (class name: (constant) @cls) @defcls
               (module name: (constant) @cls) @defcls""",
    "php": """(function_definition name: (identifier) @fn) @deffn
              (method_declaration name: (name) @fn) @deffn
              (class_declaration name: (name) @cls) @defcls""",
}

_CALL_QUERIES = {
    "python": """(call function: (identifier) @callee)
                 (call function: (attribute attribute: (identifier) @callee))""",
    "javascript": """(call_expression function: (identifier) @callee)
                     (call_expression function: (member_expression
                        property: (property_identifier) @callee))""",
    "typescript": """(call_expression function: (identifier) @callee)
                     (call_expression function: (member_expression
                        property: (property_identifier) @callee))""",
    "tsx": """(call_expression function: (identifier) @callee)""",
    "go": """(call_expression function: (identifier) @callee)
             (call_expression function: (selector_expression
                field: (field_identifier) @callee))""",
    "rust": """(call_expression function: (identifier) @callee)""",
    "java": """(method_invocation name: (identifier) @callee)""",
    "c": """(call_expression function: (identifier) @callee)""",
    "cpp": """(call_expression function: (identifier) @callee)
              (call_expression function: (field_expression
                field: (field_identifier) @callee))""",
    "ruby": """(call method: (identifier) @callee)""",
    "php": """(function_call_expression function: (identifier) @callee)""",
}

_IMPORT_QUERIES = {
    "python": """(import_statement name: (dotted_name) @mod)
                 (import_from_statement module_name: (dotted_name) @mod)
                 (import_from_statement module_name: (relative_import) @mod)""",
    "javascript": """(import_statement source: (string) @mod)
                     (export_statement source: (string) @mod)""",
    "typescript": """(import_statement source: (string) @mod)
                     (export_statement source: (string) @mod)""",
    "tsx": """(import_statement source: (string) @mod)""",
    "go": """(import_spec path: (interpreted_string_literal) @mod)""",
}

try:
    from tree_sitter import Query, QueryCursor
    from tree_sitter_language_pack import get_language, get_parser

    _PACK_ERROR = None
except Exception as _e:  # pragma: no cover - exercised only without the pack
    get_language = get_parser = None
    Query = QueryCursor = None
    _PACK_ERROR = str(_e)

_queries: dict[tuple[str, str], object] = {}


def available() -> bool:
    return get_parser is not None


def _patterns(src: str) -> list[str]:
    """Split a query table into complete patterns; a pattern may wrap across
    lines, so split when parentheses balance (a new pattern starts at '(')."""
    out, buf, depth = [], "", 0
    for ln in src.splitlines():
        buf = (buf + " " + ln.strip()).strip()
        depth += ln.count("(") - ln.count(")")
        if depth == 0 and buf:
            out.append(" ".join(buf.split()))
            buf = ""
    return [p for p in out if p]


def _query(lang: str, kind: str) -> list:
    """Compile a table's patterns individually and cache the working ones.
    Grammars drift between pack versions; one bad node type must not kill a
    whole language, so each pattern compiles on its own."""
    key = (lang, kind)
    if key not in _queries:
        table = {"def": _DEF_QUERIES, "call": _CALL_QUERIES, "import": _IMPORT_QUERIES}[
            kind
        ]
        src = table.get(lang)
        if src is None:
            _queries[key] = []
        else:
            out = []
            for pat in _patterns(src):
                try:
                    out.append(Query(get_language(lang), pat))
                except Exception:
                    pass
            _queries[key] = out
    return _queries[key]


def _captures(queries: list, root) -> dict[str, list]:
    out: dict[str, list] = {}
    for q in queries:
        res = QueryCursor(q).captures(root)
        items = (
            res.items()
            if isinstance(res, dict)
            else ((name, [node]) for node, name in res)
        )
        for name, nodes in items:
            out.setdefault(name, []).extend(nodes)
    return out


def _text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _lang_for(path: str) -> str | None:
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return LANG_BY_EXT.get(ext)


def _pair_defs(caps: dict[str, list], src: bytes) -> list[tuple[int, int, str]]:
    """[(start, end, enclosing_name)] — smallest whole def node containing each
    name capture. Used to attribute calls to their enclosing symbol."""
    wholes = [(n.start_byte, n.end_byte) for n in caps.get("deffn", [])]
    wholes += [(n.start_byte, n.end_byte) for n in caps.get("defcls", [])]
    names = [(n.start_byte, n.end_byte, _text(n, src)) for n in caps.get("fn", [])]
    names += [(n.start_byte, n.end_byte, _text(n, src)) for n in caps.get("cls", [])]
    out = []
    for ws, we in wholes:
        inner = [nm for ns, ne, nm in names if ws <= ns and ne <= we]
        if inner:
            out.append((ws, we, inner[0]))
    return sorted(out)


def extract_symbols(path: str, src: bytes) -> list[tuple[str, str]]:
    """[(kind, name)] in AST order; kinds: function | class."""
    lang = _lang_for(path)
    q = _query(lang, "def") if lang else None
    if lang is None or get_parser is None or not q:
        raise LookupError(f"no tree-sitter symbols for {path}")
    caps = _captures(q, get_parser(lang).parse(src).root_node)
    out = [("function", _text(n, src)) for n in caps.get("fn", [])]
    out += [("class", _text(n, src)) for n in caps.get("cls", [])]
    seen, uniq = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def extract_calls(path: str, src: bytes) -> list[tuple[str | None, str]]:
    """[(enclosing_symbol_or_None, callee_name)] for each call site.
    Languages without a call query contribute no calls (fail-soft)."""
    lang = _lang_for(path)
    q = _query(lang, "call") if lang else None
    if lang is None or get_parser is None or not q:
        return []
    root = get_parser(lang).parse(src).root_node
    defs = (
        _pair_defs(_captures(_query(lang, "def"), root), src)
        if _query(lang, "def")
        else []
    )
    out: list[tuple[str | None, str]] = []
    for n in _captures(q, root).get("callee", []):
        callee = _text(n, src)
        caller = next((nm for s, e, nm in defs if s <= n.start_byte < e), None)
        out.append((caller, callee))
    return out


def extract_imports(path: str, src: bytes) -> list[str]:
    """Raw module strings/paths (unresolved).
    Languages without an import query contribute no imports (fail-soft)."""
    lang = _lang_for(path)
    q = _query(lang, "import") if lang else None
    if lang is None or get_parser is None or not q:
        return []
    caps = _captures(q, get_parser(lang).parse(src).root_node)
    mods = [_text(n, src) for n in caps.get("mod", [])]
    return [m.strip().strip("'\"") for m in mods if m.strip().strip("'\"")]
