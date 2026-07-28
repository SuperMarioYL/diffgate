"""Adversarial regression tests for the v0.8.0 correctness fix.

``fix-enum-declarations-invisible`` — TS/TSX ``enum_declaration`` (incl.
``const enum``) and C++ ``enum_specifier`` (incl. ``enum class``) were absent
from ``LANGUAGE_RULES``, so ``parse_symbols`` returned *zero* Symbols for an
enum. A *truthful* ``add Color`` / ``rename Color→Hue`` on ``enum Color { Red }``
therefore false-failed: the verifier's name-matching found neither the old nor
new name in the structural diff (the no-op net even called the rename a
"no-op edit"). Java's ``enum_declaration`` was already mapped, so the TS/C++
omissions were the inconsistency — the last common nominal-type declaration
still invisible in two of DiffGate's largest ecosystems.

Each red->green test fails on the pre-fix source (truthful add/rename return
``passed=False``) and passes after the one-line-per-language fix that maps the
enum node to the ``class`` kind, mirroring Java's ``enum_declaration`` entry.
The no-op and lying-edit guards must keep failing (the fix must not
over-correct into a silent-success lie).
"""

from __future__ import annotations

import pytest

from diffgate.parsers import parse_symbols
from diffgate.verifier import EditClaim, verify

# Each enum "form" the fix makes visible: (language, Color-decl, Hue-decl).
# Pre-fix all five yielded ZERO Symbols; post-fix each yields a `Color` class
# symbol, so a truthful rename/add stops false-failing. The Hue-decl keeps the
# enumerator body byte-identical so the diff is a clean delete-Color + add-Hue.
_ENUM_FORMS = [
    pytest.param(
        "typescript", "enum Color { Red, Green, Blue }", "enum Hue { Red, Green, Blue }",
        id="ts-enum",
    ),
    pytest.param(
        "typescript", "const enum Color { Red }", "const enum Hue { Red }",
        id="ts-const-enum",
    ),
    pytest.param(
        "tsx", "enum Color { Red }", "enum Hue { Red }", id="tsx-enum",
    ),
    pytest.param(
        "cpp", "enum Color { Red };", "enum Hue { Red };", id="cpp-enum",
    ),
    pytest.param(
        "cpp", "enum class Color { Red };", "enum class Hue { Red };", id="cpp-enum-class",
    ),
]

# A language-appropriate host declaration so the `add` test's after-blob isn't
# a bare enum-only file (keeps the enum the ONE added symbol, mirroring the
# v0.4.0 Java-record add test shape).
_HOST = {"typescript": "class App {}\n", "tsx": "class App {}\n", "cpp": "class App {};\n"}


# --------------------------------------------------------------------------- #
# Visibility — pre-fix every form below yielded ZERO symbols.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lang,color,_hue", _ENUM_FORMS)
def test_enum_yields_class_symbol(lang: str, color: str, _hue: str) -> None:
    """An enum declaration must parse as a `Color` class symbol at module
    scope with a non-empty body hash (so a rename / enumerator edit registers).
    Red->green: pre-fix `parse_symbols` returned ``[]``.
    """
    syms = parse_symbols(color, lang)
    color_sym = next((s for s in syms if s.name == "Color"), None)
    assert color_sym is not None, (
        f"enum 'Color' yielded no symbol in {lang!r}: {[s.name for s in syms]}"
    )
    assert color_sym.kind == "class"
    assert color_sym.scope == "", "a top-level enum keys by module scope ''"
    assert color_sym.body_hash, (
        "enum body hash must be non-empty so renames / enumerator edits register"
    )


# --------------------------------------------------------------------------- #
# Truthful add — pre-fix the enum was invisible so `add Color` false-failed.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lang,color,_hue", _ENUM_FORMS)
def test_enum_truthful_add_passes(lang: str, color: str, _hue: str) -> None:
    """Adding a fresh enum and claiming `add Color` must PASS. Red->green:
    pre-fix the after-blob's enum yielded no symbol, so `Color` was absent
    from `diff.added` and the claim false-failed.
    """
    host = _HOST[lang]
    claim = EditClaim.from_dict(
        {
            "before_blob": host,
            "after_blob": host + color,
            "language": lang,
            "claimed_actions": [{"kind": "add", "symbol": "Color"}],
        }
    )
    verdict = verify(claim)
    assert verdict.passed is True, (
        f"truthful enum add must pass in {lang!r}, got mismatches: "
        f"{[m.to_dict() for m in verdict.mismatches]}"
    )


# --------------------------------------------------------------------------- #
# Truthful rename — pre-fix both halves were invisible so the diff was a no-op
# and `rename Color->Hue` false-failed (the no-op net even called it a no-op).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lang,color,hue", _ENUM_FORMS)
def test_enum_truthful_rename_passes(lang: str, color: str, hue: str) -> None:
    """Renaming an enum (`Color -> Hue`, enumerator body unchanged) and
    claiming `rename Color->Hue` must PASS. Red->green: pre-fix both blobs
    yielded zero symbols, the structural diff was a no-op, and the rename
    false-failed with the misleading 'no-op edit' reason.
    """
    claim = EditClaim.from_dict(
        {
            "before_blob": color,
            "after_blob": hue,
            "language": lang,
            "claimed_actions": [
                {"kind": "rename", "symbol": "Color", "new_symbol": "Hue"}
            ],
        }
    )
    verdict = verify(claim)
    assert verdict.passed is True, (
        f"truthful enum rename must pass in {lang!r}, got mismatches: "
        f"{[m.to_dict() for m in verdict.mismatches]}"
    )


# --------------------------------------------------------------------------- #
# No-op guard — the fix must not make a no-op rename pass.
# --------------------------------------------------------------------------- #
def test_typescript_enum_noop_rename_fails() -> None:
    """Claiming `rename Color->Hue` when the blob is byte-identical must still
    FAIL — the diff is a genuine no-op. Guards against the fix over-correcting
    an unchanged enum into a silent-success pass.
    """
    blob = "enum Color { Red, Green, Blue }\n"
    claim = EditClaim.from_dict(
        {
            "before_blob": blob,
            "after_blob": blob,
            "language": "typescript",
            "claimed_actions": [
                {"kind": "rename", "symbol": "Color", "new_symbol": "Hue"}
            ],
        }
    )
    verdict = verify(claim)
    assert not verdict.passed, "a no-op rename must not pass"
    assert verdict.structural_diff.is_noop(), (
        "Color is unchanged on both sides — the diff must be a true no-op"
    )


# --------------------------------------------------------------------------- #
# Lying-add guard — claiming `add Color` when a *different* enum was added.
# --------------------------------------------------------------------------- #
def test_typescript_enum_lying_add_fails() -> None:
    """Claiming `add Color` while a different enum `Shade` was added must FAIL
    — the fix must surface the lie, not grant a pass because *some* enum
    appeared. Guards against over-broad add matching.
    """
    before = "class App {}\n"
    after = "class App {}\nenum Shade { Red }\n"
    claim = EditClaim.from_dict(
        {
            "before_blob": before,
            "after_blob": after,
            "language": "typescript",
            "claimed_actions": [{"kind": "add", "symbol": "Color"}],
        }
    )
    assert verify(claim).passed is False, "a lying add must not pass"


# --------------------------------------------------------------------------- #
# Lying-rename guard — a delete is not a rename (covers `enum class`).
# --------------------------------------------------------------------------- #
def test_cpp_enum_class_lying_rename_fails() -> None:
    """Claiming `rename Color->Hue` when `Color` was deleted but `Hue` was
    never added must FAIL (a bare delete is not a rename). Covers the C++
    `enum class` form. Guards against the fix mistaking a delete-only diff
    for a rename.
    """
    before = "enum class Color { Red };\n"
    after = "class App {};\n"  # Color gone; Hue never added
    claim = EditClaim.from_dict(
        {
            "before_blob": before,
            "after_blob": after,
            "language": "cpp",
            "claimed_actions": [
                {"kind": "rename", "symbol": "Color", "new_symbol": "Hue"}
            ],
        }
    )
    assert verify(claim).passed is False, "a lying rename must not pass"
