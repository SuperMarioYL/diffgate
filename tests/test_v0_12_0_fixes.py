"""Adversarial regression tests for the v0.12.0 correctness fixes.

Four defects pinned end-to-end against the shipped v0.11.0 source:

* ``fix-signature-only-edits-invisible`` (Rust traits) — ``LANGUAGE_RULES
  ["rust"]`` mapped ``function_item`` / ``struct_item`` / ``enum_item`` /
  ``trait_item`` but omitted ``function_signature_item``, the node type that
  holds a Rust trait method DECLARATION (a signature with no body — Rust's
  interface surface). ``trait Repository { fn find(&self, id: u64) -> ...; }``
  yielded only the ``Repository`` trait symbol and zero method symbols, so a
  truthful ``add find scope=Repository`` / ``signature_change find
  scope=Repository`` false-failed. The same declaration/signature-split gap
  the v0.11.0 Go-interface fix believed it had closed for the last language.
* ``fix-signature-only-edits-invisible`` (return types) — ``_signature_text``
  read only the ``parameters`` field, so a return-type-only edit (``def
  fetch() -> Response:`` → ``-> dict:``) produced an identical signature text
  and an unchanged body_hash: the structural diff was a no-op and a truthful
  ``signature_change`` claim false-failed — across every typed language
  DiffGate supports. The return type is now appended to the signature text
  (per-language field: ``return_type`` / ``result`` / ``type``; ``type`` only
  for java/cpp because go's ``type_spec`` exposes an unrelated ``type``).
* ``fix-bench-and-diff-output-contracts`` (bench crash) — ``iter_traces``
  raised ``ValueError`` for a line that is not valid JSON and ``run_bench``
  iterated it outside any per-row try, so ONE truncated/hand-edited trace line
  aborted the whole replay with a traceback (through ``bench_cmd``, at exit
  code 1) — the same bad-row class the v0.11.0 non-object-row fix covered,
  left incomplete for malformed-JSON rows. Parsing now lives inside the
  per-row error handling and a bad row is scored as a parse error.
* ``fix-bench-and-diff-output-contracts`` (diff ordering) — ``_compute_diff``
  iterated ``set(before_by_key) & set(after_by_key)``, whose order varies with
  ``PYTHONHASHSEED`` across processes, so the order of ``signature_changed`` /
  ``body_changed`` entries in ``structural_diff`` differed between runs on
  identical inputs — violating the verifier's own "same blobs, same claim,
  same verdict" determinism contract. Shared keys are now iterated in
  ``after_by_key`` insertion order (source order).

Each red->green test fails on the pre-fix source and passes after the fix;
the guards (lying no-op claim still fails; go type_spec signatures untouched;
body-only edits still body_changed; strict ``iter_traces`` contract retained)
ensure the fixes do not over-correct.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from diffgate.bench import iter_traces, run_bench
from diffgate.parsers import parse_symbols
from diffgate.verifier import ClaimedAction, EditClaim, verify

# --------------------------------------------------------------------------- #
# fix-signature-only-edits-invisible — Rust trait method declarations
# (src/diffgate/parsers.py, LANGUAGE_RULES["rust"])
# --------------------------------------------------------------------------- #
TRAIT = (
    "trait Repository {\n"
    "    fn find(&self, id: u64) -> Option<User>;\n"
    "}\n"
)


def test_rust_trait_method_declaration_is_parsed() -> None:
    """A Rust trait method declaration must emit a method symbol keyed by the
    trait scope. Red->green: pre-fix only the trait symbol was emitted."""
    syms = parse_symbols(TRAIT, "rust")
    find = [s for s in syms if s.name == "find"]
    assert len(find) == 1
    assert find[0].kind == "method"
    assert find[0].scope == "Repository"
    assert find[0].body_hash == ""  # a declaration has no body
    assert find[0].signature == "(&self, id: u64) -> Option<User>"


def test_rust_trait_truthful_scoped_add_passes() -> None:
    """A truthful scoped `add` on a trait method declaration must PASS.
    Red->green: pre-fix this returned passed=False ('not found among newly
    added symbols')."""
    claim = EditClaim(
        before_blob="trait Repository {\n}\n",
        after_blob=TRAIT,
        language="rust",
        claimed_actions=[ClaimedAction(kind="add", symbol="find", scope="Repository")],
    )
    verdict = verify(claim)
    assert verdict.passed, [m.reason for m in verdict.mismatches]


def test_rust_trait_truthful_scoped_signature_change_passes() -> None:
    """A truthful scoped `signature_change` on a trait method declaration must
    PASS. Red->green: pre-fix this returned passed=False ('signature of find
    is unchanged')."""
    changed = (
        "trait Repository {\n"
        "    fn find(&self, id: u64, extra: bool) -> Option<User>;\n"
        "}\n"
    )
    claim = EditClaim(
        before_blob=TRAIT,
        after_blob=changed,
        language="rust",
        claimed_actions=[
            ClaimedAction(kind="signature_change", symbol="find", scope="Repository")
        ],
    )
    verdict = verify(claim)
    assert verdict.passed, [m.reason for m in verdict.mismatches]


def test_rust_trait_lying_noop_signature_change_still_fails() -> None:
    """Guard: a lying no-op signature_change on a trait method must still
    FAIL — the fix must not start passing claims the AST refutes."""
    claim = EditClaim(
        before_blob=TRAIT,
        after_blob=TRAIT,
        language="rust",
        claimed_actions=[
            ClaimedAction(kind="signature_change", symbol="find", scope="Repository")
        ],
    )
    verdict = verify(claim)
    assert not verdict.passed


# --------------------------------------------------------------------------- #
# fix-signature-only-edits-invisible — return types in signature text
# (src/diffgate/parsers.py, _signature_text / _RETURN_TYPE_FIELD)
# --------------------------------------------------------------------------- #
def test_python_return_type_only_edit_is_signature_changed() -> None:
    """A return-type-only edit must surface as signature_changed, not vanish
    into a no-op diff. Red->green: pre-fix the diff was empty."""
    before = "def fetch() -> Response:\n    return load()\n"
    after = "def fetch() -> dict:\n    return load()\n"
    syms = parse_symbols(before, "python")
    assert syms[0].signature == "() -> Response"
    diff_changed = verify(
        EditClaim(
            before_blob=before,
            after_blob=after,
            language="python",
            claimed_actions=[
                ClaimedAction(kind="signature_change", symbol="fetch")
            ],
        )
    )
    assert diff_changed.passed, [m.reason for m in diff_changed.mismatches]


def test_truthful_signature_change_passes_across_typed_languages() -> None:
    """The same return-type-only edit in every typed language must make a
    truthful `signature_change` claim PASS. Red->green: pre-fix the signature
    text excluded the return type in all of them."""
    cases = [
        ("python", "def f() -> int:\n    pass\n", "def f() -> str:\n    pass\n"),
        ("typescript", "function f(): number { return 1; }", "function f(): string { return ''; }"),
        ("go", "func f() int { return 1 }", "func f() string { return \"\" }"),
        ("rust", "fn f() -> i32 { 1 }", "fn f() -> String { todo!() }"),
        ("java", "int f() { return 1; }", "String f() { return \"\"; }"),
        ("cpp", "int f() { return 1; }", "double f() { return 1.0; }"),
    ]
    for lang, before, after in cases:
        claim = EditClaim(
            before_blob=before,
            after_blob=after,
            language=lang,
            claimed_actions=[ClaimedAction(kind="signature_change", symbol="f")],
        )
        verdict = verify(claim)
        assert verdict.passed, f"{lang}: {[m.reason for m in verdict.mismatches]}"


def test_lying_signature_change_on_unchanged_blob_still_fails() -> None:
    """Guard: a signature_change claim on an unchanged blob must still FAIL —
    including via the no-op safety net."""
    blob = "def fetch() -> Response:\n    return load()\n"
    claim = EditClaim(
        before_blob=blob,
        after_blob=blob,
        language="python",
        claimed_actions=[ClaimedAction(kind="signature_change", symbol="fetch")],
    )
    verdict = verify(claim)
    assert not verdict.passed


def test_body_only_edit_is_still_body_changed() -> None:
    """Guard: a body-only edit must still be reported as body_changed, not
    signature_changed — including the return type in the signature must not
    reclassify pure body edits."""
    before = "def f() -> int:\n    return 1\n"
    after = "def f() -> int:\n    return 2\n"
    syms = parse_symbols(before, "python")
    assert syms[0].signature == "() -> int"
    claim = EditClaim(
        before_blob=before,
        after_blob=after,
        language="python",
        claimed_actions=[ClaimedAction(kind="signature_change", symbol="f")],
    )
    verdict = verify(claim)
    assert not verdict.passed  # the signature did NOT change — the claim lies
    assert len(verdict.structural_diff.body_changed) == 1
    assert len(verdict.structural_diff.signature_changed) == 0


def test_go_result_field_including_multi_return_is_in_signature() -> None:
    """Go's `result` field (single type or multi-return parameter list) feeds
    the signature; a go type_spec's unrelated `type` field must NOT."""
    syms = parse_symbols("func f(a int) (int, error) { return 1, nil }", "go")
    assert syms[0].signature == "(a int) -> (int, error)"
    alias = parse_symbols("type S struct { a int }", "go")
    assert alias[0].name == "S"
    assert alias[0].signature == ""


def test_unannotated_symbols_keep_bare_parameter_signatures() -> None:
    """Guard: symbols without a return type keep the pre-0.12 signature text —
    existing contracts (and the committed bench trace) depend on it."""
    assert parse_symbols("def f(x):\n    pass\n", "python")[0].signature == "(x)"
    assert parse_symbols("def g():\n    pass\n", "python")[0].signature == "()"
    iface = parse_symbols("type I interface { M(x int) }", "go")
    m = [s for s in iface if s.name == "M"]
    assert m[0].signature == "(x int)"


# --------------------------------------------------------------------------- #
# fix-bench-and-diff-output-contracts — invalid-JSON trace rows
# (src/diffgate/bench.py, run_bench / _iter_trace_lines)
# --------------------------------------------------------------------------- #
def test_bench_invalid_json_row_does_not_crash(tmp_path: Path) -> None:
    """A trace line that is not valid JSON (truncated/hand-edited row) must be
    scored as a parse error, not abort the bench. Red->green: pre-fix
    iter_traces raised ValueError out of the loop and no report was produced."""
    trace = tmp_path / "trace.jsonl"
    valid_truthful = {
        "trace_id": "ok_truthful",
        "language": "python",
        "before_blob": "def foo():\n    pass\n",
        "after_blob": "def bar():\n    pass\n",
        "claimed_actions": [
            {"kind": "rename", "symbol": "foo", "new_symbol": "bar"}
        ],
        "was_lie": False,
    }
    valid_lie = {
        "trace_id": "missed_lie",
        "language": "python",
        "before_blob": "def foo():\n    pass\n",
        "after_blob": "def foo():\n    pass\n",
        "claimed_actions": [
            {"kind": "rename", "symbol": "foo", "new_symbol": "bar"}
        ],
        "was_lie": True,
    }
    trace.write_text(
        json.dumps(valid_truthful) + "\n"
        + '{"trace_id": "truncated_row", "language": "pytho\n'
        + json.dumps(valid_lie) + "\n",
        encoding="utf-8",
    )
    result = run_bench(trace)
    assert result.parse_errors == 1
    assert result.uncounted == 1
    assert result.true_negatives == 1  # the truthful rename before the bad row
    assert result.true_positives == 1  # the lying rename after it is still caught
    assert result.total == 2
    assert "line-2" in result.uncounted_examples  # cited by line number


def test_iter_traces_keeps_strict_invalid_json_contract(tmp_path: Path) -> None:
    """Guard: the direct-caller contract of iter_traces (raise ValueError with
    path:line for invalid JSON) is unchanged — tolerance is run_bench's job."""
    import pytest

    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"trace_id": "x", "language": "pytho\n', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        list(iter_traces(trace))


# --------------------------------------------------------------------------- #
# fix-bench-and-diff-output-contracts — deterministic structural_diff order
# (src/diffgate/verifier.py, _compute_diff)
# --------------------------------------------------------------------------- #
_DETERMINISM_PROBE = r"""
import json
from diffgate.verifier import compute_diff
before = "".join(f"def f{i}(x: int) -> None:\n    pass\n" for i in range(4))
after = "".join(f"def f{i}(x: str) -> None:\n    pass\n" for i in range(4))
print(json.dumps(compute_diff(before, after, "python").to_dict()))
"""


def test_structural_diff_order_is_deterministic_across_processes() -> None:
    """The full structural_diff JSON must be byte-identical across processes
    with different PYTHONHASHSEED values. Red->green: pre-fix the set-intersection
    loop yielded a different signature_changed order per hash seed."""
    outs = set()
    for seed in ("0", "1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        proc = subprocess.run(
            [sys.executable, "-c", _DETERMINISM_PROBE],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        outs.add(proc.stdout)
    assert len(outs) == 1, f"{len(outs)} distinct orderings across hash seeds"
    # And the probe must not pass vacuously: 4 signature changes are present.
    payload = json.loads(next(iter(outs)))
    assert len(payload["signature_changed"]) == 4
