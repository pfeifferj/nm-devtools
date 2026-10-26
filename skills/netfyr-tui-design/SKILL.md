---
name: netfyr-tui-design
description: Build or review netfyr terminal interfaces using the shared Ratatui widgets, layout rules and keyboard contracts in netfyr-design.
---

# netfyr TUI design

Locate the selected `netfyr-design` checkout from the target workspace or its
configuration, not this skill's installation directory. Ask for its location only
if it cannot be resolved. Paths below are relative to that checkout.

Read `AGENTS.md`, `tui/AGENTS.md`, `principles.md` and `tui/README.md`.
Use the workspace's `specs/interface-design.md` to find cross-project rule owners.
Load the relevant contract for the change:

- `tui/foundations.md`: cell alignment and visual separation, TUI-A1-A4/TUI-S1-S3.
- `tui/dropdown.md` and `tui/bindings.md`: choice states and input, TUI-D1-D6/TUI-K1-K5.
- `tui/wording.md`: labels, notices and the owning W/I/U rules.
- `tui/animations.md` or `tui/palettes.md`: motion or colour changes.

Import `netfyr-tui-design`; inspect `tui/src/` and its gallery before adding a
renderer. Extend the shared widget when it lacks required presentation behavior.
The host owns input, filtering, network operations, preferences and timing.

Keep the field value distinct from the keyboard candidate. Use the widget's
returned paging steps for wrapped options; physical rows are not option counts.
Measure terminal cells and keep selection markers in a stable gutter.
Retain state cues in `NO_COLOR` and with motion disabled. Optional font glyphs
keep `GlyphSet::Standard` fallbacks; installed fonts do not guarantee terminal support.
Change handlers, footer hints and Help together. Selection does not approve a
network mutation. Omit redundant narration while retaining errors and unknown outcomes.

For shared crate changes, run from the design checkout:

```sh
cargo fmt --manifest-path tui/Cargo.toml --all -- --check
cargo test --locked --manifest-path tui/Cargo.toml --all-targets
cargo clippy --locked --manifest-path tui/Cargo.toml --all-targets -- -D warnings
cargo run --locked --manifest-path tui/Cargo.toml --example gallery
```

Check buffer coordinates and actual input through correction, cancellation and
resize, including 32x12, long Unicode labels, monochrome and motion off. Run the
affected host tests when adapter behavior changes. Regenerate affected gallery
frames or recordings using `tui/README.md` when their rendered output changes.
