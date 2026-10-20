import ast
from pathlib import Path

import pytest

from notice.language import english_narrative, validate_narrative
from notice.schemas import Answer


def test_generated_narrative_is_english_only():
    assert english_narrative("Reply by 31 October 2026; amount: INR 12,500.")
    foreign_text = chr(0x03B1) * 4
    answer = Answer(claims=[], missing_context=[foreign_text], professional_review=True)
    with pytest.raises(ValueError, match="English"):
        validate_narrative(answer)


def test_application_source_has_no_disallowed_script_or_language_configuration():
    root = Path(__file__).resolve().parents[1]
    excluded = {
        "node_modules",
        "__pycache__",
        ".git",
        ".runtime",
        ".pytest_cache",
        ".ruff_cache",
        "test-results",
        "playwright-report",
    }
    failures = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in excluded or part.endswith(".egg-info") for part in path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        if any(0x0900 <= ord(char) <= 0x097F or 0xA8E0 <= ord(char) <= 0xA8FF for char in content):
            failures.append(str(path.relative_to(root)))
        if path.suffix == ".py":
            for node in ast.walk(ast.parse(content)):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if any(0x0900 <= ord(char) <= 0x097F for char in node.value):
                        failures.append(str(path.relative_to(root)) + ": decoded string")
    assert not failures
    web = root / "notice" / "web"
    assert '<html lang="en">' in (web / "index.html").read_text(encoding="utf-8")
    assert '"en-IN"' in (web / "app.js").read_text(encoding="utf-8")
