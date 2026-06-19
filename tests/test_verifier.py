"""End-to-end tests for the structural verification gate.

The bulk of these tests are driven by ``tests/fixtures/silent_lie_cases.json``
— a hand-curated set of agent silent-lie scenarios across Python, TypeScript,
Go, and Rust. Each fixture pins down either a real lie that DiffGate must
catch (``expected_passed=false``) or a truthful edit DiffGate must not flag
(``expected_passed=true``). Together they form the contract that the m1
verifier ships against.

A handful of focused unit tests cover the bench-mode glue from m3 so that
trace replay stays honest under refactors.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diffgate.bench import iter_traces, render_report, run_bench
from diffgate.verifier import EditClaim, verify

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "silent_lie_cases.json"


def _load_fixture_cases() -> list[dict[str, object]]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    cases = data["cases"]
    assert isinstance(cases, list) and cases, "fixture file is empty"
    return cases


FIXTURE_CASES = _load_fixture_cases()


@pytest.mark.parametrize(
    "case",
    FIXTURE_CASES,
    ids=[c["case_id"] for c in FIXTURE_CASES],
)
def test_silent_lie_fixture(case: dict[str, object]) -> None:
    """Every fixture case must produce the expected verdict."""
    claim = EditClaim.from_dict(
        {
            "before_blob": case["before"],
            "after_blob": case["after"],
            "language": case["language"],
            "claimed_actions": case["claims"],
        }
    )
    verdict = verify(claim)

    assert verdict.passed is case["expected_passed"], (
        f"case {case['case_id']}: expected passed={case['expected_passed']} "
        f"but got {verdict.passed}. Mismatches: "
        f"{[m.to_dict() for m in verdict.mismatches]}"
    )

    expected_sub = case.get("expected_mismatch_substring")
    if expected_sub:
        joined = " ".join(m.reason for m in verdict.mismatches).lower()
        assert expected_sub.lower() in joined, (
            f"case {case['case_id']}: expected mismatch reason to contain "
            f"{expected_sub!r}, got: {[m.reason for m in verdict.mismatches]}"
        )


def test_fixture_has_minimum_lie_coverage() -> None:
    """Plan §m1 says ≥20 hand-crafted lies; pin that down here."""
    lies = [c for c in FIXTURE_CASES if not c["expected_passed"]]
    assert len(lies) >= 20, (
        f"silent_lie_cases.json must keep ≥20 lie cases; found {len(lies)}"
    )


def test_fixture_has_truthful_cases() -> None:
    """Truth cases prevent the verifier from drifting toward always-fail."""
    truths = [c for c in FIXTURE_CASES if c["expected_passed"]]
    assert len(truths) >= 5, (
        f"silent_lie_cases.json must keep some truthful cases to detect "
        f"over-flagging; found {len(truths)}"
    )


def test_noop_edit_with_claims_is_caught() -> None:
    """An empty diff with any claim is the canonical silent-success lie."""
    claim = EditClaim.from_dict(
        {
            "before_blob": "def foo():\n    return 1\n",
            "after_blob": "def foo():\n    return 1\n",
            "language": "python",
            "claimed_actions": [{"kind": "add", "symbol": "validate"}],
        }
    )
    verdict = verify(claim)
    assert not verdict.passed
    assert verdict.mismatches


def test_empty_claim_list_is_passing() -> None:
    """No claim, no contract — the verifier should not invent a failure."""
    claim = EditClaim.from_dict(
        {
            "before_blob": "def foo():\n    return 1\n",
            "after_blob": "def foo():\n    return 2\n",
            "language": "python",
            "claimed_actions": [],
        }
    )
    verdict = verify(claim)
    assert verdict.passed
    assert verdict.mismatches == []


def test_bench_replay_catches_lies(tmp_path: Path) -> None:
    """The bench harness must report meaningful catch-rate on the fixture set."""
    trace_path = tmp_path / "traces.jsonl"
    with trace_path.open("w", encoding="utf-8") as fh:
        for case in FIXTURE_CASES:
            record = {
                "trace_id": case["case_id"],
                "before_blob": case["before"],
                "after_blob": case["after"],
                "language": case["language"],
                "claimed_actions": case["claims"],
                "was_lie": not case["expected_passed"],
            }
            fh.write(json.dumps(record) + "\n")

    result = run_bench(trace_path)
    assert result.total == len(FIXTURE_CASES)
    assert result.parse_errors == 0
    # The fixtures define the contract; bench must match the per-case test
    # outcomes exactly (no FP, no FN).
    assert result.false_negatives == 0
    assert result.false_positives == 0
    assert result.catch_rate == pytest.approx(1.0)
    assert result.precision == pytest.approx(1.0)

    report = render_report(result)
    assert "Catch-rate" in report
    assert "Precision" in report


def test_bench_skips_blank_and_comment_lines(tmp_path: Path) -> None:
    trace_path = tmp_path / "mixed.jsonl"
    trace_path.write_text(
        "\n"
        "# this is a comment line\n"
        + json.dumps(
            {
                "trace_id": "real",
                "before_blob": "def foo():\n    return 1\n",
                "after_blob": "def foo():\n    return 1\n",
                "language": "python",
                "claimed_actions": [{"kind": "rename", "symbol": "foo", "new_symbol": "bar"}],
                "was_lie": True,
            }
        )
        + "\n\n",
        encoding="utf-8",
    )
    records = list(iter_traces(trace_path))
    assert len(records) == 1
    assert records[0]["trace_id"] == "real"


def test_bench_records_false_negative(tmp_path: Path) -> None:
    """A trace labelled as a lie but accepted by the verifier should count as FN."""
    trace_path = tmp_path / "fn.jsonl"
    # Real edit (add validate) labelled — wrongly — as a lie.
    record = {
        "trace_id": "labelled_as_lie_but_truth",
        "before_blob": "def main():\n    return 1\n",
        "after_blob": "def main():\n    return 1\n\ndef validate(x):\n    return x > 0\n",
        "language": "python",
        "claimed_actions": [{"kind": "add", "symbol": "validate"}],
        "was_lie": True,
    }
    trace_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    result = run_bench(trace_path)
    assert result.total == 1
    assert result.false_negatives == 1
    assert result.true_positives == 0
    assert "labelled_as_lie_but_truth" in result.missed_examples


def test_scoped_add_in_wrong_scope_is_caught() -> None:
    """Claiming `add A.helper` must fail when a module-level helper was added instead."""
    claim = EditClaim.from_dict(
        {
            "before_blob": "class A:\n    pass\n",
            "after_blob": "class A:\n    pass\n\ndef helper():\n    return 1\n",
            "language": "python",
            "claimed_actions": [{"kind": "add", "symbol": "helper", "scope": "A"}],
        }
    )
    verdict = verify(claim)
    assert not verdict.passed
    assert any("scope 'A'" in m.reason for m in verdict.mismatches)


def test_scoped_add_in_right_scope_passes() -> None:
    """The same claim must pass when the method really lands inside the class."""
    claim = EditClaim.from_dict(
        {
            "before_blob": "class A:\n    pass\n",
            "after_blob": "class A:\n    def helper(self):\n        return 1\n",
            "language": "python",
            "claimed_actions": [{"kind": "add", "symbol": "helper", "scope": "A"}],
        }
    )
    verdict = verify(claim)
    assert verdict.passed
    assert verdict.mismatches == []


def test_scoped_delete_distinguishes_method_from_free_function() -> None:
    """Deleting the module-level twin must not satisfy a scoped method-delete claim."""
    before = "class A:\n    def foo(self):\n        return 1\n\ndef foo():\n    return 2\n"
    after = "class A:\n    def foo(self):\n        return 1\n"
    claim = EditClaim.from_dict(
        {
            "before_blob": before,
            "after_blob": after,
            "language": "python",
            "claimed_actions": [{"kind": "delete", "symbol": "foo", "scope": "A"}],
        }
    )
    verdict = verify(claim)
    assert not verdict.passed
    assert any("still present" in m.reason for m in verdict.mismatches)


def test_unscoped_claim_keeps_name_only_matching() -> None:
    """An unscoped claim is a wildcard: it must still match on name alone."""
    claim = EditClaim.from_dict(
        {
            "before_blob": "class A:\n    pass\n",
            "after_blob": "class A:\n    def helper(self):\n        return 1\n",
            "language": "python",
            "claimed_actions": [{"kind": "add", "symbol": "helper"}],
        }
    )
    verdict = verify(claim)
    assert verdict.passed


def test_cli_exposes_documented_subcommands() -> None:
    """The README + mcp.json advertise `mcp-server` and `bench`; they must exist."""
    from typer.testing import CliRunner

    from diffgate.cli import app

    runner = CliRunner()
    help_out = runner.invoke(app, ["--help"]).output
    assert "mcp-server" in help_out
    assert "bench" in help_out
    # mcp.json wires `["mcp-server", "--stdio"]`; the option must be accepted.
    assert runner.invoke(app, ["mcp-server", "--help"]).exit_code == 0


def test_cli_bench_reports_catch_rate(tmp_path: Path) -> None:
    """`diffgate bench traces.jsonl` must score the trace and print catch-rate."""
    from typer.testing import CliRunner

    from diffgate.cli import app

    trace_path = tmp_path / "traces.jsonl"
    trace_path.write_text(
        json.dumps(
            {
                "trace_id": "lie",
                "before_blob": "def foo():\n    return 1\n",
                "after_blob": "def foo():\n    return 1\n",
                "language": "python",
                "claimed_actions": [
                    {"kind": "rename", "symbol": "foo", "new_symbol": "bar"}
                ],
                "was_lie": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["bench", str(trace_path)])
    assert result.exit_code == 0
    assert "Catch-rate" in result.output


def test_mcp_tool_handler_round_trip() -> None:
    """The MCP tool body must accept a JSON-ish payload and return a verdict dict."""
    from diffgate.mcp_server import verify_edit_payload

    result = verify_edit_payload(
        before_blob="def foo():\n    return 1\n",
        after_blob="def foo():\n    return 1\n",
        language="python",
        claimed_actions=[{"kind": "rename", "symbol": "foo", "new_symbol": "bar"}],
    )
    assert isinstance(result, dict)
    assert result["passed"] is False
    assert result["mismatches"], "MCP tool must surface the mismatch list"
    assert "structural_diff" in result
