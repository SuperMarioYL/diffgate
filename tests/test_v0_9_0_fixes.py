"""Adversarial regression tests for the v0.9.0 correctness fixes.

Three defects pinned end-to-end against the shipped v0.8.0 source:

* ``fix-cpp-ts-namespace-scope-dropped`` — ``_walk`` propagated scope for
  class/method/Rust ``impl``/Go receiver/C++ out-of-line qualifier but had no
  branch for C++ ``namespace_definition`` (or TS/TSX ``internal_module`` /
  ``module``), so a free function ``void bar()`` inside ``namespace Foo { ... }``
  was emitted with ``scope=''`` while the same logical symbol written
  out-of-line as ``void Foo::bar() {}`` correctly got ``scope='Foo'``. A
  contract-following agent emitting a scoped claim on the namespace form
  therefore false-failed a *truthful* edit. The walk now reads the block name
  and propagates it as ``next_scope``.
* ``fix-cpp-method-declaration-invisible`` — ``LANGUAGE_RULES["cpp"]`` mapped
  only ``function_definition`` (a definition *with* a body), so a C++ method
  *declaration* like ``struct Foo { void bar(); };`` (no body — the canonical
  ``.h`` header form) yielded *zero* symbols for ``bar``. The most common C++
  header edit (``void bar();`` -> ``void bar(int x);``) was therefore
  over-flagged. ``_walk`` now handles ``declaration`` / ``field_declaration``
  nodes whose declarator chain holds a ``function_declarator``, emitting a
  symbol with an empty ``body_hash`` mirroring the ``function_definition`` path.
* ``fix-version-string-drift-07-vs-08`` — the in-package version string was
  stuck at ``0.7.0`` in both ``src/diffgate/__init__.py`` and
  ``pyproject.toml`` while the GitHub release lineage had already advanced to
  ``v0.8.0``; both are bumped straight to ``0.9.0``.

Each red->green test fails on the pre-fix source and passes after the fix; the
guards (anonymous namespace, data-field exclusion, wrong-scope claim) ensure
the fixes do not over-correct into a silent-success lie.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from diffgate.parsers import parse_symbols
from diffgate.verifier import EditClaim, verify

_REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# fix-cpp-ts-namespace-scope-dropped (src/diffgate/parsers.py)
# --------------------------------------------------------------------------- #
def test_cpp_namespace_function_carries_namespace_scope() -> None:
    """`void bar()` inside `namespace Foo { ... }` must key by scope='Foo'
    (and promote to method, matching the out-of-line `void Foo::bar()` form).
    Red->green: pre-fix `bar` was emitted with scope=''.
    """
    syms = parse_symbols("namespace Foo { void bar() {} }\n", "cpp")
    bar = next(s for s in syms if s.name == "bar")
    assert bar.scope == "Foo", f"expected scope 'Foo', got {bar.scope!r}"
    assert bar.kind == "method"


def test_cpp_namespace_function_keys_like_out_of_line_definition() -> None:
    """The namespace-block form and the out-of-line `Foo::bar` form must
    produce the same (scope, kind) so a definition moved in/out of a namespace
    isn't surfaced as a spurious delete+add. Red->green: pre-fix the namespace
    form had scope='' so the keys diverged.
    """
    ns_syms = parse_symbols("namespace Foo { void bar() {} }\n", "cpp")
    ool_syms = parse_symbols("void Foo::bar() {}\n", "cpp")
    ns = next(s for s in ns_syms if s.name == "bar")
    ool = next(s for s in ool_syms if s.name == "bar")
    assert (ns.scope, ns.kind) == (ool.scope, ool.kind) == ("Foo", "method")


def test_cpp_namespace_truthful_scoped_signature_change_passes() -> None:
    """A real signature change on a namespace-scoped function with a scoped
    claim must PASS. Red->green: pre-fix `bar` had scope='' so the scoped claim
    false-failed ("a same-named symbol changed elsewhere").
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "namespace Foo { void bar() {} }\n",
            "after_blob": "namespace Foo { void bar(int x) {} }\n",
            "language": "cpp",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "Foo"}
            ],
        }
    )
    assert verify(claim).passed is True, [m.to_dict() for m in verify(claim).mismatches]


def test_cpp_namespace_truthful_scoped_add_passes() -> None:
    """Adding a function inside `namespace Foo` and claiming `add bar scope=Foo`
    must PASS. Red->green: pre-fix the added `bar` had scope=''.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "namespace Foo {}\n",
            "after_blob": "namespace Foo { void bar() {} }\n",
            "language": "cpp",
            "claimed_actions": [{"kind": "add", "symbol": "bar", "scope": "Foo"}],
        }
    )
    assert verify(claim).passed is True, [m.to_dict() for m in verify(claim).mismatches]


def test_cpp_anonymous_namespace_leaves_scope_unchanged() -> None:
    """An anonymous `namespace { ... }` (no name) must NOT propagate a scope —
    `bar` keeps module scope ''. Guard: the fix must not over-correct an
    anonymous namespace into a spurious scope.
    """
    syms = parse_symbols("namespace { void bar() {} }\n", "cpp")
    bar = next(s for s in syms if s.name == "bar")
    assert bar.scope == "", f"anonymous namespace must not set scope, got {bar.scope!r}"


def test_ts_namespace_function_carries_namespace_scope() -> None:
    """`function bar()` inside TS `namespace Foo { ... }` must key by scope='Foo'.
    Red->green: pre-fix `bar` was emitted with scope=''.
    """
    syms = parse_symbols("namespace Foo { function bar() {} }\n", "typescript")
    bar = next(s for s in syms if s.name == "bar")
    assert bar.scope == "Foo", f"expected scope 'Foo', got {bar.scope!r}"


def test_ts_module_function_carries_module_scope() -> None:
    """`function bar()` inside TS `module Foo { ... }` must key by scope='Foo'.
    Red->green: pre-fix `bar` was emitted with scope=''.
    """
    syms = parse_symbols("module Foo { function bar() {} }\n", "typescript")
    bar = next(s for s in syms if s.name == "bar")
    assert bar.scope == "Foo", f"expected scope 'Foo', got {bar.scope!r}"


def test_tsx_namespace_function_carries_namespace_scope() -> None:
    """Same namespace-scope fix holds for TSX. Red->green: pre-fix scope=''."""
    syms = parse_symbols("namespace Foo { function bar() {} }\n", "tsx")
    bar = next(s for s in syms if s.name == "bar")
    assert bar.scope == "Foo", f"expected scope 'Foo', got {bar.scope!r}"


def test_ts_namespace_truthful_scoped_add_passes() -> None:
    """Adding a function inside a TS `namespace Foo` with a scoped claim must
    PASS. Red->green: pre-fix the added `bar` had scope=''.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "namespace Foo {}\n",
            "after_blob": "namespace Foo { function bar() {} }\n",
            "language": "typescript",
            "claimed_actions": [{"kind": "add", "symbol": "bar", "scope": "Foo"}],
        }
    )
    assert verify(claim).passed is True, [m.to_dict() for m in verify(claim).mismatches]


def test_cpp_namespace_scoped_claim_on_wrong_scope_fails() -> None:
    """Claiming `signature_change bar scope=Bar` when bar lives in namespace Foo
    must still FAIL — the fix must not over-correct into granting a pass for a
    wrong-scope claim. Guard.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "namespace Foo { void bar() {} }\n",
            "after_blob": "namespace Foo { void bar(int x) {} }\n",
            "language": "cpp",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "Bar"}
            ],
        }
    )
    assert verify(claim).passed is False


# --------------------------------------------------------------------------- #
# fix-cpp-method-declaration-invisible (src/diffgate/parsers.py)
# --------------------------------------------------------------------------- #
def test_cpp_method_declaration_in_struct_yields_symbol() -> None:
    """`struct Foo { void bar(); };` (no body — the canonical .h form) must
    yield a `bar` method symbol in scope `Foo` with an empty body_hash and the
    parameter-list signature. Red->green: pre-fix `bar` was never emitted.
    """
    syms = parse_symbols("struct Foo { void bar(); };\n", "cpp")
    bar = next((s for s in syms if s.name == "bar"), None)
    assert (
        bar is not None
    ), f"method declaration 'bar' yielded no symbol: {[s.name for s in syms]}"
    assert bar.kind == "method"
    assert bar.scope == "Foo"
    assert bar.body_hash == "", "a prototype has no body"
    assert bar.signature == "()"


def test_cpp_method_declaration_truthful_signature_change_passes() -> None:
    """`void bar();` -> `void bar(int x);` inside a struct with a scoped
    `signature_change bar scope=Foo` must PASS. Red->green: pre-fix `bar` was
    invisible so the diff was a no-op and the claim false-failed with the
    misleading 'unchanged between the two blobs' reason.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "struct Foo { void bar(); };\n",
            "after_blob": "struct Foo { void bar(int x); };\n",
            "language": "cpp",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "Foo"}
            ],
        }
    )
    assert verify(claim).passed is True, [m.to_dict() for m in verify(claim).mismatches]


def test_cpp_method_declaration_truthful_add_passes() -> None:
    """Adding a method declaration to a struct with `add bar scope=Foo` must
    PASS. Red->green: pre-fix the added declaration was invisible.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": "struct Foo {};\n",
            "after_blob": "struct Foo { void bar(); };\n",
            "language": "cpp",
            "claimed_actions": [{"kind": "add", "symbol": "bar", "scope": "Foo"}],
        }
    )
    assert verify(claim).passed is True, [m.to_dict() for m in verify(claim).mismatches]


def test_cpp_method_declaration_keys_like_definition() -> None:
    """A method declaration and its definition must key identically
    (scope='Foo', kind='method') so turning a declaration into a definition
    reads as a body change, not a delete+add. Red->green: pre-fix the
    declaration `bar` didn't exist, so this raised.
    """
    decl = next(
        s
        for s in parse_symbols("struct Foo { void bar(); };\n", "cpp")
        if s.name == "bar"
    )
    defn = next(
        s
        for s in parse_symbols("struct Foo { void bar() {} };\n", "cpp")
        if s.name == "bar"
    )
    assert (decl.scope, decl.kind) == (defn.scope, defn.kind) == ("Foo", "method")


def test_cpp_qualified_out_of_line_declaration_carries_scope() -> None:
    """A free-standing out-of-line *declaration* `void Foo::bar();` (header,
    no body) must yield `bar` in scope `Foo` with an empty body_hash, matching
    the out-of-line *definition*. Red->green: pre-fix declarations were
    invisible.
    """
    syms = parse_symbols("void Foo::bar();\n", "cpp")
    bar = next(s for s in syms if s.name == "bar")
    assert bar.scope == "Foo", f"expected scope 'Foo', got {bar.scope!r}"
    assert bar.kind == "method"
    assert bar.body_hash == ""


def test_cpp_data_field_declaration_not_emitted() -> None:
    """`int x;` inside a struct is data, not a callable — the fix's
    `function_declarator` discriminator must NOT emit it. Guard.
    """
    syms = parse_symbols("struct Foo { int x; };\n", "cpp")
    assert not any(s.name == "x" for s in syms), [s.name for s in syms]
    assert any(s.name == "Foo" for s in syms)


def test_cpp_top_level_data_declaration_not_emitted() -> None:
    """A top-level data declaration `int x;` / `int y = 5;` must NOT be emitted
    as a callable. Guard.
    """
    syms = parse_symbols("int x;\nint y = 5;\n", "cpp")
    assert not any(s.name in {"x", "y"} for s in syms), [s.name for s in syms]


# --------------------------------------------------------------------------- #
# fix-version-string-drift-07-vs-08 (src/diffgate/__init__.py + pyproject.toml)
# --------------------------------------------------------------------------- #
def test_package_version_string_is_0_9_0() -> None:
    """The in-package version must read 0.9.0 (was stuck at 0.7.0 while GitHub
    was already at v0.8.0). Red->green: pre-fix __version__ == '0.7.0'.
    """
    import diffgate

    assert diffgate.__version__ == "0.9.0", diffgate.__version__


def test_pyproject_version_is_0_9_0() -> None:
    """The pyproject.toml [project] version must read 0.9.0 so `pip install`
    reports the shipped string. Red->green: pre-fix version == '0.7.0'.
    """
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["version"] == "0.9.0", data["project"]["version"]
