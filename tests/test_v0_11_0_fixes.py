"""Adversarial regression tests for the v0.11.0 correctness fixes.

Four defects pinned end-to-end against the shipped v0.10.0 source:

* ``fix-go-interface-methods-invisible`` — ``LANGUAGE_RULES["go"]`` mapped
  ``function_declaration`` / ``method_declaration`` / ``type_spec`` but omitted
  the ``method_elem`` node that holds a Go interface method signature, so
  ``type Iface interface { Method(x int) int }`` yielded only the ``Iface``
  type symbol and zero method symbols. A truthful ``add Method scope=Iface``
  / ``signature_change Method scope=Iface`` therefore false-failed (Java
  abstract methods, TS ``method_signature``, and C++ header method declarations
  are all parsed, so Go interfaces were the one supported language whose
  declaration/signature split was uncovered). A one-line rule addition now
  parses them; the interface scope flows through the existing ``_walk`` path.
* ``fix-ruby-qualified-class-method-scope`` — ``_walk`` had no branch that
  reads a Ruby ``singleton_method``'s ``object`` field, so a top-level
  ``def Foo.bar`` (a class method defined from outside the class) was emitted
  with ``scope=''`` while the class-block ``def self.bar`` form correctly keyed
  by ``scope='Foo'``. A contract-following agent emitting
  ``signature_change bar scope=Foo`` therefore false-failed a truthful edit —
  the same over-flag class closed nine times before. ``_walk`` now reads the
  ``object`` when it is a concrete ``constant`` (NOT ``self``) and promotes to
  method, so the two forms key identically without touching the class-block form.
* ``fix-cli-claim-file-bad-action-traceback`` — the single-file
  ``--claim-file`` path called ``_actions_from_payload`` OUTSIDE any
  try/except, and ``ClaimedAction.from_dict`` raises ``KeyError`` (not
  ``ValueError``) when an action dict omits ``kind`` / ``symbol``, so a
  malformed claim file leaked an unhandled traceback at exit 1 — the exact
  class the v0.7.0 traceback fix targeted, left incomplete for the single-file
  path. The call is now wrapped so a bad claim file exits 2 cleanly.
* ``fix-bench-non-object-row-crash`` — ``run_bench`` read
  ``record.get(...)`` BEFORE its try/except, so a JSONL row that is valid JSON
  but not an object (a bare list / number / string) raised ``AttributeError``
  outside the ``except Exception`` and crashed the bench, breaking the
  "robust to bad rows" contract. The label read now lives inside the try with
  an ``isinstance`` guard, so a non-object row is scored as a parse error.

Each red->green test fails on the pre-fix source and passes after the fix;
the guards (lying no-op claim still fails; class-block self form unchanged;
wrong-scope claim still fails) ensure the fixes do not over-correct.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from diffgate.bench import run_bench
from diffgate.cli import _actions_from_payload, app
from diffgate.parsers import parse_symbols
from diffgate.verifier import EditClaim, verify

runner = CliRunner()


# --------------------------------------------------------------------------- #
# fix-go-interface-methods-invisible (src/diffgate/parsers.py)
# --------------------------------------------------------------------------- #
def test_go_interface_method_is_parsed_with_scope() -> None:
    """`type Iface interface { Method(x int) int }` must emit a `Method`
    method symbol keyed by `scope='Iface'`. Red->green: pre-fix only `Iface`
    was emitted.
    """
    syms = parse_symbols(
        "package p\ntype Iface interface {\n  Method(x int) int\n}\n", "go"
    )
    method = next(s for s in syms if s.name == "Method")
    assert (method.scope, method.kind) == ("Iface", "method")
    assert method.signature == "(x int)"
    assert method.body_hash == ""


def test_go_interface_truthful_add_method_passes(tmp_path: Path) -> None:
    """Adding a method to a Go interface with `add Method scope=Iface` must
    PASS. Red->green: pre-fix `Method` was invisible so the claim false-failed.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "package p\ntype Iface interface {\n  A(x int) int\n}\n",
            "after_blob": (
                "package p\ntype Iface interface {\n"
                "  A(x int) int\n  Method(y int) int\n}\n"
            ),
            "language": "go",
            "claimed_actions": [
                {"kind": "add", "symbol": "Method", "scope": "Iface"}
            ],
        }
    )
    assert verify(claim).passed is True, [
        m.to_dict() for m in verify(claim).mismatches
    ]


def test_go_interface_truthful_signature_change_passes() -> None:
    """A real signature change on a Go interface method with a scoped claim
    must PASS. Red->green: pre-fix `Method` was invisible.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "package p\ntype Iface interface {\n  Method(x int) int\n}\n",
            "after_blob": "package p\ntype Iface interface {\n  Method(x int, y int) int\n}\n",
            "language": "go",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "Method", "scope": "Iface"}
            ],
        }
    )
    assert verify(claim).passed is True, [
        m.to_dict() for m in verify(claim).mismatches
    ]


def test_go_interface_lying_noop_signature_change_fails() -> None:
    """A LYING `signature_change Method scope=Iface` on a no-op edit must still
    FAIL. Guard.
    """
    blob = "package p\ntype Iface interface {\n  Method(x int) int\n}\n"
    claim = EditClaim.from_dict(
        {
            "before_blob": blob,
            "after_blob": blob,
            "language": "go",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "Method", "scope": "Iface"}
            ],
        }
    )
    assert verify(claim).passed is False


# --------------------------------------------------------------------------- #
# fix-ruby-qualified-class-method-scope (src/diffgate/parsers.py)
# --------------------------------------------------------------------------- #
def test_ruby_qualified_class_method_carries_scope() -> None:
    """`def Foo.bar` at top level must key by `scope='Foo'` (and promote to
    method). Red->green: pre-fix `bar` was emitted with `scope=''`.
    """
    syms = parse_symbols("def Foo.bar\n  1\nend\n", "ruby")
    bar = next(s for s in syms if s.name == "bar")
    assert (bar.scope, bar.kind) == ("Foo", "method")


def test_ruby_qualified_class_method_truthful_signature_change_passes() -> None:
    """A real signature change on `def Foo.bar` with `scope=Foo` must PASS.
    Red->green: pre-fix the scoped claim false-failed.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "def Foo.bar\n  1\nend\n",
            "after_blob": "def Foo.bar(x)\n  1\nend\n",
            "language": "ruby",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "Foo"}
            ],
        }
    )
    assert verify(claim).passed is True, [
        m.to_dict() for m in verify(claim).mismatches
    ]


def test_ruby_class_block_self_form_still_keys_by_class_scope() -> None:
    """The class-block `def self.bar` inside `class Foo` must still key by
    `scope='Foo'` — the fix must not touch the working form. Guard.
    """
    syms = parse_symbols("class Foo\n  def self.bar\n    1\n  end\nend\n", "ruby")
    bar = next(s for s in syms if s.name == "bar")
    assert (bar.scope, bar.kind) == ("Foo", "method")


def test_ruby_class_block_self_form_truthful_scoped_claim_passes() -> None:
    """A truthful scoped claim on the class-block `def self.bar` form must
    still PASS. Guard (regression for the working path).
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "class Foo\n  def self.bar\n    1\n  end\nend\n",
            "after_blob": "class Foo\n  def self.bar(x)\n    1\n  end\nend\n",
            "language": "ruby",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "Foo"}
            ],
        }
    )
    assert verify(claim).passed is True, [
        m.to_dict() for m in verify(claim).mismatches
    ]


def test_ruby_qualified_method_wrong_scope_claim_fails() -> None:
    """Claiming `signature_change bar scope=Bar` when bar lives on `Foo` must
    still FAIL. Guard.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "def Foo.bar\n  1\nend\n",
            "after_blob": "def Foo.bar(x)\n  1\nend\n",
            "language": "ruby",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "Bar"}
            ],
        }
    )
    assert verify(claim).passed is False


# --------------------------------------------------------------------------- #
# fix-cli-claim-file-bad-action-traceback (src/diffgate/cli.py)
# --------------------------------------------------------------------------- #
def test_actions_from_payload_missing_kind_raises() -> None:
    """A claim-file action missing `kind` raises (so the CLI wrap can catch it).
    Red->green: pre-fix this raised KeyError; it still raises, but the CLI
    now catches it. This pins the raise so the wrap stays meaningful.
    """
    raised = False
    try:
        _actions_from_payload({"claimed_actions": [{"symbol": "foo"}]})
    except (KeyError, ValueError):
        raised = True
    assert raised


def test_cli_claim_file_missing_kind_exits_2_no_traceback(
    tmp_path: Path,
) -> None:
    """A single-file --claim-file whose action omits `kind` must exit 2 with
    a clean 'claim-file error' message and NO traceback. Red->green: pre-fix
    this leaked a KeyError traceback at exit 1.
    """
    before = tmp_path / "b.py"
    after = tmp_path / "a.py"
    before.write_text("def foo():\n    pass\n", encoding="utf-8")
    after.write_text("def bar():\n    pass\n", encoding="utf-8")
    claim_file = tmp_path / "claim.json"
    claim_file.write_text(
        json.dumps(
            {"language": "python", "claimed_actions": [{"symbol": "foo"}]}
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "verify",
            "--before",
            str(before),
            "--after",
            str(after),
            "--claim-file",
            str(claim_file),
        ],
    )
    assert result.exit_code == 2
    assert "claim-file error" in result.output
    assert "Traceback" not in result.output


def test_cli_claim_file_missing_symbol_exits_2_no_traceback(
    tmp_path: Path,
) -> None:
    """Same guard for an action missing `symbol`."""
    before = tmp_path / "b.py"
    after = tmp_path / "a.py"
    before.write_text("def foo():\n    pass\n", encoding="utf-8")
    after.write_text("def foo():\n    pass\n", encoding="utf-8")
    claim_file = tmp_path / "claim.json"
    claim_file.write_text(
        json.dumps({"language": "python", "claimed_actions": [{"kind": "add"}]}),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "verify",
            "--before",
            str(before),
            "--after",
            str(after),
            "--claim-file",
            str(claim_file),
        ],
    )
    assert result.exit_code == 2
    assert "claim-file error" in result.output
    assert "Traceback" not in result.output


def test_cli_claim_file_valid_still_passes(tmp_path: Path) -> None:
    """A valid single-file claim file must still work. Guard (regression for
    the happy path the wrap now sits on).
    """
    before = tmp_path / "b.py"
    after = tmp_path / "a.py"
    before.write_text("def foo():\n    pass\n", encoding="utf-8")
    after.write_text("def bar():\n    pass\n", encoding="utf-8")
    claim_file = tmp_path / "claim.json"
    claim_file.write_text(
        json.dumps(
            {
                "language": "python",
                "claimed_actions": [
                    {"kind": "rename", "symbol": "foo", "new_symbol": "bar"}
                ],
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "verify",
            "--before",
            str(before),
            "--after",
            str(after),
            "--claim-file",
            str(claim_file),
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["passed"] is True


# --------------------------------------------------------------------------- #
# fix-bench-non-object-row-crash (src/diffgate/bench.py)
# --------------------------------------------------------------------------- #
def test_bench_non_object_row_does_not_crash(tmp_path: Path) -> None:
    """A JSONL row that is valid JSON but not an object (a bare list) must not
    crash run_bench; it must be counted as a parse error. Red->green: pre-fix
    this raised AttributeError and aborted the run.
    """
    trace = tmp_path / "trace.jsonl"
    trace.write_text("[1, 2, 3]\n", encoding="utf-8")
    result = run_bench(trace)
    assert result.parse_errors == 1
    assert result.uncounted == 1
    assert result.total == 0


def test_bench_non_object_row_does_not_mask_later_rows(tmp_path: Path) -> None:
    """A non-object row must be scored as a parse error and NOT abort scoring
    of the rows that follow it. Guard.
    """
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '[1, 2, 3]\n'
        + json.dumps(
            {
                "trace_id": "ok_truthful",
                "language": "python",
                "before_blob": "def foo():\n    pass\n",
                "after_blob": "def bar():\n    pass\n",
                "claimed_actions": [
                    {"kind": "rename", "symbol": "foo", "new_symbol": "bar"}
                ],
                "was_lie": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = run_bench(trace)
    assert result.parse_errors == 1  # the non-object row
    assert result.true_negatives == 1  # the truthful rename honored its claim
    assert result.total == 1
