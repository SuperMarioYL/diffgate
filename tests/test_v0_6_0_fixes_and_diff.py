"""Regression tests for the v0.6.0 correctness fixes + the new ``diff`` subcommand.

Each fix pins a defect verified end-to-end against the shipped v0.5.0 source:

* ``fix-rust-impl-method-scope-dropped`` — a Rust method inside an ``impl Foo {}``
  block emitted ``scope=''`` because ``_walk`` never read the impl type, so a
  truthful scoped ``add`` / ``signature_change`` returned ``passed=False``
  (over-flag). The impl type is now read from the ``impl_item`` ``type`` field.
* ``fix-ts-js-class-arrow-property-invisible`` — a TS/JS class arrow-function
  property (``handle = (req) => {}`` inside a class) was never emitted as a Symbol,
  so a truthful scoped ``add handle scope=Handler`` returned ``passed=False``.
  ``public_field_definition`` (ts/tsx) / ``field_definition`` (js) are now mapped.

Plus tests for the new ``compute_diff`` public API and the ``diffgate diff`` CLI
subcommand (m8_expose_structural_diff_command). These tests fail on the v0.5.0
code and pass after the v0.6.0 fix / feature.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from diffgate.cli import app
from diffgate.parsers import parse_symbols
from diffgate.verifier import EditClaim, compute_diff, verify

# ---------------------------------------------------------------------------
# fix-rust-impl-method-scope-dropped (src/diffgate/parsers.py)
# ---------------------------------------------------------------------------
_RUST_IMPL = "struct Foo;\nimpl Foo {\n    fn bar(&self) {}\n    fn baz() {}\n}\n"


def test_rust_impl_method_carries_impl_type_as_scope() -> None:
    """`fn bar` inside `impl Foo {}` must key by scope=Foo and kind=method."""
    syms = parse_symbols(_RUST_IMPL, "rust")
    bar = next(s for s in syms if s.name == "bar")
    assert bar.scope == "Foo", f"expected scope 'Foo', got {bar.scope!r}"
    assert bar.kind == "method"
    baz = next(s for s in syms if s.name == "baz")
    assert baz.scope == "Foo" and baz.kind == "method"


def test_rust_impl_truthful_scoped_add_passes() -> None:
    before = "struct Foo;\nimpl Foo {}\n"
    after = "struct Foo;\nimpl Foo {\n    fn bar(&self) {}\n}\n"
    claim = EditClaim.from_dict(
        {
            "before_blob": before,
            "after_blob": after,
            "language": "rust",
            "claimed_actions": [{"kind": "add", "symbol": "bar", "scope": "Foo"}],
        }
    )
    assert verify(claim).passed is True


def test_rust_impl_truthful_scoped_signature_change_passes() -> None:
    before = "struct Foo;\nimpl Foo {\n    fn bar(&self) {}\n}\n"
    after = "struct Foo;\nimpl Foo {\n    fn bar(&self, b: i32) {}\n}\n"
    claim = EditClaim.from_dict(
        {
            "before_blob": before,
            "after_blob": after,
            "language": "rust",
            "claimed_actions": [
                {"kind": "signature_change", "symbol": "bar", "scope": "Foo"}
            ],
        }
    )
    assert verify(claim).passed is True


def test_rust_impl_lying_scoped_add_on_free_function_fails() -> None:
    """Claiming `add bar scope=Foo` while really adding a free fn must FAIL."""
    before = "struct Foo;\n"
    after = "struct Foo;\nfn bar() {}\n"
    claim = EditClaim.from_dict(
        {
            "before_blob": before,
            "after_blob": after,
            "language": "rust",
            "claimed_actions": [{"kind": "add", "symbol": "bar", "scope": "Foo"}],
        }
    )
    assert verify(claim).passed is False


def test_rust_generic_impl_type_unwraps_to_base() -> None:
    syms = parse_symbols(
        "struct Stack<T>{}\nimpl Stack<T> {\n    fn push(&self, a: T) {}\n}\n", "rust"
    )
    push = next(s for s in syms if s.name == "push")
    assert push.scope == "Stack"


def test_rust_impl_trait_for_unwraps_to_implementing_type() -> None:
    syms = parse_symbols(
        "struct Baz;\nimpl Trait for Baz {\n    fn qux() {}\n}\n", "rust"
    )
    qux = next(s for s in syms if s.name == "qux")
    assert qux.scope == "Baz"


def test_rust_free_function_scope_unchanged() -> None:
    """Plain Rust free functions (no impl) keep module scope ('') and kind=function."""
    syms = parse_symbols("fn free() {}\n", "rust")
    free = next(s for s in syms if s.name == "free")
    assert free.scope == ""
    assert free.kind == "function"


# ---------------------------------------------------------------------------
# fix-ts-js-class-arrow-property-invisible (src/diffgate/parsers.py)
# ---------------------------------------------------------------------------
def test_typescript_class_arrow_property_is_visible_as_method() -> None:
    blob = "class Handler {\n  handle = (req) => { return req; };\n}\n"
    syms = parse_symbols(blob, "typescript")
    handle = next(s for s in syms if s.name == "handle")
    assert handle.kind == "method"
    assert handle.scope == "Handler"


def test_tsx_class_arrow_property_is_visible_as_method() -> None:
    blob = "class C {\n  onClick = () => { return 1; };\n}\n"
    syms = parse_symbols(blob, "tsx")
    assert any(s.name == "onClick" and s.scope == "C" and s.kind == "method" for s in syms)


def test_javascript_class_arrow_property_is_visible_as_method() -> None:
    blob = "class C {\n  handle = function() { return 1; };\n}\n"
    syms = parse_symbols(blob, "javascript")
    handle = next(s for s in syms if s.name == "handle")
    assert handle.kind == "method"
    assert handle.scope == "C"


def test_typescript_data_field_stays_invisible() -> None:
    """`count = 0` is data, not a callable — must not emit a Symbol."""
    blob = "class C {\n  count = 0;\n  handle = () => {};\n}\n"
    syms = parse_symbols(blob, "typescript")
    names = {s.name for s in syms}
    assert "count" not in names
    assert "handle" in names


def test_typescript_scoped_add_on_class_arrow_property_passes() -> None:
    before = "class Handler {}\n"
    after = "class Handler {\n  handle = (req) => { return req; };\n}\n"
    claim = EditClaim.from_dict(
        {
            "before_blob": before,
            "after_blob": after,
            "language": "typescript",
            "claimed_actions": [{"kind": "add", "symbol": "handle", "scope": "Handler"}],
        }
    )
    assert verify(claim).passed is True


def test_typescript_lying_scoped_add_on_free_arrow_fails() -> None:
    """Claiming `add handle scope=Handler` while adding a module-level const must FAIL."""
    before = "class Handler {}\n"
    after = "class Handler {}\nconst handle = () => {};\n"
    claim = EditClaim.from_dict(
        {
            "before_blob": before,
            "after_blob": after,
            "language": "typescript",
            "claimed_actions": [{"kind": "add", "symbol": "handle", "scope": "Handler"}],
        }
    )
    assert verify(claim).passed is False


def test_typescript_module_level_const_arrow_regression() -> None:
    """The v0.3.0 module-level `const handler = () => {}` path is unchanged."""
    syms = parse_symbols("const handler = (req) => { return req; };\n", "typescript")
    handler = next(s for s in syms if s.name == "handler")
    assert handler.kind == "function"
    assert handler.scope == ""


# ---------------------------------------------------------------------------
# m8_expose_structural_diff_command (src/diffgate/verifier.py + cli.py)
# ---------------------------------------------------------------------------
def test_compute_diff_reports_added_deleted_signature_change() -> None:
    diff = compute_diff(
        "def foo(a):\n    return a\n",
        "def bar(a, b):\n    return a\n",
        "python",
    )
    assert {s.name for s in diff.added} == {"bar"}
    assert {s.name for s in diff.deleted} == {"foo"}
    assert not diff.signature_changed  # rename = delete+add, not a sig change
    assert not diff.is_noop()


def test_compute_diff_reports_signature_change_in_place() -> None:
    diff = compute_diff(
        "def foo(a):\n    return a\n",
        "def foo(a, b):\n    return a\n",
        "python",
    )
    assert not diff.added and not diff.deleted
    assert len(diff.signature_changed) == 1
    b, a = diff.signature_changed[0]
    assert b.name == "foo" and a.name == "foo"
    assert b.signature != a.signature
    assert not diff.is_noop()


def test_compute_diff_noop_on_identical_blobs() -> None:
    blob = "def foo(a):\n    return a\n"
    assert compute_diff(blob, blob, "python").is_noop()


def test_cli_diff_subcommand_prints_structural_diff(tmp_path: Path) -> None:
    before = tmp_path / "b.py"
    after = tmp_path / "a.py"
    before.write_text("def foo(a):\n    return a\n", encoding="utf-8")
    after.write_text("def foo(a, b):\n    return a\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["diff", "--before", str(before), "--after", str(after)])
    assert result.exit_code == 0
    assert "signature changed" in result.output.lower()
    assert "foo" in result.output


def test_cli_diff_json_round_trips(tmp_path: Path) -> None:
    before = tmp_path / "b.py"
    after = tmp_path / "a.py"
    before.write_text("def foo(a):\n    return a\n", encoding="utf-8")
    after.write_text("def bar(a):\n    return a\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app, ["diff", "--before", str(before), "--after", str(after), "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["added"][0]["name"] == "bar"
    assert payload["deleted"][0]["name"] == "foo"
    assert payload["unchanged_count"] == 0


def test_cli_diff_missing_args_exits_2() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["diff", "--before", "/tmp/whatever.py"])
    assert result.exit_code == 2


def test_cli_diff_noop_message(tmp_path: Path) -> None:
    same = tmp_path / "same.py"
    same.write_text("def foo(a):\n    return a\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["diff", "--before", str(same), "--after", str(same)])
    assert result.exit_code == 0
    assert "no structural change" in result.output.lower()
