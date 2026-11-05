from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
# Per-directory files that may legitimately repeat in every test folder.
PER_DIRECTORY = {"conftest.py", "__init__.py"}


def test_test_module_basenames_are_unique_across_test_folders():
    # Default (prepend) import mode imports test modules by basename; duplicates break collection.
    names = [path.name for path in TESTS.rglob("*.py") if path.name not in PER_DIRECTORY]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == []
