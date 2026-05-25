# Wire DiffGate into Claude Code (or any MCP-aware agent)

DiffGate ships an MCP server. Register it once in your agent's `mcp.json`,
then instruct the agent to call `verify_edit` after every source-file edit.
A `passed: false` verdict is the structural backpressure signal — the agent
treats the step as failed and retries with the mismatch reasons as
feedback.

The whole loop is local: no daemon, no cloud, no auth.

## 1. Install

```bash
pipx install diffgate          # or:  uv tool install diffgate
diffgate --version             # sanity check
```

## 2. Register the MCP server (3 lines)

Claude Code reads `~/.config/claude-code/mcp.json` (Linux/macOS) or the
equivalent on your platform. Add the `diffgate` block:

```json
{
  "mcpServers": {
    "diffgate": {
      "command": "diffgate",
      "args": ["mcp-server", "--stdio"]
    }
  }
}
```

> If you haven't shipped a CLI entry-point yet, you can spawn the module
> directly:
>
> ```json
> {
>   "mcpServers": {
>     "diffgate": {
>       "command": "python",
>       "args": ["-m", "diffgate.mcp_server"]
>     }
>   }
> }
> ```

Restart Claude Code. You should see `diffgate` listed under the active
MCP servers, exposing one tool: `verify_edit`.

## 3. Tell the agent to gate on it

Add this to your project's `CLAUDE.md` (or your agent system prompt):

```markdown
## DiffGate verification protocol

After EVERY edit you make to a source file you MUST call the
`verify_edit` MCP tool with:

- `before_blob`: the file contents BEFORE your edit
- `after_blob`: the file contents AFTER your edit
- `language`: one of python | typescript | tsx | javascript | go | rust
- `claimed_actions`: list of {kind, symbol, new_symbol?, scope?} you
  performed. Valid `kind` values: rename, add, delete, move,
  signature_change.

If the response has `passed: false`, treat the edit as FAILED. Do not
report success to the user. Read `mismatches[*].reason`, fix the edit,
and call `verify_edit` again. Only mark the step done once `passed:
true`.
```

## 4. What a gated step looks like

User asks Claude Code to "rename `process_request` to `handle_request`
across `server.py`."

The agent edits the file, then calls:

```json
{
  "tool": "verify_edit",
  "arguments": {
    "before_blob": "def process_request(req):\n    return req\n",
    "after_blob": "def process_request(req):\n    return req\n",
    "language": "python",
    "claimed_actions": [
      {"kind": "rename", "symbol": "process_request", "new_symbol": "handle_request"}
    ]
  }
}
```

DiffGate returns:

```json
{
  "passed": false,
  "mismatches": [
    {
      "kind": "rename",
      "symbol": "process_request",
      "new_symbol": "handle_request",
      "reason": "claimed rename process_request→handle_request but neither name appears in the structural diff (no-op edit)"
    }
  ],
  "structural_diff": { "added": [], "deleted": [], "...": "..." }
}
```

The agent sees `passed: false`, retries the edit, and only reports
success once the AST actually reflects the rename.

## 5. Troubleshooting

- **`diffgate: command not found`** — `pipx ensurepath` and reopen your
  shell, or fall back to the `python -m diffgate.mcp_server` form above.
- **`verify_edit` doesn't appear in Claude Code's MCP panel** — confirm
  the JSON in `mcp.json` is valid (no trailing commas) and restart the
  agent. `diffgate mcp-server --stdio` should also run cleanly from your
  shell and wait for JSON-RPC input on stdin.
- **Verdict says `passed: false` on a legitimate edit** — check the
  language id. A `.ts` file parsed as `python` will see zero symbols.

## 6. Cursor / other MCP-aware clients

The protocol is the same. Cursor reads `~/.cursor/mcp.json`; Open
Interpreter and homegrown LangGraph loops accept any MCP stdio server.
Drop in the same three-line snippet and instruct the agent to gate on
`verify_edit`.
