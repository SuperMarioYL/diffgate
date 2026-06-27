"""``diffgate`` CLI — the thin wrapper over :func:`verifier.verify`.

The CLI exists so a coding agent (or a CI hook, or a human reviewer) can run
a one-shot check from the shell:

.. code-block:: bash

    diffgate verify --before X.py --after X.py.new --claim "rename foo→bar"

Exit code is 0 on a structural match and 1 on mismatch — exactly the contract
an agent loop needs to gate on. Pass ``--json`` to get a machine-readable
verdict instead of the rich table.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .verifier import (
    ClaimedAction,
    EditClaim,
    FileClaim,
    Verdict,
    verify,
    verify_multi,
)

app = typer.Typer(
    name="diffgate",
    help="Structural verification gate for coding agents.",
    add_completion=False,
    no_args_is_help=True,
)

_console_stdout = Console()
_console_stderr = Console(stderr=True)

# Map common file extensions to tree-sitter language ids.
EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    # `.h` / `.hxx` are the most common C++ header conventions and were
    # missing, so the default `--lang auto` raised BadParameter on `widget.h`
    # and exited 2 before the verifier ran — silently disabling C++ for the
    # single most common header naming. The tree-sitter cpp grammar parses
    # plain-C headers too, so C headers are covered as a side effect.
    ".h": "cpp",
    ".hxx": "cpp",
    ".rb": "ruby",
}

# Parsers for the ``--claim`` natural-language mini-DSL. The agent (or the
# user) writes claims like:
#
#     rename foo→bar in module_x
#     add helper_fn
#     delete OldClass
#     signature_change handle_request
#     move foo to other_module
#
# Multiple claims are joined with ``;`` or `` and ``.
_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^rename\s+(?P<old>[\w.]+)\s*(?:->|→|=>|\s+to\s+)\s*(?P<new>[\w.]+)\s*$",
            re.IGNORECASE,
        ),
        "rename",
    ),
    (
        re.compile(
            r"^(?:add|added|introduce)\s+(?:function\s+|class\s+|method\s+|fn\s+)?"
            r"(?P<sym>[\w.]+)\s*$",
            re.IGNORECASE,
        ),
        "add",
    ),
    (
        re.compile(
            r"^(?:delete|deleted|remove|removed|drop)\s+"
            r"(?:function\s+|class\s+|method\s+|fn\s+)?(?P<sym>[\w.]+)\s*$",
            re.IGNORECASE,
        ),
        "delete",
    ),
    (
        re.compile(
            r"^(?:move|moved|relocate)\s+(?P<sym>[\w.]+)"
            r"(?:\s+(?:to|into)\s+(?P<dest>[\w./]+))?\s*$",
            re.IGNORECASE,
        ),
        "move",
    ),
    (
        re.compile(
            r"^(?:signature_change|change\s+signature\s+of|retype)\s+"
            r"(?P<sym>[\w.]+)\s*$",
            re.IGNORECASE,
        ),
        "signature_change",
    ),
)


def _version_callback(value: bool) -> None:
    if value:
        _console_stdout.print(f"diffgate {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """DiffGate root command — see ``diffgate verify --help`` for the gate."""


@app.command("verify")
def verify_cmd(
    before: Path = typer.Option(
        None,
        "--before",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the file BEFORE the agent's edit.",
    ),
    after: Path = typer.Option(
        None,
        "--after",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the file AFTER the agent's edit.",
    ),
    claim: list[str] = typer.Option(
        None,
        "--claim",
        "-c",
        help=(
            "Claimed action(s). Examples: 'rename foo→bar in module_x', "
            "'add helper_fn', 'delete OldClass'. Pass --claim multiple times "
            "or separate with ';'."
        ),
    ),
    claim_file: str = typer.Option(
        None,
        "--claim-file",
        help=(
            "Path to a structured JSON claim file (see "
            "examples/claim_file_schema.json), or '-' to read it from stdin. "
            "Feeds claimed_actions straight to the verifier — CLI/MCP parity. "
            "A multi-file claim file carries its own before/after paths and is "
            "gated in one call (omit --before/--after)."
        ),
    ),
    language: str = typer.Option(
        "auto",
        "--lang",
        help=(
            "Source language: python|typescript|tsx|javascript|go|rust|"
            "java|cpp|ruby|auto."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a machine-readable JSON verdict instead of the human table.",
    ),
) -> None:
    """Verify an agent's edit claim against the AST diff.

    Exits 0 if every claimed action is reflected in the structural diff,
    and 1 otherwise (with a per-mismatch explanation). The claim can come from
    the ``--claim`` mini-DSL or a structured ``--claim-file`` (single- or
    multi-file).
    """
    if claim and claim_file:
        _console_stderr.print(
            "[bold red]error:[/bold red] pass either --claim or --claim-file, "
            "not both."
        )
        raise typer.Exit(code=2)

    if claim_file:
        try:
            payload = _load_claim_file(claim_file)
        except (OSError, ValueError) as exc:
            _console_stderr.print(f"[bold red]claim-file error:[/bold red] {exc}")
            raise typer.Exit(code=2) from exc

        if isinstance(payload, dict) and "files" in payload:
            _run_multi_file(payload, claim_file, language, json_output)
            return

        actions = _actions_from_payload(payload)
        payload_lang = payload.get("language") if isinstance(payload, dict) else None
    else:
        if not claim:
            _console_stderr.print(
                "[bold red]error:[/bold red] provide a claim via --claim or "
                "--claim-file."
            )
            raise typer.Exit(code=2)
        try:
            actions = _parse_claims(claim)
        except ValueError as exc:
            _console_stderr.print(f"[bold red]claim parse error:[/bold red] {exc}")
            raise typer.Exit(code=2) from exc
        payload_lang = None

    if before is None or after is None:
        _console_stderr.print(
            "[bold red]error:[/bold red] single-file verify needs --before and "
            "--after (use a multi-file claim file to gate several files)."
        )
        raise typer.Exit(code=2)

    lang = _detect_language(before, payload_lang or language)

    edit = EditClaim(
        before_blob=before.read_text(encoding="utf-8"),
        after_blob=after.read_text(encoding="utf-8"),
        language=lang,
        claimed_actions=actions,
    )

    verdict = verify(edit)

    if json_output:
        json.dump(verdict.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        _render_verdict(verdict, before, after, lang)

    raise typer.Exit(code=0 if verdict.passed else 1)


def _run_multi_file(
    payload: dict, claim_file: str, language: str, json_output: bool
) -> None:
    """Build FileClaims from a multi-file claim payload and gate them at once."""
    base = _claim_file_base_dir(payload, claim_file)
    file_claims: list[FileClaim] = []
    for entry in payload["files"]:
        if not isinstance(entry, dict):
            _console_stderr.print(
                "[bold red]claim-file error:[/bold red] each `files` entry must "
                "be an object."
            )
            raise typer.Exit(code=2)
        try:
            before_path = (base / entry["before"]).resolve()
            after_path = (base / entry["after"]).resolve()
            label = entry.get("path") or entry["after"]
            actions = [ClaimedAction.from_dict(a) for a in entry["claimed_actions"]]
        except (KeyError, ValueError) as exc:
            _console_stderr.print(f"[bold red]claim-file error:[/bold red] {exc}")
            raise typer.Exit(code=2) from exc

        try:
            entry_lang = _detect_language(
                after_path, entry.get("language") or language
            )
            edit = EditClaim(
                before_blob=before_path.read_text(encoding="utf-8"),
                after_blob=after_path.read_text(encoding="utf-8"),
                language=entry_lang,
                claimed_actions=actions,
            )
        except (OSError, typer.BadParameter) as exc:
            _console_stderr.print(
                f"[bold red]claim-file error:[/bold red] {label}: {exc}"
            )
            raise typer.Exit(code=2) from exc
        file_claims.append(FileClaim(path=str(label), claim=edit))

    verdict = verify_multi(file_claims)

    if json_output:
        json.dump(verdict.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        _render_multi_verdict(verdict, len(file_claims))

    raise typer.Exit(code=0 if verdict.passed else 1)


@app.command("mcp-server")
def mcp_server_cmd(
    stdio: bool = typer.Option(
        True,
        "--stdio/--no-stdio",
        help="Serve the MCP tool over stdio (the only supported transport).",
    ),
) -> None:
    """Run the DiffGate MCP server so an agent loop can gate on every edit.

    Register it in ``mcp.json`` with
    ``{"command": "diffgate", "args": ["mcp-server", "--stdio"]}`` and the
    agent gains a single ``verify_edit`` tool. The transport is stdio so the
    gate stays fully local — no daemon, no network.
    """
    if not stdio:
        _console_stderr.print(
            "[bold red]error:[/bold red] only --stdio transport is supported."
        )
        raise typer.Exit(code=2)
    # Imported lazily so `diffgate verify` doesn't pay the FastMCP import cost.
    from .mcp_server import run_stdio  # noqa: PLC0415

    run_stdio()


@app.command("bench")
def bench_cmd(
    traces: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="JSONL trace file of agent edits with ground-truth `was_lie` labels.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of the human report.",
    ),
) -> None:
    """Replay a JSONL edit trace and report catch-rate vs. ground truth.

    Each line is an ``EditClaim`` plus a ``was_lie`` label; DiffGate's verdict
    is scored against it to produce precision / recall / catch-rate — exactly
    the numbers the README badge quotes.
    """
    from .bench import render_report, run_bench  # noqa: PLC0415

    result = run_bench(traces)
    if json_output:
        json.dump(result.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        _console_stdout.print(render_report(result))


def _load_claim_file(spec: str) -> object:
    """Read a JSON claim file (or stdin when ``spec`` is ``-``)."""
    if spec == "-":
        raw = sys.stdin.read()
    else:
        path = Path(spec)
        if not path.exists():
            raise OSError(f"claim file not found: {spec}")
        raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in claim file: {exc.msg}") from exc


def _actions_from_payload(payload: object) -> list[ClaimedAction]:
    """Coerce a single-file claim payload into a list of ClaimedActions."""
    if isinstance(payload, list):
        raw_actions = payload
    elif isinstance(payload, dict):
        raw_actions = payload.get("claimed_actions")
        if raw_actions is None:
            raise ValueError(
                "claim file object must have a 'claimed_actions' list "
                "(or be a bare list of actions, or a multi-file 'files' object)."
            )
    else:
        raise ValueError("claim file must be a list or an object.")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ValueError("claim file must contain at least one claimed action.")
    return [ClaimedAction.from_dict(a) for a in raw_actions]


def _claim_file_base_dir(payload: dict, claim_file: str) -> Path:
    """Resolve the base directory per-file paths are relative to."""
    if payload.get("base_dir"):
        return Path(payload["base_dir"])
    if claim_file == "-":
        return Path.cwd()
    return Path(claim_file).resolve().parent


def _detect_language(path: Path, override: str) -> str:
    if override and override.lower() != "auto":
        return override.lower()
    lang = EXT_TO_LANG.get(path.suffix.lower())
    if lang is None:
        raise typer.BadParameter(
            f"could not infer language from {path.name!r}; pass --lang explicitly."
        )
    return lang


def _parse_claims(raw_claims: list[str]) -> list[ClaimedAction]:
    fragments: list[str] = []
    for raw in raw_claims:
        for piece in re.split(r"\s*(?:;|&&|\sand\s)\s*", raw.strip()):
            if piece:
                fragments.append(piece)

    if not fragments:
        raise ValueError("no claims provided")

    actions: list[ClaimedAction] = []
    for fragment in fragments:
        actions.append(_parse_one_claim(fragment))
    return actions


def _parse_one_claim(fragment: str) -> ClaimedAction:
    scope = ""
    # Pull off any trailing ``in <scope>`` qualifier.
    scope_match = re.search(r"\bin\s+([\w./]+)\s*$", fragment, re.IGNORECASE)
    if scope_match:
        scope = scope_match.group(1)
        fragment = fragment[: scope_match.start()].strip()

    for pattern, kind in _CLAIM_PATTERNS:
        m = pattern.match(fragment)
        if not m:
            continue
        groups = m.groupdict()
        if kind == "rename":
            return ClaimedAction(
                kind="rename",
                symbol=groups["old"],
                new_symbol=groups["new"],
                scope=scope,
            )
        return ClaimedAction(
            kind=kind,
            symbol=groups["sym"],
            scope=scope or (groups.get("dest") or ""),
        )

    raise ValueError(
        f"could not parse claim fragment {fragment!r}; expected something like "
        f"'rename foo→bar', 'add helper_fn', 'delete OldClass'."
    )


def _render_verdict(verdict: Verdict, before: Path, after: Path, lang: str) -> None:
    diff = verdict.structural_diff
    header = (
        f"[dim]{before.name}[/dim] [bold]→[/bold] [dim]{after.name}[/dim]  "
        f"[dim]({lang})[/dim]"
    )
    _console_stdout.print(header)

    if verdict.passed:
        _console_stdout.print(
            "[bold green]✓ PASSED[/bold green] — every claimed action is "
            "reflected in the AST diff."
        )
    else:
        _console_stdout.print(
            f"[bold red]✗ FAILED[/bold red] — {len(verdict.mismatches)} "
            f"mismatch(es) detected:\n"
        )
        table = Table(show_lines=True, header_style="bold")
        table.add_column("Kind", style="cyan", no_wrap=True)
        table.add_column("Symbol", style="yellow")
        table.add_column("Reason", style="red")
        for m in verdict.mismatches:
            sym = m.action.symbol
            if m.action.new_symbol:
                sym = f"{m.action.symbol} → {m.action.new_symbol}"
            if m.action.scope:
                sym = f"{sym}  [dim](scope: {m.action.scope})[/dim]"
            table.add_row(m.action.kind, sym, m.reason)
        _console_stdout.print(table)

    _console_stdout.print(
        f"\n[dim]Structural diff: +{len(diff.added)} added · "
        f"-{len(diff.deleted)} deleted · "
        f"~{len(diff.signature_changed)} sig_changed · "
        f"…{len(diff.body_changed)} body_changed · "
        f"={len(diff.unchanged)} unchanged[/dim]"
    )


def _render_multi_verdict(verdict: Verdict, file_count: int) -> None:
    diff = verdict.structural_diff
    _console_stdout.print(f"[dim]multi-file verify — {file_count} file(s)[/dim]")

    if verdict.passed:
        _console_stdout.print(
            "[bold green]✓ PASSED[/bold green] — every claimed action is "
            "reflected in the AST diff across all files."
        )
    else:
        _console_stdout.print(
            f"[bold red]✗ FAILED[/bold red] — {len(verdict.mismatches)} "
            f"mismatch(es) detected:\n"
        )
        table = Table(show_lines=True, header_style="bold")
        table.add_column("Kind", style="cyan", no_wrap=True)
        table.add_column("Symbol", style="yellow")
        table.add_column("Reason", style="red")
        for m in verdict.mismatches:
            sym = m.action.symbol
            if m.action.new_symbol:
                sym = f"{m.action.symbol} → {m.action.new_symbol}"
            if m.action.scope:
                sym = f"{sym}  [dim](scope: {m.action.scope})[/dim]"
            table.add_row(m.action.kind, sym, m.reason)
        _console_stdout.print(table)

    _console_stdout.print(
        f"\n[dim]Aggregate structural diff: +{len(diff.added)} added · "
        f"-{len(diff.deleted)} deleted · "
        f"~{len(diff.signature_changed)} sig_changed · "
        f"…{len(diff.body_changed)} body_changed · "
        f"={len(diff.unchanged)} unchanged[/dim]"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
