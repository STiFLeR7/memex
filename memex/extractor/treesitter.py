from dataclasses import dataclass, field
from typing import List, Optional, Dict
import tree_sitter_language_pack as tslp
from memex.graph.schema import Symbol

@dataclass
class SymbolDelta:
    added: List[Symbol] = field(default_factory=list)
    removed: List[Symbol] = field(default_factory=list)
    modified: List[Symbol] = field(default_factory=list)

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
