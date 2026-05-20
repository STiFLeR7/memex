import pytest
from memex.extractor.treesitter import extract_symbol_delta, extract_calls


def test_extract_calls_resolves_caller_and_callee():
    """v0.3.7 Layer 2 — map each call-site to the function that contains it
    (caller) and the called name (callee). Feeds CALLS edges for
    predict_impact."""
    src = """def foo(x):
    return bar(x) + baz()

class A:
    def m(self):
        foo(1)
        self.helper()
"""
    calls = extract_calls("mod.py", src, language="python")
    pairs = {(c.caller, c.callee) for c in calls}

    # foo() calls bar and baz
    assert ("foo", "bar") in pairs
    assert ("foo", "baz") in pairs
    # A.m() calls foo and helper (self.helper -> attribute name)
    assert ("m", "foo") in pairs
    assert ("m", "helper") in pairs
    # every call carries the file
    assert all(c.file == "mod.py" for c in calls)


def test_extract_calls_skips_module_level_calls():
    """Calls at module scope have no enclosing function symbol — we don't
    invent a caller for them in v1."""
    src = "import os\nprint('hi')\nos.getcwd()\n"
    calls = extract_calls("mod.py", src, language="python")
    assert calls == []


def test_extract_calls_unsupported_language_returns_empty():
    calls = extract_calls("mod.rb", "puts 'hi'", language="ruby")
    assert calls == []


@pytest.mark.asyncio
async def test_extract_symbol_delta_new_file():
    old_content = ""
    new_content = """
def hello(name: str):
    print(f"Hello {name}")

class Greeter:
    def __init__(self):
        pass
"""
    delta = await extract_symbol_delta("test.py", old_content, new_content)
    
    assert len(delta.added) == 2
    assert any(s.name == "hello" and s.kind == "fn" for s in delta.added)
    assert any(s.name == "Greeter" and s.kind == "class" for s in delta.added)
    assert len(delta.removed) == 0
    assert len(delta.modified) == 0

@pytest.mark.asyncio
async def test_extract_symbol_delta_deleted_file():
    old_content = "def old_fn(): pass"
    new_content = ""
    delta = await extract_symbol_delta("test.py", old_content, new_content)
    
    assert len(delta.added) == 0
    assert len(delta.removed) == 1
    assert delta.removed[0].name == "old_fn"

@pytest.mark.asyncio
async def test_extract_symbol_delta_modified_signature():
    old_content = "def greet(name): pass"
    new_content = "def greet(name: str, shout: bool = False): pass"
    delta = await extract_symbol_delta("test.py", old_content, new_content)
    
    assert len(delta.added) == 0
    assert len(delta.removed) == 0
    assert len(delta.modified) == 1
    assert delta.modified[0].name == "greet"
    assert "name: str" in delta.modified[0].signature
