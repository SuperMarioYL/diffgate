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
from .verifier import ClaimedAction, EditClaim, Verdict, verify

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
        ...,
        "--before",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the file BEFORE the agent's edit.",
    ),
    after: Path = typer.Option(
        ...,
        "--after",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the file AFTER the agent's edit.",
    ),
    claim: list[str] = typer.Option(
        ...,
        "--claim",
        "-c",
        help=(
            "Claimed action(s). Examples: 'rename foo→bar in module_x', "
            "'add helper_fn', 'delete OldClass'. Pass --claim multiple times "
            "or separate with ';'."
        ),
    ),
    language: str = typer.Option(
        "auto",
        "--lang",
        help="Source language: python|typescript|tsx|javascript|go|rust|auto.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a machine-readable JSON verdict instead of the human table.",
    ),
) -> None:
    """Verify an agent's edit claim against the AST diff.

    Exits 0 if every claimed action is reflected in the structural diff,
    and 1 otherwise (with a per-mismatch explanation).
    """
    lang = _detect_language(before, language)

    try:
        actions = _parse_claims(claim)
    except ValueError as exc:
        _console_stderr.print(f"[bold red]claim parse error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

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


if __name__ == "__main__":  # pragma: no cover
    app()
