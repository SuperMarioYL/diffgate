"""MCP server exposing :func:`verifier.verify` as a callable tool.

This is the m2 milestone — once a coding agent (Claude Code, Cursor, an
in-house LangGraph loop) registers DiffGate via ``mcp.json``, the agent can
call ``verify_edit`` after each tool-call edit and treat a non-passing
verdict as backpressure that aborts or retries the step.

Run with:

.. code-block:: bash

    diffgate mcp-server --stdio

The transport is stdio so the loop is fully local — no daemon, no network
round-trip. We deliberately expose exactly one tool. More tools means more
surface area for an agent to call the *wrong* thing; the whole pitch is a
single deterministic gate.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from .verifier import EditClaim, verify

mcp = FastMCP(
    name="diffgate",
    instructions=(
        "DiffGate is the structural verification gate for coding agents. "
        "After EVERY edit you make to a source file, call `verify_edit` "
        "with the before-blob, the after-blob, the language id, and the "
        "actions you claim you performed. If the returned verdict has "
        "passed=false, treat the step as failed — do not report success "
        "to the user. Retry with the mismatch reasons as feedback."
    ),
)


def verify_edit_payload(
    before_blob: str,
    after_blob: str,
    language: str,
    claimed_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure verifier wrapper — separated so unit tests don't depend on FastMCP wrapping."""
    claim = EditClaim.from_dict(
        {
            "before_blob": before_blob,
            "after_blob": after_blob,
            "language": language,
            "claimed_actions": claimed_actions,
        }
    )
    verdict = verify(claim)
    return verdict.to_dict()


@mcp.tool(
    name="verify_edit",
    description=(
        "Structurally verify that a code edit matches the claimed actions. "
        "Returns {passed: bool, mismatches: [...], structural_diff: {...}}. "
        "Agents MUST call this after every source-file edit and treat "
        "passed=false as a hard failure of the edit step."
    ),
)
def verify_edit(
    before_blob: str,
    after_blob: str,
    language: str,
    claimed_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify one agent edit against its claimed actions.

    Parameters
    ----------
    before_blob:
        Full file contents BEFORE the edit (utf-8 text).
    after_blob:
        Full file contents AFTER the edit (utf-8 text).
    language:
        Source language id. One of: python, typescript, tsx, javascript,
        go, rust, java, cpp, ruby.
    claimed_actions:
        List of action dicts. Each dict has ``kind`` (one of "rename",
        "add", "delete", "move", "signature_change"), ``symbol`` (source
        name), and optionally ``new_symbol`` (target for renames/moves)
        and ``scope`` (containing class/module name).

    Returns
    -------
    A dict matching :meth:`verifier.Verdict.to_dict` — ``passed`` is the
    headline signal; ``mismatches`` explains each lie; ``structural_diff``
    is the raw AST delta in case the agent wants to reason about it.
    """
    return verify_edit_payload(before_blob, after_blob, language, claimed_actions)


@mcp.tool(
    name="diffgate_version",
    description="Return the running DiffGate version. Useful for compatibility checks.",
)
def diffgate_version() -> dict[str, str]:
    return {"version": __version__}


def run_stdio() -> None:
    """Entry point used by ``diffgate mcp-server --stdio``."""
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    run_stdio()
