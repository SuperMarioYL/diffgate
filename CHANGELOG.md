# Changelog

All notable changes to DiffGate are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0]

First real feature iteration: claims can now pin a containing **scope**, and
the CLI surface that the README has always advertised is finally wired up.

### Added
- **Scope-aware claim matching.** Every claim kind (`add`, `delete`, `rename`,
  `signature_change`, `move`) now honours the `scope` field. Claiming
  `add MyClass.helper` only passes when the method really lands inside
  `MyClass` — adding a free-standing `helper()` no longer satisfies it. An
  empty `scope` stays a wildcard, so existing name-only claims behave exactly
  as before. New mismatch reasons call out scope mismatches explicitly
  (e.g. `'helper' was added, but not in scope 'MyClass'`).
- `diffgate mcp-server --stdio` and `diffgate bench <traces.jsonl>` are now
  real CLI subcommands. They were documented in the README and the `mcp.json`
  snippet but were previously only reachable via `python -m diffgate.…`;
  copying the documented config now works as written.
- Eight new fixtures plus focused unit tests covering scope lies, scope
  truths, and the unscoped wildcard path.

### Fixed
- **Silent-lie false negatives from scope confusion.** Before this release the
  verifier matched claimed symbols by *name only*, so an agent that claimed it
  edited a method on a class but actually touched a same-named module-level
  function (or vice versa) slipped through as a pass. The structural gate now
  distinguishes the two.

## [0.1.0]

First public release covering the m1 milestone:

- `diffgate verify --before X --after Y --claim ...` CLI that exits 0 on a
  structural match and 1 on mismatch, with a `rich`-rendered diff table.
- Tree-sitter parsers for Python and TypeScript.
- Claim kinds: `rename`, `add`, `delete`.
- ≥20 hand-crafted silent-lie fixtures in `tests/fixtures/silent_lie_cases.json`.

[Unreleased]: https://github.com/supermario-leo/diffgate/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/supermario-leo/diffgate/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/supermario-leo/diffgate/releases/tag/v0.1.0
