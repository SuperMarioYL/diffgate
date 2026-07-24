"""Adversarial tests for the v0.7.0 correctness fixes.

Two regressions an agent (or a CI hook) could trip on are pinned down here:

* **Fix A — scoped delete/rename misleading no-op reason.** A scoped claim that
  pins the *wrong* scope used to emit a factually-wrong
  "no-op"/"was not present in either blob"/"neither name appears in the
  structural diff (no-op edit)" message — the symbol IS present, just in a
  different scope. The verifier now mirrors the scope-mismatch branch already in
  ``_check_add`` so the verdict still FAILs but the reason text becomes accurate.
* **Fix B — ``diffgate verify --lang python2`` traceback.** An explicit non-
  ``auto`` ``--lang`` used to be returned UNCHECKED, so a typo propagated an
  ``UnsupportedLanguageError`` (a ``ValueError`` subclass) as an unhandled
  traceback at exit 1 — indistinguishable from a failed verification to an agent
  gating on exit code. The CLI now validates ``--lang`` against
  ``SUPPORTED_LANGUAGES`` and emits a clean exit-2 usage error.

Each Fix A test is red→green: pre-fix the reason said "no-op"/"not present";
post-fix it says "not in scope".
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from diffgate.verifier import EditClaim, verify


# --------------------------------------------------------------------------- #
# Fix A — scoped delete pinning the wrong scope must NOT say "no-op"/"not present".
# --------------------------------------------------------------------------- #
def test_scoped_delete_wrong_scope_reports_scope_mismatch_not_noop() -> None:
    """Claiming `delete foo scope=B` when foo was deleted from scope A must
    FAIL with a scope-mismatch reason, not the misleading
    "was not present in either blob (no-op)" message (foo IS present — just in
    a different scope). Red→green: pre-fix the reason said "not present".
    """
    claim = EditClaim.from_dict(
        {
            # foo lives (and is deleted) inside class A.
            "before_blob": "class A:\n    def foo(self):\n        return 1\n",
            "after_blob": "class A:\n    pass\n",
            "language": "python",
            # The claim pins scope B — the wrong scope.
            "claimed_actions": [{"kind": "delete", "symbol": "foo", "scope": "B"}],
        }
    )
    verdict = verify(claim)

    # The verdict still FAILs (unchanged) — the claim is still a lie.
    assert not verdict.passed
    assert verdict.mismatches, "expected at least one mismatch for the wrong-scope delete"

    reasons = " ".join(m.reason for m in verdict.mismatches).lower()
    # The misleading no-op / not-present wording must be gone.
    assert "no-op" not in reasons, f"reason still says no-op: {reasons!r}"
    assert "not present" not in reasons, f"reason still says not present: {reasons!r}"
    # …replaced by an accurate scope-mismatch reason (mirrors _check_add).
    assert "not in scope" in reasons, (
        f"expected a scope-mismatch reason, got: {reasons!r}"
    )


# --------------------------------------------------------------------------- #
# Fix A — scoped rename pinning the wrong scope must NOT say "no-op".
# --------------------------------------------------------------------------- #
def test_scoped_rename_wrong_scope_reports_scope_mismatch_not_noop() -> None:
    """Claiming `rename foo→bar scope=C` when the rename happened in other
    scopes must FAIL with a scope-mismatch reason, not the misleading
    "neither name appears in the structural diff (no-op edit)" message (both
    names ARE present — just in different scopes). Red→green.
    """
    claim = EditClaim.from_dict(
        {
            # foo is deleted from A; bar is added to B — a real rename, but in
            # scopes A/B, not the claimed scope C.
            "before_blob": (
                "class A:\n    def foo(self):\n        return 1\n"
                "class B:\n    pass\n"
            ),
            "after_blob": (
                "class A:\n    pass\n"
                "class B:\n    def bar(self):\n        return 2\n"
            ),
            "language": "python",
            "claimed_actions": [
                {"kind": "rename", "symbol": "foo", "new_symbol": "bar", "scope": "C"}
            ],
        }
    )
    verdict = verify(claim)

    assert not verdict.passed
    assert verdict.mismatches, "expected at least one mismatch for the wrong-scope rename"

    reasons = " ".join(m.reason for m in verdict.mismatches).lower()
    assert "no-op" not in reasons, f"reason still says no-op: {reasons!r}"
    assert "not present" not in reasons, f"reason still says not present: {reasons!r}"
    assert "not in scope" in reasons, (
        f"expected a scope-mismatch reason, got: {reasons!r}"
    )


# --------------------------------------------------------------------------- #
# Fix A regression guards — the new scope-mismatch branch must not swallow the
# precise "target not found" / truthful-pass reasons.
# --------------------------------------------------------------------------- #
def test_rename_partial_with_scope_keeps_target_not_found_reason() -> None:
    """A scoped rename where the old name WAS deleted in the claimed scope but
    the new name was never added must still say "target not found" — NOT the
    new scope-mismatch reason (which would be a regression: the scope is right,
    only the target is missing). Guards the ``not old_deleted``/``not new_added``
    gating in _check_rename.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "class A:\n    def foo(self):\n        return 1\n",
            "after_blob": "class A:\n    pass\n",
            "language": "python",
            # scope A is correct (foo really left A); bar was never added.
            "claimed_actions": [
                {"kind": "rename", "symbol": "foo", "new_symbol": "bar", "scope": "A"}
            ],
        }
    )
    verdict = verify(claim)

    assert not verdict.passed
    reasons = " ".join(m.reason for m in verdict.mismatches).lower()
    assert "target" in reasons and "not found" in reasons, (
        f"expected 'target not found', got: {reasons!r}"
    )
    assert "not in scope" not in reasons, (
        f"scope-mismatch wrongly fired for a same-scope partial rename: {reasons!r}"
    )


def test_scoped_rename_truthful_passes() -> None:
    """The new scope-mismatch branch must not false-fail a truthful scoped
    rename where both halves land in the claimed scope.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "class A:\n    def foo(self):\n        return 1\n",
            "after_blob": "class A:\n    def bar(self):\n        return 1\n",
            "language": "python",
            "claimed_actions": [
                {"kind": "rename", "symbol": "foo", "new_symbol": "bar", "scope": "A"}
            ],
        }
    )
    verdict = verify(claim)
    assert verdict.passed, (
        f"truthful scoped rename must pass, got mismatches: "
        f"{[m.to_dict() for m in verdict.mismatches]}"
    )


def test_scoped_delete_still_present_keeps_still_present_reason() -> None:
    """A scoped delete where the symbol is still alive in the claimed scope
    (a same-named twin in another scope was deleted) must keep the accurate
    "is still present" reason — NOT the new scope-mismatch reason. Guards the
    ordering of the "still present" check before the scope-mismatch branch.
    """
    claim = EditClaim.from_dict(
        {
            # A.foo still present; module-level foo() removed.
            "before_blob": (
                "class A:\n    def foo(self):\n        return 1\n"
                "\ndef foo():\n    return 2\n"
            ),
            "after_blob": "class A:\n    def foo(self):\n        return 1\n",
            "language": "python",
            "claimed_actions": [{"kind": "delete", "symbol": "foo", "scope": "A"}],
        }
    )
    verdict = verify(claim)
    assert not verdict.passed
    reasons = " ".join(m.reason for m in verdict.mismatches).lower()
    assert "still present" in reasons, f"expected 'still present', got: {reasons!r}"
    assert "not in scope" not in reasons, (
        f"scope-mismatch wrongly fired when the claimed-scope symbol is still alive: "
        f"{reasons!r}"
    )


# --------------------------------------------------------------------------- #
# Fix B — `diffgate verify --lang python2` exits 2, not a traceback at exit 1.
# --------------------------------------------------------------------------- #
def test_verify_invalid_lang_exits_2(tmp_path: Path) -> None:
    """An explicit non-`auto` ``--lang`` that isn't a supported language must
    surface as a clean exit-2 usage error, NOT propagate an
    ``UnsupportedLanguageError`` traceback at exit 1 (which an agent gating on
    exit code would read as a failed verification). Red→green.
    """
    from typer.testing import CliRunner

    from diffgate.cli import app

    before = tmp_path / "a.py"
    before.write_text("def foo():\n    return 1\n", encoding="utf-8")
    after = tmp_path / "b.py"
    after.write_text("def bar():\n    return 1\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--before",
            str(before),
            "--after",
            str(after),
            "--lang",
            "python2",
            "--claim",
            "rename foo→bar",
        ],
    )
    # Exit 2 (usage error) — not 1, which an agent would read as a failed gate.
    assert result.exit_code == 2, (
        f"expected exit 2 for bad --lang, got {result.exit_code}; "
        f"exception={result.exception!r}; output={result.output!r}"
    )
    # No unhandled ValueError/UnsupportedLanguageError leaked — the only
    # acceptable "exception" CliRunner may surface is a clean SystemExit (the
    # exit-2 typer.BadParameter path), NOT a traceback-causing ValueError.
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"expected no unhandled ValueError traceback, got {result.exception!r}"
    )
    # A clean usage message names the bad value. (The message is printed to
    # stderr; CliRunner mixes stderr into output on some click versions and
    # exposes it separately on others, so check both.)
    out = result.output.lower()
    with contextlib.suppress(AssertionError):
        # stderr is not accessible when CliRunner mixes it into output.
        out += (result.stderr or "").lower()
    assert "python2" in out or "not supported" in out, (
        f"expected a clean usage message naming the bad lang, got: {result.output!r}"
    )


def test_verify_valid_lang_still_works(tmp_path: Path) -> None:
    """Regression guard: a valid explicit ``--lang`` must still run the gate
    (and fail) — the new validation didn't break the happy path.
    """
    from typer.testing import CliRunner

    from diffgate.cli import app

    before = tmp_path / "a.py"
    before.write_text("def foo():\n    return 1\n", encoding="utf-8")
    after = tmp_path / "b.py"
    # Genuine no-op rename claim → the gate must FAIL at exit 1 (not exit 2).
    after.write_text("def foo():\n    return 1\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--before",
            str(before),
            "--after",
            str(after),
            "--lang",
            "python",
            "--claim",
            "rename foo→bar",
        ],
    )
    assert result.exit_code == 1, (
        f"valid --lang must run the gate (exit 1 on mismatch), got "
        f"{result.exit_code}; output={result.output!r}"
    )


def test_verify_invalid_lang_claim_file_exits_2(tmp_path: Path) -> None:
    """Fix B also covers the multi-file ``--claim-file`` path: a bad per-entry
    language must surface as a clean exit-2 usage error, not a traceback.
    """
    from typer.testing import CliRunner

    from diffgate.cli import app

    src = tmp_path / "a.py"
    src.write_text("def foo():\n    return 1\n", encoding="utf-8")
    dst = tmp_path / "b.py"
    dst.write_text("def bar():\n    return 1\n", encoding="utf-8")

    claim_file = tmp_path / "claims.json"
    claim_file.write_text(
        json.dumps(
            {
                "base_dir": str(tmp_path),
                "files": [
                    {
                        "before": "a.py",
                        "after": "b.py",
                        "language": "python2",
                        "claimed_actions": [
                            {"kind": "rename", "symbol": "foo", "new_symbol": "bar"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app, ["verify", "--claim-file", str(claim_file)]
    )
    assert result.exit_code == 2, (
        f"expected exit 2 for bad per-entry language in claim file, got "
        f"{result.exit_code}; exception={result.exception!r}; "
        f"output={result.output!r}"
    )
    # No unhandled ValueError/UnsupportedLanguageError traceback — only a clean
    # SystemExit (the exit-2 path) is acceptable.
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"expected no unhandled ValueError traceback, got {result.exception!r}"
    )
