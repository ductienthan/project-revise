import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("validate_requirements", ROOT / "scripts" / "validate_requirements.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def test_every_stated_requirement_is_extracted_from_the_document():
    items = validator.doc_items((ROOT / "docs" / "requirements.md").read_text())
    frs = [k for k in items if k.startswith("FR:")]
    quotes = [v for k, v in items.items() if k.startswith("Q:")]
    assert len(frs) == 42 and items["FR:FR-14"] == "Items marked sensitive are never offered and never sent."
    assert "Repeat rate" in quotes and "Rule status" in quotes
    assert "Items marked sensitive are never sent." in quotes
    assert "Export is always available so data is never locked in." in quotes
    assert len(quotes) == 14 + 9 + 6, "14 metrics, 9 privacy bullets, 6 non-functional bullets"
