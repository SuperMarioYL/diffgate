"""Regression tests for the v0.5.0 correctness fixes.

Each test pins a defect verified end-to-end against the shipped v0.4.0 source:

* ``fix-overload-positional-mispair-fabricates-diff`` — overloaded same-name
  methods were keyed by ``(scope, name, kind)`` only and paired *positionally*,
  so a pure reorder of two ``f`` overloads fabricated ``signature_changed`` /
  ``body_changed`` entries and let a LYING ``signature_change`` claim pass
  (silent-lie FALSE NEGATIVE). The diff now pairs overloads by content.
* ``fix-go-method-receiver-scope-dropped`` — a Go method emitted ``scope=''``,
  so a truthful scoped ``add`` / ``signature_change`` claim returned
  ``passed=False`` (over-flag). The receiver type is now read as the scope.

These tests fail on the v0.4.0 code and pass after the v0.5.0 fix.
"""

from __future__ import annotations

import pytest

from diffgate.parsers import parse_symbols
from diffgate.verifier import EditClaim, verify

# ---------------------------------------------------------------------------
# fix-overload-positional-mispair-fabricates-diff (src/diffgate/verifier.py)
# ---------------------------------------------------------------------------

_CPP_OVERLOADS = "int f(int a) { return a; }\ndouble f(double b) { return b; }\n"
_CPP_OVERLOADS_REORDERED = "double f(double b) { return b; }\nint f(int a) { return a; }\n"

_JAVA_OVERLOADS = "class C {\n  int f(int a){return a;}\n  String f(String s){return s;}\n}\n"
_JAVA_OVERLOADS_REORDERED = (
    "class C {\n  String f(String s){return s;}\n  int f(int a){return a;}\n}\n"
)


def test_cpp_overload_reorder_does_not_fabricate_signature_change() -> None:
    """Reordering two C++ overloads must not show them as signature-changed."""
    claim = EditClaim.from_dict(
        {
            "before_blob": _CPP_OVERLOADS,
            "after_blob": _CPP_OVERLOADS_REORDERED,
            "language": "cpp",
            "claimed_actions": [],
        }
    )
    diff = verify(claim).structural_diff
    sig_names = {b.name for b, _ in diff.signature_changed}
    body_names = {b.name for b, _ in diff.body_changed}
    assert "f" not in sig_names, "overload reorder fabricated a signature change"
    assert "f" not in body_names, "overload reorder fabricated a body change"


def test_cpp_lying_signature_change_on_overload_reorder_fails() -> None:
    """A LYING `signature_change f` over a pure reorder must be CAUGHT."""
    claim = EditClaim.from_dict(
        {
            "before_blob": _CPP_OVERLOADS,
            "after_blob": _CPP_OVERLOADS_REORDERED,
            "language": "cpp",
            "claimed_actions": [{"kind": "signature_change", "symbol": "f"}],
        }
    )
    assert verify(claim).passed is False


def test_java_lying_signature_change_on_overload_reorder_fails() -> None:
    claim = EditClaim.from_dict(
        {
            "before_blob": _JAVA_OVERLOADS,
            "after_blob": _JAVA_OVERLOADS_REORDERED,
            "language": "java",
            "claimed_actions": [{"kind": "signature_change", "symbol": "f", "scope": "C"}],
        }
    )
    assert verify(claim).passed is False


def test_genuine_signature_change_on_overload_still_passes() -> None:
    """A REAL signature edit on one overload must still surface (truthful → PASS)."""
    after = "int f(int a, int z) { return a; }\ndouble f(double b) { return b; }\n"
    claim = EditClaim.from_dict(
        {
            "before_blob": _CPP_OVERLOADS,
            "after_blob": after,
            "language": "cpp",
            "claimed_actions": [{"kind": "signature_change", "symbol": "f"}],
        }
    )
    verdict = verify(claim)
    assert verdict.passed is True
    assert any(b.name == "f" for b, _ in verdict.structural_diff.signature_changed)


def test_deleting_one_overload_under_reorder_is_a_delete() -> None:
    """Deleting one overload (with the other reordered) is a truthful `delete f`."""
    after = "double f(double b){return b;}\n"  # f(int) deleted; f(double) kept
    claim = EditClaim.from_dict(
        {
            "before_blob": _CPP_OVERLOADS,
            "after_blob": after,
            "language": "cpp",
            "claimed_actions": [{"kind": "delete", "symbol": "f"}],
        }
    )
    verdict = verify(claim)
    assert verdict.passed is True
    assert len(verdict.structural_diff.deleted) == 1


def test_single_non_overloaded_signature_change_unaffected() -> None:
    """The 1:1 (non-overloaded) path keeps its original signature-change semantics."""
    claim = EditClaim.from_dict(
        {
            "before_blob": "def foo(a):\n    return a\n",
            "after_blob": "def foo(a, b):\n    return a\n",
            "language": "python",
            "claimed_actions": [{"kind": "signature_change", "symbol": "foo"}],
        }
    )
    verdict = verify(claim)
    assert verdict.passed is True
    assert len(verdict.structural_diff.signature_changed) == 1


# ---------------------------------------------------------------------------
# fix-go-method-receiver-scope-dropped (src/diffgate/parsers.py)
# ---------------------------------------------------------------------------

_GO_TYPE = "package main\ntype Server struct{}\n"


def test_go_pointer_receiver_scope_is_the_type() -> None:
    syms = parse_symbols("package main\nfunc (s *Server) Handle(a int) {}\n", "go")
    handle = next(s for s in syms if s.name == "Handle")
    assert handle.scope == "Server", f"expected scope 'Server', got {handle.scope!r}"
    assert handle.kind == "method"


def test_go_value_receiver_scope_is_the_type() -> None:
    syms = parse_symbols("package main\nfunc (s Server) Handle(a int) {}\n", "go")
    handle = next(s for s in syms if s.name == "Handle")
    assert handle.scope == "Server"


def test_go_generic_receiver_scope_unwraps_to_base_type() -> None:
    syms = parse_symbols("package main\nfunc (s *Stack[T]) Push(a T) {}\n", "go")
    push = next(s for s in syms if s.name == "Push")
    assert push.scope == "Stack"


def test_go_truthful_scoped_method_add_passes() -> None:
    claim = EditClaim.from_dict(
        {
            "before_blob": _GO_TYPE,
            "after_blob": _GO_TYPE + "func (s *Server) Handle(a int) {}\n",
            "language": "go",
            "claimed_actions": [{"kind": "add", "symbol": "Handle", "scope": "Server"}],
        }
    )
    assert verify(claim).passed is True


def test_go_truthful_scoped_method_signature_change_passes() -> None:
    claim = EditClaim.from_dict(
        {
            "before_blob": "package main\nfunc (s *Server) Handle(a int) {}\n",
            "after_blob": "package main\nfunc (s *Server) Handle(a int, b int) {}\n",
            "language": "go",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "Handle", "scope": "Server"}
            ],
        }
    )
    assert verify(claim).passed is True


def test_go_lying_scoped_add_on_free_function_fails() -> None:
    """Claiming a method on `Server` while really adding a free func must FAIL."""
    claim = EditClaim.from_dict(
        {
            "before_blob": "package main\n",
            "after_blob": "package main\nfunc Handle(a int) {}\n",
            "language": "go",
            "claimed_actions": [{"kind": "add", "symbol": "Handle", "scope": "Server"}],
        }
    )
    assert verify(claim).passed is False


def test_go_free_func_and_method_no_longer_collide() -> None:
    """A free `func Handle` and a method `Handle` land in distinct scopes."""
    syms = parse_symbols(
        "package main\nfunc Handle() {}\nfunc (s *Server) Handle() {}\n", "go"
    )
    scopes = sorted(s.scope for s in syms if s.name == "Handle")
    assert scopes == ["", "Server"]


@pytest.mark.parametrize("blob", ["package main\nfunc Free() {}\n"])
def test_go_free_function_scope_unchanged(blob: str) -> None:
    """Plain Go functions (no receiver) keep module scope ('')."""
    syms = parse_symbols(blob, "go")
    free = next(s for s in syms if s.name == "Free")
    assert free.scope == ""
    assert free.kind == "function"
