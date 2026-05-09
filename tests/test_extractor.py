import pytest
from memex.extractor.treesitter import extract_symbol_delta

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
