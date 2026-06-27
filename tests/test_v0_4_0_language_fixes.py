"""Regression tests for the v0.4.0 correctness fixes.

Each test pins a defect that was verified end-to-end against the shipped v0.3.0
source: a *truthful* edit in one of the m6 languages was being false-positived
(or the language went undetected) because of a parser / extension gap. These
tests fail on the v0.3.0 code and pass after the v0.4.0 fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from diffgate.cli import _detect_language
from diffgate.parsers import SUPPORTED_LANGUAGES, parse_symbols
from diffgate.verifier import EditClaim, verify


# ---------------------------------------------------------------------------
# fix-cpp-out-of-line-method-qualified-name (src/diffgate/parsers.py)
# ---------------------------------------------------------------------------
def test_cpp_out_of_line_method_name_is_unqualified() -> None:
    """`void Foo::bar(...)` must parse as symbol name `bar`, not `Foo::bar`."""
    syms = parse_symbols("void Foo::bar(int x) {}", "cpp")
    names = {s.name for s in syms}
    assert "bar" in names, f"expected unqualified 'bar', got {names}"
    assert "Foo::bar" not in names


def test_cpp_out_of_line_truthful_rename_passes() -> None:
    """A real out-of-line method rename is a truthful edit and must PASS."""
    claim = EditClaim.from_dict(
        {
            "before_blob": "void Foo::bar(int x) {}",
            "after_blob": "void Foo::baz(int x) {}",
            "language": "cpp",
            "claimed_actions": [{"kind": "rename", "symbol": "bar", "new_symbol": "baz"}],
        }
    )
    assert verify(claim).passed is True


# ---------------------------------------------------------------------------
# fix-java-record-declaration-invisible (src/diffgate/parsers.py)
# ---------------------------------------------------------------------------
def test_java_record_yields_a_symbol() -> None:
    syms = parse_symbols("public record Point(int x, int y) {}", "java")
    assert any(s.name == "Point" for s in syms), [s.name for s in syms]


def test_java_record_truthful_add_passes() -> None:
    claim = EditClaim.from_dict(
        {
            "before_blob": "public class App {}",
            "after_blob": "public class App {}\npublic record Point(int x, int y) {}",
            "language": "java",
            "claimed_actions": [{"kind": "add", "symbol": "Point"}],
        }
    )
    assert verify(claim).passed is True


def test_java_record_truthful_rename_passes() -> None:
    claim = EditClaim.from_dict(
        {
            "before_blob": "public record Point(int x, int y) {}",
            "after_blob": "public record Pt(int x, int y) {}",
            "language": "java",
            "claimed_actions": [{"kind": "rename", "symbol": "Point", "new_symbol": "Pt"}],
        }
    )
    assert verify(claim).passed is True


# ---------------------------------------------------------------------------
# fix-cpp-header-extension-not-detected (src/diffgate/cli.py)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("filename", ["widget.h", "widget.hxx"])
def test_cpp_header_extensions_auto_detect_to_cpp(filename: str) -> None:
    assert _detect_language(Path(filename), "auto") == "cpp"


# ---------------------------------------------------------------------------
# fix-stale-supported-languages-in-mcp-doc-and-readme (parsers + docs)
# ---------------------------------------------------------------------------
def test_supported_languages_is_the_full_nine() -> None:
    assert set(SUPPORTED_LANGUAGES) >= {
        "python",
        "typescript",
        "tsx",
        "javascript",
        "go",
        "rust",
        "java",
        "cpp",
        "ruby",
    }


def test_mcp_verify_edit_docstring_lists_all_nine_languages() -> None:
    from diffgate import mcp_server

    doc = (mcp_server.verify_edit.__doc__ or "").lower()
    for lang in ("java", "cpp", "ruby"):
        assert lang in doc, f"verify_edit docstring still omits {lang!r}"
