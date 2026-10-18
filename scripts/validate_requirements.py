"""Check every requirement against the evidence that proves it.

Reads validation/requirements.json, then:
- extracts the FR ids, the section 12.1 metric names, and the section 15 and 16 bullets from docs/requirements.md
  and fails if any of them has no entry, so a new or reworded requirement cannot slip by;
- runs the test suite and the end-to-end check once;
- fails if an entry marked "met" has no evidence, cites a test or check that does not exist, or cites one that
  did not pass;
- writes validation/report.md.

    uv run python scripts/validate_requirements.py
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "validation" / "requirements.json"
REPORT = ROOT / "validation" / "report.md"
STATUSES = {"met", "open", "superseded"}
WORST_FIRST = ["passed", "skipped", "failed"]


def doc_items(text: str) -> dict[str, str]:
    """Every requirement the document states, keyed the way matrix entries refer to them."""
    items = {f"FR:{m}": m for m in re.findall(r"\*\*(FR-\d+)\*\*", text)}
    metrics = text.split("### 12.1", 1)[1].split("### 12.2", 1)[0]
    items |= {f"Q:{m}": m for m in re.findall(r"^\| \*\*(.+?)\*\* +\|", metrics, re.MULTILINE)}
    for heading in ("## 15.", "## 16."):
        section = text.split(heading, 1)[1].split("\n## ", 1)[0]
        items |= {f"Q:{b}": b for b in re.findall(r"^- (.+)$", section, re.MULTILINE)}
    return items


def run_tests() -> dict[str, str]:
    """Outcome per test, keyed as file.py::name. A parametrized test passes only if every case passes."""
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "junit.xml"
        subprocess.run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"],
                       cwd=ROOT, capture_output=True, text=True, check=False)
        outcomes: dict[str, str] = {}
        for case in ET.parse(xml).getroot().iter("testcase"):
            key = f"{case.get('classname').split('.')[-1]}.py::{case.get('name').split('[')[0]}"
            result = "failed" if case.find("failure") is not None or case.find("error") is not None else (
                "skipped" if case.find("skipped") is not None else "passed")
            outcomes[key] = max(outcomes.get(key, "passed"), result, key=WORST_FIRST.index)
        return outcomes


def run_e2e() -> dict[str, str]:
    result = subprocess.run(["uv", "run", "python", "scripts/e2e.py"], cwd=ROOT, capture_output=True, text=True, check=False)
    outcomes = {line[6:].strip(): "passed" for line in result.stdout.splitlines() if line.startswith("ok    ")}
    for line in result.stdout.splitlines():
        if line.startswith("FAIL  "):
            outcomes[line[6:].strip()] = "failed"
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    entries = json.loads(args.matrix.read_text())
    problems: list[str] = []

    duplicates = [i for i, n in Counter(e["id"] for e in entries).items() if n > 1]
    problems += [f"{i}: listed more than once" for i in duplicates]
    covered = {f"FR:{e['id']}" for e in entries if e["id"].startswith("FR-")} | {f"Q:{e['quote']}" for e in entries if "quote" in e}
    stated = doc_items((ROOT / "docs" / "requirements.md").read_text())
    problems += [f"{text}: in docs/requirements.md but not in the matrix" for key, text in stated.items() if key not in covered]
    problems += [f"{key[2:]}: in the matrix but not in docs/requirements.md" for key in covered if key not in stated]

    print("Running the test suite and the end-to-end check...", flush=True)
    tests, e2e = run_tests(), run_e2e()
    if not tests:
        problems.append("the test suite produced no results")
    if not e2e:
        problems.append("the end-to-end check produced no results")

    rows = []
    for e in entries:
        status = e.get("status")
        if status not in STATUSES:
            problems.append(f"{e['id']}: unknown status {status!r}")
            continue
        verdict = status
        if status == "met":
            evidence = [("test", t, tests.get(t, "missing")) for t in e.get("tests", [])]
            evidence += [("e2e", c, e2e.get(c, "missing")) for c in e.get("e2e", [])]
            if not evidence:
                problems.append(f"{e['id']}: marked met with no evidence")
            for kind, name, outcome in evidence:
                if outcome != "passed":
                    problems.append(f"{e['id']}: {kind} '{name}' {outcome}")
            bad = [name for _, name, outcome in evidence if outcome != "passed"]
            verdict = "met" if evidence and not bad else "FAILED"
            detail = f"{sum(1 for k, *_ in evidence if k == 'test')} tests, {sum(1 for k, *_ in evidence if k == 'e2e')} e2e checks"
        else:
            if not e.get("reason"):
                problems.append(f"{e['id']}: {status} needs a reason")
            detail = e.get("reason", "")
        text = e.get("requirement") or e.get("quote", "")
        rows.append((e["id"], verdict, text, detail + (f" {e['note']}" if e.get("note") else "")))

    counts = Counter(v for _, v, *_ in rows)
    summary = (f"{counts['met']} met, {counts['open']} open, {counts['superseded']} superseded by docs/design.md, "
               f"{counts['FAILED']} failed. {sum(v == 'passed' for v in tests.values())} tests and "
               f"{sum(v == 'passed' for v in e2e.values())} end-to-end checks passed.")
    lines = ["# Requirements validation", "", "Generated by `scripts/validate_requirements.py`. Do not edit by hand.", "", summary, "",
             "| Id | Verdict | Requirement | Evidence or reason |", "|---|---|---|---|"]
    lines += [f"| {i} | {v} | {t.replace('|', '/')} | {d.replace('|', '/')} |" for i, v, t, d in rows]
    if problems:
        lines += ["", "## Problems", ""] + [f"- {p}" for p in problems]
    args.report.write_text("\n".join(lines) + "\n")

    print(summary)
    for p in problems:
        print(f"PROBLEM  {p}")
    print(f"Wrote {args.report}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
