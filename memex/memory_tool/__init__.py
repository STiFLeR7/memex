"""memex memory-tool adapter (Move 1).

Exposes ``MemexAsyncMemoryTool``, a subclass of
``anthropic.lib.tools._beta_builtin_memory_tool.BetaAsyncAbstractMemoryTool``
that lets Claude's ``memory_20250818`` tool surface use memex as its
storage backend.

See ARCHITECTURE-v0.3.0.md §11.5 for the locked design.
"""

from memex.memory_tool.server import MemexAsyncMemoryTool, run_memory_tool_serve

__all__ = ["MemexAsyncMemoryTool", "run_memory_tool_serve"]
