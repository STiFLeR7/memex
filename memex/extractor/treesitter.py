import ast
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


def _flatten_functions(items, acc: List[Tuple[str, int, int]]) -> None:
    """Collect (name, start_line, end_line) for every function/method symbol,
    recursing into class bodies. Lines are 0-indexed (tree-sitter convention)."""
    for it in items:
        kind = str(it.kind).lower()
        if "function" in kind or "method" in kind:
            acc.append((it.name, it.span.start_line, it.span.end_line))
    if it.children:
            _flatten_functions(it.children, acc)


class _PythonCallVisitor(ast.NodeVisitor):
    """Collect calls while retaining the enclosing Python function name."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.caller_stack: list[str] = []
        self.edges: list[CallEdge] = []
        self.seen: set[Tuple[str, str, int]] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.caller_stack.append(node.name)
        self.generic_visit(node)
        self.caller_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if self.caller_stack:
            if isinstance(node.func, ast.Name):
                callee = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callee = node.func.attr
            else:
                callee = None
            if callee is not None:
                key = (self.caller_stack[-1], callee, node.lineno)
                if key not in self.seen:
                    self.seen.add(key)
                    self.edges.append(
                        CallEdge(
                            caller=key[0],
                            callee=callee,
                            file=self.file_path,
                            line=node.lineno,
                        )
                    )
        self.generic_visit(node)


def extract_calls(file_path: str, content: str, language: str = "python") -> List[CallEdge]:
    """Extract intra-file call-sites and map each to its enclosing function.

    Returns one :class:`CallEdge` per (caller, callee) call-site. Calls made at
    module scope (no enclosing function) are skipped — we don't fabricate a
    caller. Unsupported languages return ``[]``.
    """
    if language != "python" or not content:
        return []

    try:
        tree = ast.parse(content, filename=file_path)
    except Exception:
        logger.debug("call extraction failed for %s", file_path, exc_info=True)
        return []
    visitor = _PythonCallVisitor(file_path)
    visitor.visit(tree)
    return visitor.edges

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
