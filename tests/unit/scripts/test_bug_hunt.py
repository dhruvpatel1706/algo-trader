"""Tests for scripts/bug_hunt.py.

The bug hunter must NOT scan the live repo during tests — every test points it
at a tmp_path fixture directory. We exercise:
  - Pattern visitor: each domain pattern fires correctly + clean files do not.
  - Severity filter: --severity high excludes low/medium.
  - --json mode produces valid JSON and does NOT touch docs/bug_hunt.md.
  - --quick skips mypy and the test-health pass.
  - Exit code 1 iff a critical finding is present.
  - Markdown output is appended (idempotent) and atomically written.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "bug_hunt.py"


@pytest.fixture
def bh():
    """Import scripts/bug_hunt.py as a module."""
    spec = importlib.util.spec_from_file_location("bug_hunt", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bug_hunt"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_synthetic_repo(root: Path, files: dict[str, str]) -> Path:
    """Build a minimal repo at `root` with src/ scaffolding + the given files."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "dashboard" / "api").mkdir(parents=True, exist_ok=True)
    # pyproject.toml so --root passes the validity check
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root


# ---------------------------------------------------------------------------
# Pattern: broad_exception_swallow
# ---------------------------------------------------------------------------


def test_pattern_broad_exception_swallow_fires(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/foo.py": (
                "def bad():\n"
                "    try:\n"
                "        risky()\n"
                "    except Exception:\n"
                "        pass\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    pats = [f.pattern for f in findings]
    assert "broad_exception_swallow" in pats


def test_pattern_broad_exception_with_logger_does_not_fire(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/foo.py": (
                "import logging\n"
                "log = logging.getLogger(__name__)\n"
                "def ok():\n"
                "    try:\n"
                "        risky()\n"
                "    except Exception:\n"
                "        log.exception('boom')\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert not any(f.pattern == "broad_exception_swallow" for f in findings)


# ---------------------------------------------------------------------------
# Pattern: timezone_naive_datetime
# ---------------------------------------------------------------------------


def test_pattern_tz_naive_datetime_now_fires(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/clock.py": (
                "from datetime import datetime\n"
                "def what_time():\n"
                "    return datetime.now()\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert any(f.pattern == "timezone_naive_datetime" for f in findings)


def test_pattern_tz_aware_datetime_now_does_not_fire(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/clock.py": (
                "from datetime import datetime, UTC\n"
                "def what_time():\n"
                "    return datetime.now(UTC)\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert not any(f.pattern == "timezone_naive_datetime" for f in findings)


def test_pattern_datetime_utcnow_fires(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/clock.py": (
                "from datetime import datetime\n"
                "def what_time():\n"
                "    return datetime.utcnow()\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert any(f.pattern == "timezone_naive_datetime" for f in findings)


# ---------------------------------------------------------------------------
# Pattern: decimal_float_compare
# ---------------------------------------------------------------------------


def test_pattern_decimal_float_compare_fires(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/risk/sizing.py": (
                "from decimal import Decimal\n"
                "def size(qty: Decimal) -> bool:\n"
                "    return qty > 0.5\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert any(f.pattern == "decimal_float_compare" for f in findings)


def test_pattern_decimal_decimal_compare_does_not_fire(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/risk/sizing.py": (
                "from decimal import Decimal\n"
                "def size(qty: Decimal) -> bool:\n"
                "    return qty > Decimal('0.5')\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert not any(f.pattern == "decimal_float_compare" for f in findings)


# ---------------------------------------------------------------------------
# Pattern: look_ahead_iloc_minus_1
# ---------------------------------------------------------------------------


def test_pattern_iloc_minus_1_in_ml_fires(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/ml/features.py": (
                "def latest_close(df):\n"
                "    return df.iloc[-1]\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert any(f.pattern == "look_ahead_iloc_minus_1" for f in findings)


def test_pattern_iloc_minus_1_inside_generate_signals_does_not_fire(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/ml/features.py": (
                "def generate_signals(df):\n"
                "    return df.iloc[-1]\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert not any(f.pattern == "look_ahead_iloc_minus_1" for f in findings)


# ---------------------------------------------------------------------------
# Pattern: missing_fsync_on_journal_write
# ---------------------------------------------------------------------------


def test_pattern_missing_fsync_fires(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/journal/writer.py": (
                "def write_record(path, line):\n"
                "    with open(path, 'a') as f:\n"
                "        f.write(line)\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert any(f.pattern == "missing_fsync_on_journal_write" for f in findings)


def test_pattern_fsync_present_does_not_fire(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/journal/writer.py": (
                "import os\n"
                "def write_record(path, line):\n"
                "    with open(path, 'a') as f:\n"
                "        f.write(line)\n"
                "        f.flush()\n"
                "        os.fsync(f.fileno())\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert not any(f.pattern == "missing_fsync_on_journal_write" for f in findings)


# ---------------------------------------------------------------------------
# Clean file produces nothing
# ---------------------------------------------------------------------------


def test_clean_file_produces_no_findings(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/clean.py": (
                '"""A clean module."""\n'
                "from datetime import datetime, UTC\n"
                "def safe(x: int) -> int:\n"
                "    return x + 1\n"
                "def now() -> datetime:\n"
                "    return datetime.now(UTC)\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert findings == []


# ---------------------------------------------------------------------------
# Suppression: # noqa: bug-hunt:<pattern>
# ---------------------------------------------------------------------------


def test_suppression_comment_silences_finding(bh, tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/foo.py": (
                "from datetime import datetime\n"
                "def now():\n"
                "    return datetime.now()  # noqa: bug-hunt:timezone_naive_datetime\n"
            ),
        },
    )
    findings = bh.run_pattern_scan(repo)
    assert not any(f.pattern == "timezone_naive_datetime" for f in findings)


# ---------------------------------------------------------------------------
# Severity helper
# ---------------------------------------------------------------------------


def test_at_or_above(bh):
    assert bh._at_or_above("critical", "high")
    assert bh._at_or_above("high", "high")
    assert not bh._at_or_above("medium", "high")
    assert not bh._at_or_above("low", "medium")


# ---------------------------------------------------------------------------
# Markdown rendering + atomic append
# ---------------------------------------------------------------------------


def test_render_markdown_groups_by_severity(bh):
    findings = [
        bh.Finding("p1", "critical", "src/a.py", 10, "boom", "pattern"),
        bh.Finding("p2", "low", "src/b.py", 5, "minor", "pattern"),
    ]
    out = bh.render_markdown(
        findings,
        health=None,
        versions={"ruff": "0.x"},
        timestamp=__import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").UTC),
        notes=[],
    )
    assert "### Critical" in out
    assert "### Low" in out
    assert "boom" in out
    assert "minor" in out
    # critical comes before low
    assert out.index("### Critical") < out.index("### Low")


def test_append_report_initializes_then_appends(bh, tmp_path):
    report = tmp_path / "docs" / "bug_hunt.md"
    bh.append_report(report, "## Section A\n")
    first = report.read_text()
    assert first.startswith("# Bug Hunt")
    assert "## Section A" in first

    bh.append_report(report, "## Section B\n")
    second = report.read_text()
    # Header still there exactly once (we only append).
    assert second.count("# Bug Hunt — automated triage") == 1
    assert "## Section A" in second
    assert "## Section B" in second
    # Append, not overwrite.
    assert len(second) > len(first)


def test_append_report_atomic_no_temp_left_behind(bh, tmp_path):
    report = tmp_path / "docs" / "bug_hunt.md"
    bh.append_report(report, "## X\n")
    leftover = list(report.parent.glob(".bug_hunt.*"))
    assert leftover == []


# ---------------------------------------------------------------------------
# CLI integration: severity filter, --json, exit codes
# ---------------------------------------------------------------------------


def _run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_cli_json_mode_does_not_write_markdown(tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/foo.py": (
                "def bad():\n"
                "    try:\n"
                "        risky()\n"
                "    except Exception:\n"
                "        pass\n"
            ),
        },
    )
    proc = _run_cli(repo, "--quick", "--json")
    assert proc.returncode in (0, 1)
    data = json.loads(proc.stdout)
    assert "findings" in data
    assert isinstance(data["findings"], list)
    # Markdown report should NOT have been created.
    assert not (repo / "docs" / "bug_hunt.md").exists()


def test_cli_quick_writes_markdown_and_appends(tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/foo.py": (
                "from datetime import datetime\n"
                "def now():\n"
                "    return datetime.now()\n"
            ),
        },
    )
    p1 = _run_cli(repo, "--quick")
    assert p1.returncode in (0, 1)
    report = repo / "docs" / "bug_hunt.md"
    assert report.exists()
    first = report.read_text()
    p2 = _run_cli(repo, "--quick")
    assert p2.returncode in (0, 1)
    second = report.read_text()
    assert second.count("# Bug Hunt — automated triage") == 1
    assert len(second) > len(first)


def test_cli_severity_filter_excludes_low(tmp_path):
    # `look_ahead_iloc_minus_1` is medium; `broad_exception_swallow` is high.
    # With --severity high we should not see the medium one.
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/ml/feat.py": (
                "def x(df):\n"
                "    return df.iloc[-1]\n"
            ),
            "src/foo.py": (
                "def bad():\n"
                "    try:\n"
                "        risky()\n"
                "    except Exception:\n"
                "        pass\n"
            ),
        },
    )
    proc = _run_cli(repo, "--quick", "--json", "--severity", "high")
    assert proc.returncode in (0, 1)
    data = json.loads(proc.stdout)
    pats = [f["pattern"] for f in data["findings"]]
    assert "look_ahead_iloc_minus_1" not in pats
    # The high-severity broad except must still be present.
    assert any(p == "broad_exception_swallow" for p in pats)


def test_cli_exit_code_zero_when_no_critical(tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/clean.py": (
                "from datetime import datetime, UTC\n"
                "def now():\n"
                "    return datetime.now(UTC)\n"
            ),
        },
    )
    proc = _run_cli(repo, "--quick", "--json")
    data = json.loads(proc.stdout)
    assert data["critical_count"] == 0
    assert proc.returncode == 0


def test_cli_exit_code_one_when_critical(tmp_path):
    # Synthesize a critical finding by making an unparseable file (syntax error
    # → bug_hunt records `syntax_error` at severity=critical).
    repo = _make_synthetic_repo(
        tmp_path,
        {"src/broken.py": "def bad(:\n    pass\n"},
    )
    proc = _run_cli(repo, "--quick", "--json")
    data = json.loads(proc.stdout)
    assert data["critical_count"] >= 1
    assert proc.returncode == 1


def test_cli_quick_skips_mypy(tmp_path):
    repo = _make_synthetic_repo(
        tmp_path,
        {
            "src/foo.py": (
                "from datetime import datetime\n"
                "def x() -> int:\n"
                "    return datetime.now()\n"  # type-error: returns datetime not int
            ),
        },
    )
    proc = _run_cli(repo, "--quick", "--json")
    data = json.loads(proc.stdout)
    sources = {f["source"] for f in data["findings"]}
    assert "mypy" not in sources
