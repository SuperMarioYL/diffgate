# Cursor + DiffGate

Use DiffGate as a post-edit verification gate inside Cursor's composer / agent loop.

## 1. Install

```bash
pipx install diffgate
diffgate --version    # sanity check
```

## 2. Register the MCP server

Open Cursor → **Settings → MCP → Add new MCP server**. Add this entry (or paste it
directly into `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "diffgate": {
      "command": "diffgate",
      "args": ["mcp-server", "--stdio"],
      "env": {}
    }
  }
}
```

Restart Cursor. The composer's MCP indicator should show `diffgate · 1 tool`
(the tool name is `verify_edit`).

## 3. Wire it into the agent loop

In Cursor's **Rules → Agent Rules**, add:

```
After every file edit, call the `verify_edit` MCP tool with:
  - before_blob: the file contents you read
  - after_blob:  the file contents you wrote
  - claimed_actions: the structured list of edits you just performed
If verify_edit returns passed=false, do NOT report success. Read the
`mismatches` array, fix the discrepancy, and retry the edit.
```

That's it. The next time the agent claims `"renamed foo→bar across module_x.py"`
but the actual diff is empty, the tool call returns:

```json
{
  "passed": false,
  "mismatches": ["claimed 4 renames of `foo`, observed 0 in module_x.py"],
  "structural_diff": { "renames": [], "adds": [], "deletes": [] }
}
```

…and Cursor's loop retries with the failure context, instead of returning
`success` and moving on.

## 4. One-screen example

User prompt:

> Rename `foo` to `bar` across `module_x.py`.

Without DiffGate, the agent sometimes returns:

> ✓ Renamed `foo` to `bar` in 4 locations.

…even when `module_x.py` is byte-identical. With DiffGate wired in, the agent
sees `passed: false, mismatches: ["claimed 4 renames of foo, observed 0"]`,
reads the structural diff, retries, and only then reports success.

## Troubleshooting

| Symptom                                  | Likely cause                                  | Fix                                       |
| ---------------------------------------- | --------------------------------------------- | ----------------------------------------- |
| `diffgate: command not found` in Cursor  | `pipx` binaries not on Cursor's PATH          | Use the absolute path in `command:`       |
| `verify_edit` tool missing from indicator | MCP server crashed on startup                 | Run `diffgate mcp-server --stdio` manually, read stderr |
| All claims pass even on obvious lies     | Language not in `languages` config            | Add the file's language to your config    |
| False positive on rename                  | `strict_renames: true` requires all-refs hit  | Set `strict_renames: false`               |

For a richer hook example (and the equivalent Claude Code wiring), see
[`claude_code_hook.md`](./claude_code_hook.md).
