#!/usr/bin/env python3
"""Bug Hunter — static + domain-specific code scan for algo-trader.

Three classes of analysis:
  1. Off-the-shelf static analyzers (ruff, mypy --strict, bandit, vulture if installed)
  2. Domain-specific pattern scans (look-ahead bias, timezone-naive dt, broad except,
     missing fsync, Decimal/float mixing, etc.) using stdlib `ast`
  3. Test-suite health (collection counts, slow tests, optional flaky-test rerun)

Writes results to docs/bug_hunt.md as a severity-ranked, dated section. Append-only.

Usage:
  uv run python scripts/bug_hunt.py [--quick] [--json] [--severity SEV]
                                    [--with-flake-check] [--root PATH]

  --quick              Skip mypy and slow scans; just ruff + pattern scans (~5s)
  --json               Output as JSON to stdout instead of writing the markdown report
  --severity           Filter to severity at or above (low|medium|high|critical)
  --with-flake-check   Run pytest twice and diff to surface flaky tests (slow)
  --root               Override repo root (default: parent of this script)

Exit codes:
  0  no critical findings
  1  one or more critical findings (suitable for CI)
  2  config / tool-missing error preventing the scan from running
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Severity ranking
# ---------------------------------------------------------------------------

_SEVERITIES = ("low", "medium", "high", "critical")
_SEVERITY_RANK = {s: i for i, s in enumerate(_SEVERITIES)}


def _at_or_above(sev: str, threshold: str) -> bool:
    return _SEVERITY_RANK.get(sev, -1) >= _SEVERITY_RANK.get(threshold, 0)


# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """A single bug-hunt finding.

    `pattern` is a stable identifier (e.g. `broad_exception_swallow`, `mypy:error`).
    Operators can suppress with a `# noqa: bug-hunt:<pattern>` comment.
    """

    pattern: str
    severity: str
    file: str
    line: int
    detail: str
    source: str  # "pattern" | "ruff" | "mypy" | "bandit" | "vulture"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Pattern config
# ---------------------------------------------------------------------------

PATTERNS: dict[str, dict[str, Any]] = {
    "look_ahead_iloc_minus_1": {
        "severity": "medium",
        "scope": ("src/ml/", "src/data/"),
        "message": (
            "df/series.iloc[-1] outside generate_signals(): may leak future bar into "
            "training/feature-prep code"
        ),
    },
    "broad_exception_swallow": {
        "severity": "high",
        "scope": ("src/",),
        "message": (
            "broad except clause that swallows the error without logging — at least log "
            "the exception"
        ),
    },
    "timezone_naive_datetime": {
        "severity": "high",
        "scope": ("src/", "scripts/", "dashboard/api/"),
        "message": (
            "tz-naive datetime: datetime.now() without tz= or datetime.utcnow() "
            "(deprecated). Use datetime.now(UTC)"
        ),
    },
    "decimal_float_compare": {
        "severity": "medium",
        "scope": ("src/risk/", "src/execution/"),
        "message": (
            "comparison/arithmetic between a Decimal-typed value and a float literal — "
            "wrap the literal in Decimal()"
        ),
    },
    "missing_fsync_on_journal_write": {
        "severity": "high",
        "scope": ("src/journal/", "src/observability/"),
        "message": (
            "file open(..) + write(..) without subsequent .flush() and os.fsync() — "
            "appends may not survive process kill"
        ),
    },
    "race_condition_global_state": {
        "severity": "low",
        "scope": ("src/",),
        "message": (
            "global mutable variable assigned at module scope (PLW0603-class) — review "
            "for race conditions in async/threaded contexts"
        ),
    },
    "test_against_real_network": {
        "severity": "high",
        "scope": ("tests/",),
        "message": (
            "test calls urllib.request.urlopen / requests directly without "
            "monkeypatch in scope — likely hits real network"
        ),
    },
    "missing_input_validation": {
        "severity": "low",
        "scope": ("src/risk/", "src/backtest/"),
        "message": (
            "public function takes a numeric arg used as divisor without prior zero "
            "guard — TODO: validate"
        ),
    },
}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

_SKIP_DIRS = {".venv", "node_modules", ".git", "__pycache__", ".pytest_cache", ".ruff_cache"}


def _iter_python_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*.py"):
        parts = set(p.relative_to(root).parts)
        if parts & _SKIP_DIRS:
            continue
        out.append(p)
    return sorted(out)


def _in_scope(rel: str, scopes: tuple[str, ...]) -> bool:
    rel = rel.replace(os.sep, "/")
    return any(rel.startswith(s) for s in scopes)


def _is_suppressed(line_text: str, pattern: str) -> bool:
    """Check for `# noqa: bug-hunt:<pattern>` suppression comment on the line."""
    return f"bug-hunt:{pattern}" in line_text


# ---------------------------------------------------------------------------
# Pattern visitors
# ---------------------------------------------------------------------------


def _qualified_attr(node: ast.AST) -> str:
    """Return e.g. 'datetime.now' for an ast.Attribute call. Empty if not applicable."""
    if isinstance(node, ast.Attribute):
        prefix = _qualified_attr(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


class _PatternVisitor(ast.NodeVisitor):
    """One visitor per file. Collects findings against `self.findings`."""

    def __init__(self, rel_path: str, source_lines: list[str]) -> None:
        self.rel_path = rel_path
        self.lines = source_lines
        self.findings: list[Finding] = []
        # Track current function name so we can recognize `generate_signals`.
        self._fn_stack: list[str] = []
        # Decimal-typed local names (annotated args/locals).
        self._decimal_names: set[str] = set()
        # Global-mutable assigns: track module-level Name assigns.
        self._at_module_level = True
        # Names imported from the stdlib `datetime` module — used to disambiguate
        # `utcnow()` (real, deprecated) from a local helper of the same name.
        self._datetime_imports: set[str] = set()
        # Cache: does this test file mock the network? Recognize the standard
        # mocking surfaces — ``monkeypatch`` / ``respx`` / ``httpx_mock`` /
        # ``mocker`` (pytest-mock) AND ``unittest.mock`` (``patch`` / ``Mock``).
        joined_source = "\n".join(source_lines)
        self._has_monkeypatch = any(
            tok in joined_source
            for tok in (
                "monkeypatch",
                "respx",
                "httpx_mock",
                "MockerFixture",
                "mocker",
                "unittest.mock",
                "from unittest import mock",
            )
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "datetime":
            for alias in node.names:
                self._datetime_imports.add(alias.asname or alias.name)
        self.generic_visit(node)

    # -- helpers ----------------------------------------------------------

    def _line_text(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1]
        return ""

    def _add(self, pattern: str, lineno: int, detail: str | None = None) -> None:
        if pattern not in PATTERNS:
            return
        cfg = PATTERNS[pattern]
        if not _in_scope(self.rel_path, cfg["scope"]):
            return
        line_text = self._line_text(lineno)
        if _is_suppressed(line_text, pattern):
            return
        self.findings.append(
            Finding(
                pattern=pattern,
                severity=cfg["severity"],
                file=self.rel_path,
                line=lineno,
                detail=detail or cfg["message"],
                source="pattern",
            )
        )

    # -- scope tracking ---------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._fn_stack.append(node.name)
        prev_top = self._at_module_level
        self._at_module_level = False
        # Collect Decimal-annotated args.
        for arg in (*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs):
            if arg.annotation and self._is_decimal_anno(arg.annotation):
                self._decimal_names.add(arg.arg)
        self.generic_visit(node)
        self._at_module_level = prev_top
        self._fn_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        # ast.FunctionDef and ast.AsyncFunctionDef share the .args/.name shape.
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        prev_top = self._at_module_level
        self._at_module_level = False
        self.generic_visit(node)
        self._at_module_level = prev_top

    @staticmethod
    def _is_decimal_anno(node: ast.expr) -> bool:
        if isinstance(node, ast.Name) and node.id == "Decimal":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "Decimal":
            return True
        if isinstance(node, ast.Subscript):
            return _PatternVisitor._is_decimal_anno(node.value)
        return False

    # -- annotated-assign: locals typed as Decimal -----------------------

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and self._is_decimal_anno(node.annotation):
            self._decimal_names.add(node.target.id)
        self.generic_visit(node)

    # -- broad_exception_swallow ----------------------------------------

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # Bare `except:` or `except Exception:` whose body has no logger/raise.
        is_broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
        )
        if is_broad and self._body_silently_swallows(node.body):
            self._add("broad_exception_swallow", node.lineno)
        self.generic_visit(node)

    @staticmethod
    def _body_silently_swallows(body: list[ast.stmt]) -> bool:
        """True if the except body has no logging/raise/print/return-with-info."""
        for stmt in body:
            # raise / re-raise — not silent.
            if isinstance(stmt, ast.Raise):
                return False
            # explicit logger.* / log.* / logging.* / structlog.* / print()
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                qn = _qualified_attr(stmt.value.func)
                if any(
                    tok in qn
                    for tok in (
                        "log",
                        "Log",
                        "warn",
                        "error",
                        "exception",
                        "critical",
                        "print",
                        "alert",
                        "discord",
                        "metric",
                    )
                ):
                    return False
            # `return <expr-with-error-info>` — heuristically OK
            if isinstance(stmt, ast.Return):
                return False
        # Pure `pass` / `continue` / `break` only:
        return all(isinstance(s, (ast.Pass, ast.Continue, ast.Break)) for s in body)

    # -- timezone_naive_datetime ----------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        qn = _qualified_attr(node.func)
        # datetime.utcnow() — always tz-naive and deprecated. Bare `utcnow()`
        # is only a real hit when imported from the stdlib `datetime` module;
        # otherwise it's a local helper (we have one in src/execution/orders.py
        # that returns datetime.now(UTC) — calling that is correct).
        if qn.endswith("datetime.utcnow") or (
            qn == "utcnow" and "utcnow" in self._datetime_imports
        ):
            self._add("timezone_naive_datetime", node.lineno, "datetime.utcnow() is tz-naive")
        # datetime.now() with no args — tz-naive.
        elif qn.endswith("datetime.now") or qn == "now":
            has_tz = bool(node.args) or any(kw.arg in ("tz", "tzinfo") for kw in node.keywords)
            if not has_tz and self._looks_like_datetime_now(node):
                self._add("timezone_naive_datetime", node.lineno, "datetime.now() without tz arg")
        # urlopen / requests.get / requests.post in tests without monkeypatch
        if self.rel_path.startswith("tests/") and not self._has_monkeypatch:
            if qn.endswith("urlopen") or qn in (
                "requests.get",
                "requests.post",
                "requests.put",
                "requests.delete",
            ):
                self._add("test_against_real_network", node.lineno)
        self.generic_visit(node)

    def _looks_like_datetime_now(self, node: ast.Call) -> bool:
        """Filter out `mock.now()`, `time.now()` etc. — only flag if the receiver
        looks plausibly datetime-y. Conservative: trigger if receiver contains
        'datetime' or if `now` was imported from `datetime` directly."""
        if isinstance(node.func, ast.Attribute):
            qn = _qualified_attr(node.func)
            return "datetime" in qn or "Datetime" in qn
        if isinstance(node.func, ast.Name) and node.func.id in {"now", "utcnow"}:
            return node.func.id in self._datetime_imports
        return False

    # -- look_ahead_iloc_minus_1 -----------------------------------------

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # Detect `<something>.iloc[-1]` regardless of how something is built.
        if (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == "iloc"
            and self._is_minus_one_index(node.slice)
        ):
            # Skip if the enclosing function is `generate_signals`.
            if not (self._fn_stack and self._fn_stack[-1] == "generate_signals"):
                self._add("look_ahead_iloc_minus_1", node.lineno)
        self.generic_visit(node)

    @staticmethod
    def _is_minus_one_index(slice_node: ast.expr) -> bool:
        if isinstance(slice_node, ast.UnaryOp) and isinstance(slice_node.op, ast.USub):
            inner = slice_node.operand
            if isinstance(inner, ast.Constant) and inner.value == 1:
                return True
        # Older astor representation:
        return isinstance(slice_node, ast.Constant) and slice_node.value == -1

    # -- decimal_float_compare ------------------------------------------

    def visit_Compare(self, node: ast.Compare) -> None:
        self._check_decimal_float(node.left, node.comparators)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        # Decimal +/-/* float-literal
        self._check_decimal_float(node.left, [node.right])
        self.generic_visit(node)

    def _check_decimal_float(self, left: ast.expr, rights: list[ast.expr]) -> None:
        left_is_decimal = isinstance(left, ast.Name) and left.id in self._decimal_names
        if not left_is_decimal:
            # Try the right side as the Decimal var.
            for r in rights:
                if isinstance(r, ast.Name) and r.id in self._decimal_names:
                    left_is_decimal = True
                    break
        if not left_is_decimal:
            return
        # Did any of the other operands include a float literal?
        operands = [left, *rights]
        for op in operands:
            if isinstance(op, ast.Constant) and isinstance(op.value, float):
                self._add("decimal_float_compare", op.lineno)
                return

    # -- race_condition_global_state ------------------------------------

    def visit_Global(self, node: ast.Global) -> None:
        # `global X` inside a function body that's NOT a documented singleton.
        # We defer to ruff's PLW0603 ignore-list in pyproject; here we just flag.
        self._add(
            "race_condition_global_state",
            node.lineno,
            f"`global {', '.join(node.names)}` — review for race conditions",
        )
        self.generic_visit(node)

    # -- missing_input_validation ---------------------------------------
    # We don't try to perfectly track every divisor; instead, flag public
    # functions whose body contains a BinOp Div using one of the args.

    def visit_Module(self, node: ast.Module) -> None:
        for stmt in node.body:
            if isinstance(stmt, ast.FunctionDef) and not stmt.name.startswith("_"):
                self._maybe_flag_unvalidated_divisor(stmt)
        self.generic_visit(node)

    def _maybe_flag_unvalidated_divisor(self, fn: ast.FunctionDef) -> None:
        arg_names = {a.arg for a in fn.args.args}
        if not arg_names:
            return
        body_src = "\n".join(self._line_text(line) for line in range(fn.lineno, fn.end_lineno or fn.lineno))
        # Quick lexical guard check.
        if "raise" in body_src or "assert" in body_src or ("if" in body_src and "0" in body_src):
            # has *some* guard logic; skip to avoid noise
            return
        for sub in ast.walk(fn):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, (ast.Div, ast.FloorDiv, ast.Mod)):
                if isinstance(sub.right, ast.Name) and sub.right.id in arg_names:
                    self._add(
                        "missing_input_validation",
                        sub.lineno,
                        f"public function `{fn.name}` divides by arg `{sub.right.id}` without prior zero guard",
                    )
                    return  # one finding per function

    # -- missing_fsync_on_journal_write (sequential body scan) ----------

    def visit_With(self, node: ast.With) -> None:
        self._scan_with_block(node)
        self.generic_visit(node)

    def _scan_with_block(self, node: ast.With) -> None:
        # Look for `with open(...) as f:` and check the body for write+flush+fsync.
        if not _in_scope(self.rel_path, PATTERNS["missing_fsync_on_journal_write"]["scope"]):
            return
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call) and _qualified_attr(call.func).endswith("open"):
                # Only flag append/write modes
                mode = self._extract_open_mode(call)
                if not mode or "r" == mode.strip("rt+"):
                    continue
                wrote, flushed, fsynced = self._inspect_body_writes(node.body)
                if wrote and not (flushed and fsynced):
                    self._add(
                        "missing_fsync_on_journal_write",
                        node.lineno,
                        "open(...) + write(...) without subsequent .flush() AND os.fsync()",
                    )

    @staticmethod
    def _extract_open_mode(call: ast.Call) -> str:
        if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
            v = call.args[1].value
            if isinstance(v, str):
                return v
        for kw in call.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
        return "r"  # default

    @staticmethod
    def _inspect_body_writes(body: list[ast.stmt]) -> tuple[bool, bool, bool]:
        wrote = flushed = fsynced = False
        for stmt in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(stmt, ast.Call):
                qn = _qualified_attr(stmt.func)
                if qn.endswith(".write") or qn == "write":
                    wrote = True
                elif qn.endswith(".flush") or qn == "flush":
                    flushed = True
                elif qn.endswith("fsync"):
                    fsynced = True
        return wrote, flushed, fsynced


# ---------------------------------------------------------------------------
# Pattern scan driver
# ---------------------------------------------------------------------------


def run_pattern_scan(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in _iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        # Skip our own scanner (would self-flag patterns documented as strings).
        if rel == "scripts/bug_hunt.py" or rel.startswith("tests/unit/scripts/test_bug_hunt"):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            findings.append(
                Finding(
                    pattern="syntax_error",
                    severity="critical",
                    file=rel,
                    line=1,
                    detail="ast.parse failed — file does not parse as valid Python",
                    source="pattern",
                )
            )
            continue
        v = _PatternVisitor(rel, source.splitlines())
        v.visit(tree)
        findings.extend(v.findings)
    return findings


# ---------------------------------------------------------------------------
# Off-the-shelf tool runners
# ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str, str]:
    """Run a command, return (exit_code, stdout, stderr). Tool-not-installed -> (-1, "", msg)."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as e:
        return -1, "", f"tool not found: {e}"
    except subprocess.TimeoutExpired:
        return -2, "", f"timeout after {timeout}s: {shlex.join(cmd)}"


def _tool_version(cmd: list[str], root: Path) -> str:
    code, out, err = _run(cmd, root, timeout=30)
    if code < 0:
        return "not installed"
    return (out + err).strip().splitlines()[0] if (out or err) else "unknown"


def run_ruff(root: Path) -> tuple[list[Finding], str]:
    targets = ["src", "tests", "scripts", "dashboard/api"]
    targets = [t for t in targets if (root / t).exists()]
    cmd = ["uv", "run", "--no-sync", "ruff", "check", *targets, "--output-format", "json"]
    code, out, err = _run(cmd, root)
    if code < 0:
        return [], f"ruff: {err}"
    findings: list[Finding] = []
    if not out.strip():
        return findings, ""
    try:
        items = json.loads(out)
    except json.JSONDecodeError:
        return findings, f"ruff: could not parse JSON output (exit {code})"
    for item in items:
        rel = Path(item.get("filename", "")).relative_to(root) if Path(item.get("filename", "")).is_absolute() else Path(item.get("filename", ""))
        rel_s = rel.as_posix()
        rule = item.get("code") or "?"
        findings.append(
            Finding(
                pattern=f"ruff:{rule}",
                # Ruff rules vary in severity; treat any finding as "high" because
                # the suite is configured to be green at HEAD.
                severity="high",
                file=rel_s,
                line=int(item.get("location", {}).get("row", 1)),
                detail=item.get("message", ""),
                source="ruff",
            )
        )
    return findings, ""


def run_mypy(root: Path) -> tuple[list[Finding], str]:
    cmd = ["uv", "run", "--no-sync", "mypy", "src", "--strict", "--ignore-missing-imports", "--no-color-output"]
    code, out, err = _run(cmd, root, timeout=600)
    if code < 0:
        return [], f"mypy: {err}"
    findings: list[Finding] = []
    # mypy line: src/foo.py:42: error: ... [code]
    rx = re.compile(r"^(?P<file>[^:\s][^:]*):(?P<line>\d+):(?:\d+:)?\s*(?P<level>error|note|warning):\s*(?P<msg>.*?)\s*(?:\[(?P<code>[a-zA-Z0-9_-]+)\])?$")
    for raw in out.splitlines():
        m = rx.match(raw.strip())
        if not m:
            continue
        level = m.group("level")
        if level != "error":
            continue
        sev = "medium"  # mypy strict errors are real but rarely runtime-critical
        findings.append(
            Finding(
                pattern=f"mypy:{m.group('code') or 'error'}",
                severity=sev,
                file=m.group("file"),
                line=int(m.group("line")),
                detail=m.group("msg"),
                source="mypy",
            )
        )
    return findings, ""


def run_bandit(root: Path) -> tuple[list[Finding], str]:
    cmd = ["uv", "run", "--no-sync", "bandit", "-r", "src", "scripts", "-f", "json", "-q"]
    code, out, err = _run(cmd, root, timeout=300)
    if code < 0:
        return [], "bandit not installed (install with `uv add --dev bandit`)"
    findings: list[Finding] = []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return findings, f"bandit: could not parse JSON (exit {code})"
    sev_map = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
    for r in data.get("results", []):
        rel = Path(r.get("filename", ""))
        rel_s = rel.relative_to(root).as_posix() if rel.is_absolute() and str(rel).startswith(str(root)) else rel.as_posix()
        findings.append(
            Finding(
                pattern=f"bandit:{r.get('test_id', '?')}",
                severity=sev_map.get(r.get("issue_severity", "MEDIUM").upper(), "medium"),
                file=rel_s,
                line=int(r.get("line_number", 1)),
                detail=r.get("issue_text", ""),
                source="bandit",
            )
        )
    return findings, ""


def run_vulture(root: Path) -> tuple[list[Finding], str]:
    cmd = ["uv", "run", "--no-sync", "vulture", "src", "--min-confidence", "70"]
    code, out, err = _run(cmd, root, timeout=120)
    if code < 0:
        return [], "vulture not installed (install with `uv add --dev vulture`)"
    findings: list[Finding] = []
    # vulture format: src/foo.py:42: unused function 'bar' (90% confidence)
    rx = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+):\s*(?P<msg>.+)$")
    for raw in out.splitlines():
        m = rx.match(raw.strip())
        if not m:
            continue
        findings.append(
            Finding(
                pattern="vulture:dead_code",
                severity="low",
                file=m.group("file"),
                line=int(m.group("line")),
                detail=m.group("msg"),
                source="vulture",
            )
        )
    return findings, ""


# ---------------------------------------------------------------------------
# Test-suite health
# ---------------------------------------------------------------------------


@dataclass
class TestHealth:
    collected: int = 0
    by_module: dict[str, int] = field(default_factory=dict)
    slowest: list[tuple[str, float]] = field(default_factory=list)  # (nodeid, seconds)
    flaky: list[str] = field(default_factory=list)
    coverage_gaps: list[tuple[str, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def run_test_health(root: Path, *, with_flake: bool = False) -> TestHealth:
    h = TestHealth()
    # Collect-only — count tests.
    cmd = ["uv", "run", "--no-sync", "pytest", "tests/unit", "-q", "--collect-only"]
    code, out, err = _run(cmd, root, timeout=120)
    if code < 0:
        h.notes.append(f"pytest: {err}")
        return h
    # `-q --collect-only` lists nodeids, then a footer like "N tests collected in 0.42s".
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.startswith("="):
            continue
        if "tests collected" in line or "test collected" in line:
            m = re.match(r"(\d+)\s+tests?\s+collected", line)
            if m:
                h.collected = int(m.group(1))
            continue
        if "::" in line:
            mod = line.split("::", 1)[0]
            h.by_module[mod] = h.by_module.get(mod, 0) + 1

    # Duration of slowest tests via --durations=10 (no actual run cost beyond the durations report)
    cmd2 = ["uv", "run", "--no-sync", "pytest", "tests/unit", "-q", "--durations=10", "-x", "--no-header"]
    # NOTE: this DOES run the tests; protect with a timeout and only call when not --quick.
    code2, out2, err2 = _run(cmd2, root, timeout=600)
    if code2 in (0, 1) and out2:
        # Lines look like:  0.42s call     tests/unit/foo.py::test_bar
        rx = re.compile(r"^\s*([\d.]+)s\s+(?:call|setup|teardown)\s+(\S+)$")
        for raw in out2.splitlines():
            m = rx.match(raw)
            if m:
                t = float(m.group(1))
                if t >= 0.05:  # only report >= 50ms
                    h.slowest.append((m.group(2), t))
        h.slowest.sort(key=lambda x: -x[1])
        h.slowest = h.slowest[:10]
    elif code2 < 0:
        h.notes.append(f"pytest --durations: {err2}")

    if with_flake:
        # Run twice, diff failure sets.
        first_failed = _collect_failed(root)
        second_failed = _collect_failed(root)
        flaky = (first_failed ^ second_failed)
        h.flaky = sorted(flaky)

    # Coverage gaps (read .coverage if exists; do not require pytest-cov rerun).
    cov_path = root / ".coverage"
    if cov_path.exists():
        try:
            import sqlite3
            con = sqlite3.connect(str(cov_path))
            try:
                rows = con.execute(
                    "SELECT file.path, COUNT(line_bits.numbits) FROM file LEFT JOIN line_bits ON file.id = line_bits.file_id GROUP BY file.path"
                ).fetchall()
                for path_str, _ in rows:
                    if "/src/" in path_str or path_str.startswith("src/"):
                        # We can't easily compute exact coverage % without the full machinery;
                        # just note that coverage data exists.
                        pass
                h.notes.append(f"coverage db present ({len(rows)} files tracked)")
            finally:
                con.close()
        except Exception as e:  # pragma: no cover - defensive
            h.notes.append(f"coverage read failed: {e}")
    return h


def _collect_failed(root: Path) -> set[str]:
    code, out, err = _run(["uv", "run", "--no-sync", "pytest", "tests/unit", "-q", "--no-header", "--tb=no"], root, timeout=600)
    failed: set[str] = set()
    if code < 0:
        return failed
    for raw in out.splitlines():
        if raw.startswith("FAILED "):
            failed.add(raw.split(" ", 1)[1].split(" ", 1)[0])
    return failed


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_markdown(
    findings: list[Finding],
    health: TestHealth | None,
    versions: dict[str, str],
    *,
    timestamp: datetime,
    notes: list[str],
) -> str:
    by_sev: dict[str, list[Finding]] = {s: [] for s in _SEVERITIES}
    for f in findings:
        by_sev.setdefault(f.severity, []).append(f)
    for bucket in by_sev.values():
        bucket.sort(key=lambda f: (f.file, f.line))

    lines: list[str] = []
    lines.append(f"## Scan {timestamp.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    total = sum(len(v) for v in by_sev.values())
    lines.append(
        f"_Findings: {len(by_sev['critical'])} critical, {len(by_sev['high'])} high, "
        f"{len(by_sev['medium'])} medium, {len(by_sev['low'])} low (total {total})._"
    )
    lines.append("")

    for sev in reversed(_SEVERITIES):
        bucket = by_sev.get(sev, [])
        if not bucket:
            continue
        lines.append(f"### {sev.capitalize()}")
        lines.append("")
        lines.append("| Pattern | File | Line | Detail |")
        lines.append("|---|---|---|---|")
        for f in bucket:
            detail = f.detail.replace("|", "\\|").replace("\n", " ")
            if len(detail) > 200:
                detail = detail[:197] + "..."
            lines.append(f"| `{f.pattern}` | `{f.file}` | {f.line} | {detail} |")
        lines.append("")

    if health is not None:
        lines.append("### Test-suite health")
        lines.append("")
        lines.append(f"- {health.collected} tests collected across {len(health.by_module)} modules")
        if health.slowest:
            lines.append("- Slowest tests:")
            for nodeid, t in health.slowest[:5]:
                lines.append(f"  - `{nodeid}` — {t:.2f}s")
        if health.flaky:
            lines.append(f"- Flaky candidates: {len(health.flaky)}")
            for f in health.flaky[:10]:
                lines.append(f"  - `{f}`")
        if health.notes:
            for n in health.notes:
                lines.append(f"- _note_: {n}")
        lines.append("")

    lines.append("### Tool versions")
    lines.append("")
    for name, ver in versions.items():
        lines.append(f"- {name}: {ver}")
    lines.append("")

    if notes:
        lines.append("### Scan notes")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Atomic markdown append
# ---------------------------------------------------------------------------

_REPORT_HEADER = """# Bug Hunt — automated triage

Each scan adds a section dated by UTC timestamp. Findings are sorted within each
severity tier by (file, line). The operator triages: fix, suppress (with
`# noqa: bug-hunt:<pattern>` on the offending line), or document why it's a
false positive.

Run with: `uv run python scripts/bug_hunt.py` (or `--quick` for a fast loop).

"""


def append_report(report_path: Path, section: str) -> None:
    """Append `section` to `report_path`, atomically. Initialize header if missing."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    existing = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    if not existing:
        existing = _REPORT_HEADER
    new_content = existing + section
    # Atomic: write to sibling tempfile, fsync, rename.
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(report_path.parent),
        prefix=".bug_hunt.",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(new_content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, report_path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--quick", action="store_true", help="skip mypy and slow scans")
    p.add_argument("--json", action="store_true", help="emit JSON to stdout instead of writing markdown")
    p.add_argument(
        "--severity",
        choices=_SEVERITIES,
        default="low",
        help="filter findings to severity at or above (default low → no filter)",
    )
    p.add_argument("--with-flake-check", action="store_true", help="rerun pytest twice and diff for flaky tests")
    p.add_argument("--root", type=Path, default=None, help="repo root override")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = (args.root or Path(__file__).resolve().parent.parent).resolve()
    if not (root / "pyproject.toml").exists() and not (root / "src").exists():
        print(f"error: --root {root} doesn't look like a project root", file=sys.stderr)
        return 2

    timestamp = datetime.now(UTC)
    findings: list[Finding] = []
    notes: list[str] = []

    # Class 2 always runs (cheap, stdlib only).
    findings.extend(run_pattern_scan(root))

    # Class 1: ruff (fast) always runs.
    ruff_findings, ruff_note = run_ruff(root)
    findings.extend(ruff_findings)
    if ruff_note:
        notes.append(ruff_note)

    # Off-the-shelf, slower:
    if not args.quick:
        mypy_findings, mypy_note = run_mypy(root)
        findings.extend(mypy_findings)
        if mypy_note:
            notes.append(mypy_note)

        bandit_findings, bandit_note = run_bandit(root)
        findings.extend(bandit_findings)
        if bandit_note:
            notes.append(bandit_note)

        vulture_findings, vulture_note = run_vulture(root)
        findings.extend(vulture_findings)
        if vulture_note:
            notes.append(vulture_note)

    # Class 3: test-suite health (only when not --quick or when --with-flake-check)
    health: TestHealth | None = None
    if not args.quick:
        health = run_test_health(root, with_flake=args.with_flake_check)

    # Severity filter.
    findings = [f for f in findings if _at_or_above(f.severity, args.severity)]

    # Tool versions
    versions = {
        "ruff": _tool_version(["uv", "run", "--no-sync", "ruff", "--version"], root),
        "mypy": _tool_version(["uv", "run", "--no-sync", "mypy", "--version"], root),
        "bandit": _tool_version(["uv", "run", "--no-sync", "bandit", "--version"], root),
        "vulture": _tool_version(["uv", "run", "--no-sync", "vulture", "--version"], root),
    }

    # Output
    critical_count = sum(1 for f in findings if f.severity == "critical")

    if args.json:
        payload = {
            "timestamp": timestamp.isoformat(),
            "root": str(root),
            "findings": [f.to_dict() for f in findings],
            "test_health": (
                {
                    "collected": health.collected,
                    "by_module": health.by_module,
                    "slowest": health.slowest,
                    "flaky": health.flaky,
                    "notes": health.notes,
                }
                if health is not None
                else None
            ),
            "versions": versions,
            "notes": notes,
            "critical_count": critical_count,
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        section = render_markdown(findings, health, versions, timestamp=timestamp, notes=notes)
        report = root / "docs" / "bug_hunt.md"
        append_report(report, section)
        print(f"Bug hunt complete. {len(findings)} findings ({critical_count} critical). Report: {report}")

    return 1 if critical_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
