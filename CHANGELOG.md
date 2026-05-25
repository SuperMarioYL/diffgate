# Changelog

All notable changes to DiffGate are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Package scaffold: `pyproject.toml`, MIT `LICENSE`, `.gitignore`, lazy
  `src/diffgate/__init__.py` entry point.
- Stack pinned to Python 3.12+ with `tree-sitter`, `tree-sitter-language-pack`,
  `typer`, `mcp`, and `rich`.

## [0.1.0] — planned

First public release covering the m1 milestone:

- `diffgate verify --before X --after Y --claim ...` CLI that exits 0 on a
  structural match and 1 on mismatch, with a `rich`-rendered diff table.
- Tree-sitter parsers for Python and TypeScript.
- Claim kinds: `rename`, `add`, `delete`.
- ≥20 hand-crafted silent-lie fixtures in `tests/fixtures/silent_lie_cases.json`.

### Roadmap
- **m2_mcp_loop_gate** — `diffgate mcp-server --stdio` exposing a `verify_edit`
  tool so Claude Code / Cursor can gate the agent loop on every edit.
- **m3_replay_bench** — `diffgate bench traces.jsonl` to replay agent edit
  traces and report precision/recall against ground-truth lies.

[Unreleased]: https://github.com/supermario-leo/diffgate/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/supermario-leo/diffgate/releases/tag/v0.1.0
