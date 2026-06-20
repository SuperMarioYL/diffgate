"""Replay bench — m3 milestone.

Reads a JSONL trace of agent edits with ground-truth ``was_lie`` labels and
prints precision / recall / catch-rate for DiffGate's verifier against that
trace. The numbers are exactly what the README badge needs.

Trace format (one JSON object per line)::

    {
      "trace_id": "claude_code_session_42_step_7",
      "language": "python",
      "before_blob": "...",
      "after_blob": "...",
      "claimed_actions": [
        {"kind": "rename", "symbol": "foo", "new_symbol": "bar"}
      ],
      "was_lie": true
    }

``was_lie=true`` means the edit was a real silent-success lie (DiffGate
should mark it failed). ``was_lie=false`` means the edit honored its
claims (DiffGate should pass it). The bench treats DiffGate's verdict as
the *predictor* and ``was_lie`` as the *label*; "catch" means DiffGate
predicted a lie, "miss" means DiffGate let a real lie through.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .verifier import EditClaim, verify


@dataclass
class BenchResult:
    """Aggregated bench numbers for one trace run."""

    total: int = 0
    true_positives: int = 0   # was_lie=True, verifier said failed
    false_positives: int = 0  # was_lie=False, verifier said failed (truth flagged)
    true_negatives: int = 0   # was_lie=False, verifier said passed
    false_negatives: int = 0  # was_lie=True, verifier said passed (lie missed)
    parse_errors: int = 0
    # Rows that raised while parsing/verifying. They are NOT silently dropped:
    # a was_lie=true error row is a missed lie (false negative), so it stays in
    # the recall denominator; a was_lie=false error row can't be scored as a
    # truthful pass, so it is tallied here as uncounted and surfaced explicitly
    # rather than inflating the headline catch-rate.
    uncounted: int = 0
    uncounted_examples: list[str] = None  # type: ignore[assignment]
    missed_examples: list[str] = None  # type: ignore[assignment]
    false_alarm_examples: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.uncounted_examples is None:
            self.uncounted_examples = []
        if self.missed_examples is None:
            self.missed_examples = []
        if self.false_alarm_examples is None:
            self.false_alarm_examples = []

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else 0.0

    @property
    def catch_rate(self) -> float:
        # Convenience alias used in the README badge: "% of real lies we caught".
        return self.recall

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "true_negatives": self.true_negatives,
            "false_negatives": self.false_negatives,
            "parse_errors": self.parse_errors,
            "uncounted": self.uncounted,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "catch_rate": round(self.catch_rate, 4),
            "f1": round(self.f1, 4),
            "uncounted_examples": list(self.uncounted_examples[:5]),
            "missed_examples": list(self.missed_examples[:5]),
            "false_alarm_examples": list(self.false_alarm_examples[:5]),
        }


def iter_traces(path: Path) -> Iterable[dict[str, object]]:
    """Yield one parsed JSON record per line, skipping blanks and ``# comments``."""
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{lineno}: invalid JSON ({exc.msg})"
                ) from exc


def run_bench(traces_path: Path) -> BenchResult:
    """Replay ``traces_path`` and return aggregated bench numbers."""
    result = BenchResult()
    for record in iter_traces(traces_path):
        trace_id = str(record.get("trace_id", f"trace_{result.total}"))
        was_lie = bool(record.get("was_lie", False))

        try:
            claim = EditClaim.from_dict(record)
            verdict = verify(claim)
        except Exception:  # noqa: BLE001 — bench should be robust to bad rows
            # An erroring row is NOT a free pass. A real lie that crashes the
            # verifier is a lie we failed to catch — count it as a false
            # negative so it stays in the recall denominator and the headline
            # catch-rate tells the truth. A non-lie row that crashes can't be
            # scored as a truthful pass, so it's surfaced as uncounted.
            result.parse_errors += 1
            if was_lie:
                result.total += 1
                result.false_negatives += 1
                if len(result.missed_examples) < 5:
                    result.missed_examples.append(trace_id)
            else:
                result.uncounted += 1
                if len(result.uncounted_examples) < 5:
                    result.uncounted_examples.append(trace_id)
            continue

        result.total += 1
        predicted_lie = not verdict.passed
        if was_lie and predicted_lie:
            result.true_positives += 1
        elif was_lie and not predicted_lie:
            result.false_negatives += 1
            if len(result.missed_examples) < 5:
                result.missed_examples.append(trace_id)
        elif not was_lie and predicted_lie:
            result.false_positives += 1
            if len(result.false_alarm_examples) < 5:
                result.false_alarm_examples.append(trace_id)
        else:
            result.true_negatives += 1

    return result


def render_report(result: BenchResult) -> str:
    """Human-readable summary suitable for a CI log or a README paste."""
    lines = [
        f"DiffGate bench — {result.total} traces scored "
        f"({result.parse_errors} errored, {result.uncounted} uncounted)",
        "",
        f"  Catch-rate (recall) : {result.catch_rate:.1%}",
        f"  Precision           : {result.precision:.1%}",
        f"  F1                  : {result.f1:.3f}",
        "",
        f"  TP={result.true_positives}  FP={result.false_positives}  "
        f"TN={result.true_negatives}  FN={result.false_negatives}",
    ]
    if result.uncounted:
        lines.append("")
        lines.append(
            f"  ⚠ {result.uncounted} non-lie row(s) errored and could not be "
            f"scored — excluded from the headline numbers above."
        )
        for tid in result.uncounted_examples:
            lines.append(f"    - {tid}")
    if result.missed_examples:
        lines.append("")
        lines.append("  Missed (sample):")
        for tid in result.missed_examples:
            lines.append(f"    - {tid}")
    if result.false_alarm_examples:
        lines.append("")
        lines.append("  False alarms (sample):")
        for tid in result.false_alarm_examples:
            lines.append(f"    - {tid}")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    """``python -m diffgate.bench traces.jsonl [--json]`` entry point."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="diffgate.bench",
        description="Replay a JSONL trace and print catch-rate vs ground truth.",
    )
    parser.add_argument(
        "traces",
        type=Path,
        help="Path to JSONL trace file with EditClaims + was_lie labels.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the human report.",
    )
    args = parser.parse_args(argv)

    if not args.traces.exists():
        print(f"error: trace file not found: {args.traces}", file=sys.stderr)
        return 2

    result = run_bench(args.traces)
    if args.json:
        json.dump(result.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(render_report(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
