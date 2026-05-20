import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
import tree_sitter_language_pack as tslp
from memex.graph.schema import Symbol

logger = logging.getLogger(__name__)

@dataclass
class SymbolDelta:
    added: List[Symbol] = field(default_factory=list)
    removed: List[Symbol] = field(default_factory=list)
    modified: List[Symbol] = field(default_factory=list)


@dataclass
class CallEdge:
    """A resolved call-site: function `caller` (defined in `file`) calls the
    name `callee` at 1-indexed `line`. `callee` is a bare name — resolution to
    a concrete target Symbol node happens at write time (writer.write_call_edges).
    """
    caller: str
    callee: str
    file: str
    line: int


# tree-sitter call-expression queries, per language. Captures the *callee*
# name (the simple identifier, or the trailing attribute of `a.b.c()`).
# v0.3.7 Layer 2 ships Python only — memex's own codebase is Python and the
# grammars differ per language; others fall through to [] (no edges, no error).
_CALL_QUERIES: Dict[str, str] = {
    "python": "(call function: [(identifier) @callee "
              "(attribute attribute: (identifier) @callee)])",
}


def _flatten_functions(items, acc: List[Tuple[str, int, int]]) -> None:
    """Collect (name, start_line, end_line) for every function/method symbol,
    recursing into class bodies. Lines are 0-indexed (tree-sitter convention)."""
    for it in items:
        kind = str(it.kind).lower()
        if "function" in kind or "method" in kind:
            acc.append((it.name, it.span.start_line, it.span.end_line))
        if it.children:
            _flatten_functions(it.children, acc)


def extract_calls(file_path: str, content: str, language: str = "python") -> List[CallEdge]:
    """Extract intra-file call-sites and map each to its enclosing function.

    Returns one :class:`CallEdge` per (caller, callee) call-site. Calls made at
    module scope (no enclosing function) are skipped — we don't fabricate a
    caller. Unsupported languages return ``[]``.
    """
    query_src = _CALL_QUERIES.get(language)
    if not query_src or not content:
        return []

    try:
        import tree_sitter as ts
        lang = tslp.get_language(language)
        parser = ts.Parser(lang)
        tree = parser.parse(content.encode("utf-8", errors="ignore"))
        query = ts.Query(lang, query_src)
        cursor = ts.QueryCursor(query)
        captures = cursor.captures(tree.root_node)
    except Exception:
        logger.debug("call extraction failed for %s", file_path, exc_info=True)
        return []

    # Enclosing-function spans (0-indexed) for caller resolution.
    try:
        result = tslp.process(content, config=tslp.ProcessConfig(language=language))
        functions: List[Tuple[str, int, int]] = []
        _flatten_functions(result.structure, functions)
    except Exception:
        return []

    raw = content.encode("utf-8", errors="ignore")
    edges: List[CallEdge] = []
    seen: set[Tuple[str, str, int]] = set()

    for node in captures.get("callee", []):
        callee = raw[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
        line0 = node.start_point[0]
        # Innermost enclosing function: smallest span that contains the call.
        caller = None
        best = None
        for name, s, e in functions:
            if s <= line0 <= e:
                span = e - s
                if best is None or span < best:
                    best = span
                    caller = name
        if caller is None:
            continue  # module-level call — no caller symbol
        key = (caller, callee, line0)
        if key in seen:
            continue
        seen.add(key)
        edges.append(CallEdge(caller=caller, callee=callee, file=file_path, line=line0 + 1))

    return edges

def get_symbols_from_content(content: str, file_path: str, language_name: str) -> Dict[str, Symbol]:
    """
    Parses content and extracts symbols using tree-sitter-language-pack high-level API.
    Returns a mapping of symbol 'key' (name:kind) to Symbol object.
    """
    symbols = {}
    if not content:
        return symbols

    try:
        config = tslp.ProcessConfig(language=language_name)
        result = tslp.process(content, config=config)
    except Exception:
        # If language is not supported or other error, return empty symbols
        return symbols

    for item in result.structure:
        # Map tree-sitter kinds to our simple kinds
        kind_str = str(item.kind).lower()
        if "function" in kind_str or "method" in kind_str:
            kind = "fn"
        elif "class" in kind_str or "struct" in kind_str or "interface" in kind_str:
            kind = "class"
        else:
            # For Phase 1, we focus on fn and class. 
            # Constants might be 'other' or specific kinds depending on language.
            continue

        # Extract signature: for now, just the line where it starts
        lines = content.splitlines()
        line_idx = item.span.start_line
        signature = lines[line_idx].strip() if line_idx < len(lines) else item.name

        s = Symbol(
            name=item.name,
            kind=kind,
            signature=signature,
            file=file_path,
            line=item.span.start_line + 1 # 1-indexed
        )
        symbols[f"{item.name}:{kind}"] = s

    return symbols

async def extract_symbol_delta(
    file_path: str,
    old_content: str,
    new_content: str,
    language: Optional[str] = None,
) -> SymbolDelta:
    if language is None:
        ext = file_path.split(".")[-1]
        lang_map = {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "rs": "rust",
            "go": "go"
        }
        language = lang_map.get(ext, "python")

    old_symbols = get_symbols_from_content(old_content, file_path, language)
    new_symbols = get_symbols_from_content(new_content, file_path, language)

    delta = SymbolDelta()

    # Find added and modified
    for key, new_sym in new_symbols.items():
        if key not in old_symbols:
            delta.added.append(new_sym)
        else:
            old_sym = old_symbols[key]
            if old_sym.signature != new_sym.signature:
                delta.modified.append(new_sym)

    # Find removed
    for key, old_sym in old_symbols.items():
        if key not in new_symbols:
            delta.removed.append(old_sym)

    return delta
