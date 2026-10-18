import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gen_docs", ROOT / "scripts" / "gen_docs.py")
gen_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_docs)


@pytest.mark.parametrize("name", sorted(gen_docs.DOCS))
def test_generated_docs_match_the_code(name):
    on_disk = (ROOT / "docs" / name).read_text()
    assert on_disk == gen_docs.DOCS[name](), f"docs/{name} is stale. Run `uv run python scripts/gen_docs.py`."


def test_every_documented_cli_variable_is_read_by_the_cli():
    source = "\n".join(p.read_text() for p in (ROOT / "src" / "preflight" / "cli").glob("*.py"))
    assert all(name in source for name, _, _ in gen_docs.CLI_ENV)


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading."""
    return re.sub(r"[^a-z0-9 -]", "", heading.strip().lower()).replace(" ", "-")


def test_doc_links_point_at_files_and_headings_that_exist():
    for doc in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        for target, anchor in re.findall(r"\]\(([^)#:]*)(?:#([^)]*))?\)", doc.read_text()):
            path = (doc.parent / target) if target else doc
            assert path.exists(), f"{doc.relative_to(ROOT)} links to missing {target}"
            if anchor:
                headings = {slug(h) for h in re.findall(r"^#+ (.+)$", path.read_text(), re.MULTILINE)}
                assert anchor in headings, f"{doc.relative_to(ROOT)} links to missing heading #{anchor} in {path.name}"
