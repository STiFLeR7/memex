"""Deterministic Python structural contributions with explicit coverage."""
import ast
from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class IndexedSymbol:
    name: str
    qualified_name: str
    kind: str
    line: int
    signature: str


@dataclass(frozen=True)
class IndexedCall:
    caller: str
    callee: str
    line: int


@dataclass(frozen=True)
class FileStructure:
    path: str
    content_hash: str
    coverage: str
    symbols: tuple[IndexedSymbol, ...] = ()
    calls: tuple[IndexedCall, ...] = ()
    imports: tuple[str, ...] = ()


class _Visitor(ast.NodeVisitor):
    def __init__(self, source: str):
        self.lines = source.splitlines()
        self.scope: list[str] = []
        self.call_scope: list[str] = ["<module>"]
        self.symbols: list[IndexedSymbol] = []
        self.calls: list[IndexedCall] = []
        self.imports: set[str] = set()

    def _definition(self, node, kind: str) -> None:
        # Definition headers execute in their enclosing lexical scope. Retain
        # syntactic calls even when annotations are deferred at runtime.
        for field, value in ast.iter_fields(node):
            if field == "body":
                continue
            if isinstance(value, ast.AST):
                self.visit(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        self.visit(item)
        self.scope.append(node.name)
        qualified = ".".join(self.scope)
        self.symbols.append(IndexedSymbol(
            node.name, qualified, kind, node.lineno, self.lines[node.lineno - 1].strip(),
        ))
        self.call_scope.append(qualified if kind == "fn" else qualified + ".<body>")
        for statement in node.body:
            self.visit(statement)
        self.call_scope.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self._definition(node, "fn")

    def visit_AsyncFunctionDef(self, node):
        self._definition(node, "fn")

    def visit_ClassDef(self, node):
        self._definition(node, "class")

    def visit_Call(self, node):
        name = node.func.id if isinstance(node.func, ast.Name) else (
            node.func.attr if isinstance(node.func, ast.Attribute) else "<dynamic>"
        )
        self.calls.append(IndexedCall(self.call_scope[-1], name, node.lineno))
        self.generic_visit(node)

    def visit_Import(self, node):
        self.imports.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node):
        self.imports.add("." * node.level + (node.module or ""))


def extract_structure(path: str, content: bytes) -> FileStructure:
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    if not path.endswith(".py"):
        return FileStructure(path, digest, "unsupported")
    try:
        source = content.decode("utf-8-sig")
        tree = ast.parse(source, filename=path)
    except (UnicodeError, SyntaxError, ValueError):
        return FileStructure(path, digest, "parse_error")
    visitor = _Visitor(source)
    visitor.visit(tree)
    return FileStructure(path, digest, "complete", tuple(visitor.symbols),
                         tuple(visitor.calls), tuple(sorted(visitor.imports)))
