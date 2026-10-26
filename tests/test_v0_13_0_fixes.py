"""Adversarial regression tests for the v0.13.0 fixes.

Five defects pinned end-to-end against the shipped v0.12.0 source:

* ``fix-claim-file-shape-traceback`` — a ``--claim-file`` payload whose shape
  violates examples/claim_file_schema.json leaked an unhandled ``TypeError``
  traceback at exit 1 (``{"files": 5}``, ``{"base_dir": 5}``, a non-object
  action entry in the single-file, bare-list and multi-file shapes, a
  non-string ``before``/``after``/``language`` in a ``files`` entry, a
  non-list ``claimed_actions``) where the CLI's documented contract — and every
  sibling bad-claim-file path — is a clean exit-2 usage error.
* ``fix-undecodable-source-traceback`` — ``verify`` and ``diff`` read
  ``--before``/``--after`` with ``Path.read_text`` outside any handler, so a
  source file that isn't valid UTF-8 aborted with a ``UnicodeDecodeError``
  traceback at exit 1; the multi-file path already reported the same file as a
  clean exit-2 claim-file error.
* ``fix-doc-metadata-url-org`` — ``[project.urls]`` and the claim-file schema's
  ``$id`` named the nonexistent ``supermario-leo`` org (HTTP 404) while every
  shipped document names ``SuperMarioYL``.
* ``fix-changelog-history-and-refs`` — no changelog section for the v0.8.0 and
  v0.9.0 tags, and the reference block stopped at ``[0.7.0]``, so newer headings
  rendered as literal bracketed text and ``[Unreleased]`` compared from a
  five-release-old tag.
* ``fix-version-lockstep-013`` — five hand-maintained version surfaces with no
  test tying them together; ``src/diffgate/__init__.py`` is now the single
  source and ``pyproject.toml`` derives from it.

Each red->green test fails on the pre-fix source and passes after the fix; the
guards at the end of each section prove the fixes do not over-correct (a valid
payload, a decodable file and a passing claim still behave exactly as before).
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from typer.testing import CliRunner

import diffgate
from diffgate.cli import app

runner = CliRunner()

_REPO_ROOT = Path(__file__).resolve().parents[1]

_PY_BEFORE = "def foo():\n    return 1\n"
_PY_AFTER = "def bar():\n    return 1\n"


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    before = tmp_path / "b.py"
    after = tmp_path / "a.py"
    before.write_text(_PY_BEFORE, encoding="utf-8")
    after.write_text(_PY_AFTER, encoding="utf-8")
    return before, after


def _invoke_verify_claim_file(
    tmp_path: Path, payload: object, *, with_paths: bool = True
):
    claim_file = tmp_path / "claim.json"
    claim_file.write_text(json.dumps(payload), encoding="utf-8")
    args = ["verify", "--claim-file", str(claim_file)]
    if with_paths:
        before, after = _write_pair(tmp_path)
        args += ["--before", str(before), "--after", str(after)]
    return runner.invoke(app, args)


def _assert_clean_exit_2(result) -> None:
    assert result.exit_code == 2, (
        f"exit_code={result.exit_code}\n{result.output}\n"
        f"exception={result.exception!r}"
    )
    assert "Traceback" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


# --------------------------------------------------------------------------- #
# fix-claim-file-shape-traceback (src/diffgate/cli.py, src/diffgate/verifier.py)
# --------------------------------------------------------------------------- #
def test_files_not_a_list_exits_2(tmp_path: Path) -> None:
    """`{"files": 5}` must be a clean usage error.

    Red->green: pre-fix `for entry in payload["files"]` raised
    `TypeError: 'int' object is not iterable` and the CLI exited 1.
    """
    result = _invoke_verify_claim_file(tmp_path, {"files": 5}, with_paths=False)
    _assert_clean_exit_2(result)
    assert "claim-file error" in result.output
    assert "`files`" in result.output


def test_base_dir_not_a_string_exits_2(tmp_path: Path) -> None:
    """`{"base_dir": 5}` must be a clean usage error.

    Red->green: pre-fix `Path(payload["base_dir"])` raised
    `TypeError: argument should be a str or an os.PathLike object`.
    """
    payload = {
        "base_dir": 5,
        "files": [
            {
                "before": "b.py",
                "after": "a.py",
                "claimed_actions": [{"kind": "rename", "symbol": "foo"}],
            }
        ],
    }
    result = _invoke_verify_claim_file(tmp_path, payload, with_paths=False)
    _assert_clean_exit_2(result)
    assert "`base_dir`" in result.output


def test_single_file_non_object_action_exits_2(tmp_path: Path) -> None:
    """A non-object entry in `claimed_actions` must be a clean usage error.

    Red->green: pre-fix `ClaimedAction.from_dict("foo")` raised
    `TypeError: string indices must be integers`.
    """
    result = _invoke_verify_claim_file(tmp_path, {"claimed_actions": ["foo"]})
    _assert_clean_exit_2(result)
    assert "claim-file error" in result.output
    assert "must be a JSON object" in result.output


def test_bare_list_non_object_action_exits_2(tmp_path: Path) -> None:
    """The bare-list single-file shape has the same guard."""
    result = _invoke_verify_claim_file(tmp_path, ["foo"])
    _assert_clean_exit_2(result)
    assert "must be a JSON object" in result.output


def test_multi_file_non_object_action_exits_2(tmp_path: Path) -> None:
    """A non-object action inside a `files` entry has the same guard."""
    before, after = _write_pair(tmp_path)
    payload = {
        "files": [
            {
                "before": str(before),
                "after": str(after),
                "claimed_actions": ["foo"],
            }
        ]
    }
    result = _invoke_verify_claim_file(tmp_path, payload, with_paths=False)
    _assert_clean_exit_2(result)
    assert "must be a JSON object" in result.output


def test_multi_file_non_string_path_exits_2(tmp_path: Path) -> None:
    """A non-string `before` must be a clean usage error.

    Red->green: pre-fix `base / entry["before"]` raised
    `TypeError: unsupported operand type(s) for /: 'PosixPath' and 'int'` —
    the entry loop's handler only caught KeyError/ValueError.
    """
    payload = {
        "files": [
            {
                "before": 5,
                "after": "a.py",
                "claimed_actions": [{"kind": "add", "symbol": "bar"}],
            }
        ]
    }
    result = _invoke_verify_claim_file(tmp_path, payload, with_paths=False)
    _assert_clean_exit_2(result)
    assert "`before` must be a string" in result.output


def test_multi_file_non_string_language_exits_2(tmp_path: Path) -> None:
    """A non-string per-entry `language` must be a clean usage error.

    Red->green: pre-fix the value reached `override.lower()` and raised
    `AttributeError`.
    """
    before, after = _write_pair(tmp_path)
    payload = {
        "files": [
            {
                "before": str(before),
                "after": str(after),
                "language": 5,
                "claimed_actions": [{"kind": "rename", "symbol": "foo"}],
            }
        ]
    }
    result = _invoke_verify_claim_file(tmp_path, payload, with_paths=False)
    _assert_clean_exit_2(result)
    assert "`language` must be a string" in result.output


def test_multi_file_non_list_claimed_actions_exits_2(tmp_path: Path) -> None:
    """A non-list `claimed_actions` in an entry must be a clean usage error."""
    before, after = _write_pair(tmp_path)
    payload = {
        "files": [
            {
                "before": str(before),
                "after": str(after),
                "claimed_actions": {"kind": "rename", "symbol": "foo"},
            }
        ]
    }
    result = _invoke_verify_claim_file(tmp_path, payload, with_paths=False)
    _assert_clean_exit_2(result)
    assert "`claimed_actions` must be a list" in result.output


def test_non_string_payload_language_exits_2(tmp_path: Path) -> None:
    """A non-string top-level `language` must be a clean usage error."""
    result = _invoke_verify_claim_file(
        tmp_path,
        {
            "language": 5,
            "claimed_actions": [{"kind": "rename", "symbol": "foo"}],
        },
    )
    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output


def test_valid_claim_file_shapes_still_work(tmp_path: Path) -> None:
    """Guard: the two documented shapes still pass unchanged.

    A valid single-file claim file and a valid multi-file claim file must both
    still exit 0 — the shape validation must not reject what the schema allows.
    """
    single = _invoke_verify_claim_file(
        tmp_path,
        {
            "language": "python",
            "claimed_actions": [
                {"kind": "rename", "symbol": "foo", "new_symbol": "bar"}
            ],
        },
    )
    assert single.exit_code == 0, single.output

    before, after = _write_pair(tmp_path)
    multi = _invoke_verify_claim_file(
        tmp_path,
        {
            "base_dir": str(tmp_path),
            "files": [
                {
                    "path": "src/api.py",
                    "before": before.name,
                    "after": after.name,
                    "language": "python",
                    "claimed_actions": [
                        {"kind": "rename", "symbol": "foo", "new_symbol": "bar"}
                    ],
                }
            ],
        },
        with_paths=False,
    )
    assert multi.exit_code == 0, multi.output


# --------------------------------------------------------------------------- #
# fix-undecodable-source-traceback (src/diffgate/cli.py, _read_source)
# --------------------------------------------------------------------------- #
def test_verify_undecodable_before_exits_2(tmp_path: Path) -> None:
    """`verify` on a non-UTF-8 `--before` must be a clean usage error.

    Red->green: pre-fix the raw `UnicodeDecodeError` escaped as a traceback and
    the CLI exited 1.
    """
    before = tmp_path / "bin.py"
    before.write_bytes(b"\xff\xfe\x00\x01binary")
    _, after = _write_pair(tmp_path)
    result = runner.invoke(
        app,
        [
            "verify",
            "--before",
            str(before),
            "--after",
            str(after),
            "--claim",
            "rename foo->bar",
        ],
    )
    _assert_clean_exit_2(result)
    assert "cannot read" in result.output
    assert "UnicodeDecodeError" not in result.output


def test_diff_undecodable_before_exits_2(tmp_path: Path) -> None:
    """`diff` on a non-UTF-8 `--before` must be a clean usage error."""
    before = tmp_path / "bin.py"
    before.write_bytes(b"\xff\xfe\x00\x01binary")
    _, after = _write_pair(tmp_path)
    result = runner.invoke(
        app, ["diff", "--before", str(before), "--after", str(after)]
    )
    _assert_clean_exit_2(result)
    assert "cannot read" in result.output
    assert "UnicodeDecodeError" not in result.output


def test_multi_file_undecodable_still_exits_2(tmp_path: Path) -> None:
    """Guard: the multi-file path that already behaved correctly is unchanged.

    Both paths must now agree on exit 2 (the multi-file one reports it as a
    claim-file error; it never produced a traceback).
    """
    before = tmp_path / "bin.py"
    before.write_bytes(b"\xff\xfe\x00\x01binary")
    _, after = _write_pair(tmp_path)
    result = _invoke_verify_claim_file(
        tmp_path,
        {
            "files": [
                {
                    "before": str(before),
                    "after": str(after),
                    "claimed_actions": [
                        {"kind": "rename", "symbol": "foo", "new_symbol": "bar"}
                    ],
                }
            ]
        },
        with_paths=False,
    )
    _assert_clean_exit_2(result)
    assert "claim-file error" in result.output


def test_decodable_source_still_verifies(tmp_path: Path) -> None:
    """Guard: a normal UTF-8 file still passes (no behaviour change)."""
    before, after = _write_pair(tmp_path)
    result = runner.invoke(
        app,
        [
            "verify",
            "--before",
            str(before),
            "--after",
            str(after),
            "--claim",
            "rename foo->bar",
        ],
    )
    assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------- #
# fix-doc-metadata-url-org (pyproject.toml, examples/claim_file_schema.json)
# --------------------------------------------------------------------------- #
def test_packaged_metadata_urls_name_the_real_repo() -> None:
    """Every metadata URL must name the repo that actually exists.

    Red->green: pre-fix `[project.urls]` and the schema `$id` named
    `github.com/supermario-leo/diffgate`, which returns HTTP 404, while the
    READMEs, web/site.json and the Pages workflow all name `SuperMarioYL`.
    """
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    schema = (_REPO_ROOT / "examples" / "claim_file_schema.json").read_text(
        encoding="utf-8"
    )
    for text in (pyproject, schema):
        assert "supermario-leo" not in text
        assert "github.com/SuperMarioYL/diffgate" in text

    urls = tomllib.loads(pyproject)["project"]["urls"]
    for key in ("Homepage", "Repository", "Issues", "Changelog"):
        assert urls[key].startswith("https://github.com/SuperMarioYL/diffgate"), (
            key,
            urls[key],
        )


# --------------------------------------------------------------------------- #
# fix-changelog-history-and-refs (CHANGELOG.md)
# --------------------------------------------------------------------------- #
_HEADING_RE = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\]", re.MULTILINE)
_REF_RE = re.compile(r"^\[(?P<version>\d+\.\d+\.\d+)\]:", re.MULTILINE)


def test_changelog_documents_every_released_minor() -> None:
    """The changelog must carry a section for v0.8.0 and v0.9.0.

    Red->green: pre-fix the headings jumped from [0.10.0] to [0.7.0] although
    both tags exist, while line 2 promises all notable changes are documented.
    """
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = set(_HEADING_RE.findall(changelog))
    for version in ("0.8.0", "0.9.0", "0.10.0", "0.11.0", "0.12.0", "0.13.0"):
        assert version in headings, f"missing changelog section for {version}"


def test_every_changelog_heading_has_a_link_reference() -> None:
    """Each version heading must have a reference definition, and vice versa.

    Red->green: pre-fix the reference block stopped at [0.7.0], so the newer
    headings rendered as literal bracketed text.
    """
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = set(_HEADING_RE.findall(changelog))
    refs = set(_REF_RE.findall(changelog))
    assert headings - refs == set(), f"headings without a link ref: {headings - refs}"
    assert refs - headings == set(), f"link refs without a heading: {refs - headings}"


def test_unreleased_reference_points_at_the_newest_release() -> None:
    """`[Unreleased]` must compare the newest tag, not a stale one.

    Red->green: pre-fix it read `compare/v0.7.0...HEAD`, five releases stale.
    """
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = max(_HEADING_RE.findall(changelog), key=lambda v: tuple(map(int, v.split("."))))
    match = re.search(r"^\[Unreleased\]: (?P<url>\S+)$", changelog, re.MULTILINE)
    assert match is not None, "no [Unreleased] link reference"
    assert f"compare/v{newest}...HEAD" in match.group("url"), match.group("url")


def test_changelog_references_name_the_real_repo() -> None:
    """No reference may point at the 404 org."""
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    refs = [
        line
        for line in changelog.splitlines()
        if line.startswith("[") and "]: http" in line
    ]
    assert refs, "no link references found"
    for line in refs:
        assert line.startswith(("[Unreleased]: https://github.com/SuperMarioYL/", "[0.")), (
            line
        )


# --------------------------------------------------------------------------- #
# fix-version-lockstep-013 (src/diffgate/__init__.py + every derived surface)
# --------------------------------------------------------------------------- #
def test_version_lockstep_across_every_surface() -> None:
    """Every version surface must agree with the single source.

    Red->green: pre-fix nothing tied __version__, pyproject.toml,
    web/site.json's three version keys and the newest changelog heading
    together — the same drift class the v0.9.0 guard was added for, when the
    package string sat at 0.7.0 while the lineage was at v0.8.0.
    """
    version = diffgate.__version__

    site = json.loads((_REPO_ROOT / "web" / "site.json").read_text(encoding="utf-8"))
    assert site["meta"]["content_version"].lstrip("v") == version
    assert site["content_version"].lstrip("v") == version
    assert site["changelog"][0]["version"].lstrip("v") == version

    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = _HEADING_RE.findall(changelog)
    newest = max(headings, key=lambda v: tuple(map(int, v.split("."))))
    assert newest == version


def test_pyproject_has_no_duplicate_version_literal() -> None:
    """pyproject must derive the version, not carry a second copy.

    Red->green: pre-fix `[project] version` was a hand-maintained duplicate of
    the package constant, which is exactly how the two drifted apart before.
    """
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in data["project"]
    assert data["project"]["dynamic"] == ["version"]
    assert data["tool"]["hatch"]["version"]["path"] == "src/diffgate/__init__.py"


def test_cli_version_flag_reports_the_package_version() -> None:
    """`diffgate --version` must report the single source's string."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert diffgate.__version__ in result.output
