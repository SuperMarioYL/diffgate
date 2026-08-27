"""Adversarial regression tests for the v0.10.0 correctness fixes.

Three defects pinned end-to-end against the shipped v0.9.0 source:

* ``fix-cpp-ts-nested-namespace-scope-dropped`` — the v0.9.0 namespace-scope
  fix only handled a single-segment namespace name. A nested C++
  ``namespace A::B { void bar() {} }`` keyed ``bar`` by ``scope="B"`` (it
  took the last ``::`` segment) and the nested-block form
  ``namespace A { namespace B {} }`` keyed by ``scope="A.B"`` (it chained
  with ``.``), while the same logical symbol written out-of-line as
  ``void A::B::bar() {}`` keyed by ``scope="A::B"`` — so the three forms of
  the identical symbol diverged and a scoped claim on the nested form
  false-failed a *truthful* edit. The walk now propagates the *full* namespace
  path and chains nested C++ blocks with ``::`` so both block forms key by
  ``A::B`` identically to the out-of-line qualifier. TS/TSX had a second
  shape of the same defect: ``namespace A.B { ... }`` was skipped *entirely*
  (its name is a ``nested_identifier``, not an ``identifier``, so the guard
  failed) → ``scope=''``; the walk now accepts a ``nested_identifier`` and
  propagates the full dotted path so ``namespace A.B`` and nested blocks both
  key by ``A.B``.
* ``fix-rust-mod-scope-dropped`` — ``_walk`` propagated scope for Rust
  ``impl_item``, C++/TS namespaces, and Go receivers, but had NO branch for
  Rust ``mod_item``. A free function ``fn bar()`` inside ``mod foo { ... }``
  was emitted with ``scope=''``, so a contract-following agent emitting
  ``signature_change bar scope=foo`` false-failed a truthful edit. The walk
  now reads the ``mod``'s name and propagates it as ``next_scope`` exactly as
  the ``impl_item`` branch does.
* ``fix-scoped-delete-present-elsewhere-misleading-noop`` — a scoped delete
  where the symbol was NOT deleted and still alive in the after-blob at a
  *different* scope than the claim pinned fell through every scope-mismatch
  branch in ``_check_delete`` to the false "was not present in either blob
  (no-op)" message. The verdict still FAILed correctly, but the reason lied
  about *why*. ``_check_delete`` now mirrors the scope-mismatch branch in
  ``_check_add`` / ``_check_rename``: when the claim names a scope and the
  symbol is still present in the after-blob only at a different scope, the
  reason becomes "'foo' is still present, but not in scope '<claimed>'".

Each red->green test fails on the pre-fix source and passes after the fix;
the guards (wrong-scope claim, still-present-in-claimed-scope precedence)
ensure the fixes do not over-correct into a silent-success lie.
"""

from __future__ import annotations

from diffgate.parsers import parse_symbols
from diffgate.verifier import EditClaim, verify


# --------------------------------------------------------------------------- #
# fix-cpp-ts-nested-namespace-scope-dropped (src/diffgate/parsers.py)
# --------------------------------------------------------------------------- #
def test_cpp_dotted_namespace_keys_like_out_of_line_definition() -> None:
    """`namespace A::B { void bar() {} }` and the out-of-line
    `void A::B::bar() {}` must produce the same (scope, kind). Red->green:
    pre-fix the dotted form keyed by scope='B' while out-of-line keyed by
    'A::B'.
    """
    ns_syms = parse_symbols("namespace A::B { void bar() {} }\n", "cpp")
    ool_syms = parse_symbols("void A::B::bar() {}\n", "cpp")
    ns = next(s for s in ns_syms if s.name == "bar")
    ool = next(s for s in ool_syms if s.name == "bar")
    assert (ns.scope, ns.kind) == (ool.scope, ool.kind) == ("A::B", "method")


def test_cpp_nested_namespace_blocks_key_like_out_of_line_definition() -> None:
    """Nested *blocks* `namespace A { namespace B { void bar() {} } }` must
    also key by scope='A::B', matching the out-of-line form. Red->green:
    pre-fix the block form chained with '.' -> scope='A.B', diverging from the
    out-of-line 'A::B'.
    """
    block_syms = parse_symbols(
        "namespace A { namespace B { void bar() {} } }\n", "cpp"
    )
    ool_syms = parse_symbols("void A::B::bar() {}\n", "cpp")
    block = next(s for s in block_syms if s.name == "bar")
    ool = next(s for s in ool_syms if s.name == "bar")
    assert (block.scope, block.kind) == (ool.scope, ool.kind) == (
        "A::B",
        "method",
    )


def test_cpp_dotted_namespace_truthful_scoped_signature_change_passes() -> None:
    """A real signature change on a `namespace A::B`-scoped function with a
    scoped claim must PASS. Red->green: pre-fix `bar` keyed by scope='B' so
    the `scope=A::B` claim false-failed.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "namespace A::B { void bar() {} }\n",
            "after_blob": "namespace A::B { void bar(int x) {} }\n",
            "language": "cpp",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "A::B"}
            ],
        }
    )
    assert verify(claim).passed is True, [
        m.to_dict() for m in verify(claim).mismatches
    ]


def test_ts_dotted_namespace_keys_like_nested_blocks() -> None:
    """`namespace A.B { function bar() {} }` (dotted) and nested blocks
    `namespace A { namespace B {} }` must both key by scope='A.B'.
    Red->green: pre-fix the dotted form was skipped entirely (scope='').
    """
    dotted = parse_symbols("namespace A.B { function bar() {} }\n", "typescript")
    blocks = parse_symbols(
        "namespace A { namespace B { function bar() {} } }\n", "typescript"
    )
    d = next(s for s in dotted if s.name == "bar")
    b = next(s for s in blocks if s.name == "bar")
    assert (d.scope, b.scope) == ("A.B", "A.B")


def test_tsx_dotted_namespace_carries_scope() -> None:
    """Same dotted-namespace fix holds for TSX. Red->green: pre-fix scope=''."""
    syms = parse_symbols("namespace A.B { function bar() {} }\n", "tsx")
    bar = next(s for s in syms if s.name == "bar")
    assert bar.scope == "A.B", f"expected scope 'A.B', got {bar.scope!r}"


def test_ts_dotted_namespace_truthful_scoped_add_passes() -> None:
    """Adding a function inside `namespace A.B` with `add bar scope=A.B` must
    PASS. Red->green: pre-fix the added `bar` had scope=''.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "namespace A.B {}\n",
            "after_blob": "namespace A.B { function bar() {} }\n",
            "language": "typescript",
            "claimed_actions": [{"kind": "add", "symbol": "bar", "scope": "A.B"}],
        }
    )
    assert verify(claim).passed is True, [
        m.to_dict() for m in verify(claim).mismatches
    ]


def test_cpp_nested_namespace_wrong_scope_claim_fails() -> None:
    """Claiming `signature_change bar scope=Bar` when bar lives in namespace
    A::B must still FAIL — the fix must not over-correct into granting a pass
    for a wrong-scope claim. Guard.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "namespace A::B { void bar() {} }\n",
            "after_blob": "namespace A::B { void bar(int x) {} }\n",
            "language": "cpp",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "Bar"}
            ],
        }
    )
    assert verify(claim).passed is False


# --------------------------------------------------------------------------- #
# fix-rust-mod-scope-dropped (src/diffgate/parsers.py)
# --------------------------------------------------------------------------- #
def test_rust_mod_function_carries_mod_scope() -> None:
    """`fn bar()` inside `mod foo { ... }` must key by scope='foo' (and promote
    to method). Red->green: pre-fix `bar` was emitted with scope=''.
    """
    syms = parse_symbols("mod foo { fn bar() {} }\n", "rust")
    bar = next(s for s in syms if s.name == "bar")
    assert bar.scope == "foo", f"expected scope 'foo', got {bar.scope!r}"
    assert bar.kind == "method"


def test_rust_mod_truthful_scoped_signature_change_passes() -> None:
    """A real signature change on a `mod foo`-scoped function with a scoped
    claim must PASS. Red->green: pre-fix `bar` had scope='' so the scoped
    claim false-failed ("a same-named symbol changed elsewhere").
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "mod foo { fn bar() {} }\n",
            "after_blob": "mod foo { fn bar(x: u8) {} }\n",
            "language": "rust",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "foo"}
            ],
        }
    )
    assert verify(claim).passed is True, [
        m.to_dict() for m in verify(claim).mismatches
    ]


def test_rust_mod_truthful_scoped_add_passes() -> None:
    """Adding a function inside `mod foo` with `add bar scope=foo` must PASS.
    Red->green: pre-fix the added `bar` had scope=''.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "mod foo {}\n",
            "after_blob": "mod foo { fn bar() {} }\n",
            "language": "rust",
            "claimed_actions": [{"kind": "add", "symbol": "bar", "scope": "foo"}],
        }
    )
    assert verify(claim).passed is True, [
        m.to_dict() for m in verify(claim).mismatches
    ]


def test_rust_mod_wrong_scope_claim_fails() -> None:
    """Claiming `signature_change bar scope=Bar` when bar lives in `mod foo`
    must still FAIL. Guard.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "mod foo { fn bar() {} }\n",
            "after_blob": "mod foo { fn bar(x: u8) {} }\n",
            "language": "rust",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "Bar"}
            ],
        }
    )
    assert verify(claim).passed is False


# --------------------------------------------------------------------------- #
# fix-scoped-delete-present-elsewhere-misleading-noop (src/diffgate/verifier.py)
# --------------------------------------------------------------------------- #
def test_scoped_delete_present_elsewhere_gives_scope_mismatch_reason() -> None:
    """A scoped `delete foo scope=MyClass` on an unchanged module-level `foo`
    (present in the after-blob, just not in scope 'MyClass') must FAIL with an
    accurate scope-mismatch reason, NOT the false "not present in either blob"
    no-op message. Red->green: pre-fix the reason lied "was not present in
    either blob (no-op)".
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "def foo():\n    pass\n",
            "after_blob": "def foo():\n    pass\n",
            "language": "python",
            "claimed_actions": [
                {"kind": "delete", "symbol": "foo", "scope": "MyClass"}
            ],
        }
    )
    res = verify(claim)
    assert res.passed is False
    assert res.mismatches, "expected a mismatch reason"
    reason = res.mismatches[0].reason
    assert "not in scope" in reason, f"reason should mention scope mismatch: {reason!r}"
    assert "not present in either blob" not in reason, (
        f"reason should not claim the symbol was absent: {reason!r}"
    )


def test_scoped_delete_present_in_claimed_scope_keeps_precise_reason() -> None:
    """A scoped `delete foo scope=MyClass` where `foo` is still present AT the
    claimed scope must keep its more precise "is still present in the
    after-blob" reason, not the new scope-mismatch one. Guard: the new branch
    is ordered AFTER the "still present in claimed scope" check.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "class MyClass:\n    def foo(self):\n        pass\n",
            "after_blob": "class MyClass:\n    def foo(self):\n        pass\n",
            "language": "python",
            "claimed_actions": [
                {"kind": "delete", "symbol": "foo", "scope": "MyClass"}
            ],
        }
    )
    res = verify(claim)
    assert res.passed is False
    assert res.mismatches, "expected a mismatch reason"
    reason = res.mismatches[0].reason
    assert "is still present in the after-blob" in reason, (
        f"should keep the 'still present in claimed scope' reason: {reason!r}"
    )
