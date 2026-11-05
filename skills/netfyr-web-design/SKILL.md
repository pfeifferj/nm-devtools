---
name: netfyr-web-design
description: Build or review netfyr web interfaces using the shared web components, branding and interaction rules from the design library.
---

# netfyr web design

Locate the target application and the selected `netfyr-design` checkout in the
workspace. Read their `AGENTS.md` files; ask for the design checkout if it cannot
be found. Paths below are relative to that checkout, not the installed skill.

Read `web/AGENTS.md`, then `web/README.md` for component markup and APIs and
`principles.md` for interaction rules. Inspect the relevant implementation in
`web/src/` before adding a control. Use `web/index.html` for examples and `src/`
for the brand site.

- Reuse the shared controls and scoped `.ui` styles within the application's
  existing framework. Keep routes, data access and validation in the application.
- Initialize interactive controls with `enhance(root)` and retain its cleanup
  function when replacing a view. Mount the standalone theme control explicitly
  with `mountTheme`; it is not part of `enhance`.
- Reuse the navigation, logo/subtitle, section dividers and footer for netfyr
  pages. Preserve the supplied artwork and local fonts; the component package
  does not contain those assets. Put SpecDoc branding in its deployment theme.
- Keep native form behavior, labels, focus return and browser navigation.
  Use the selected design checkout's wording and interaction contracts;
  terminal keybindings and cell geometry do not carry over to the browser.
- Keep copy brief. Report confirmed outcomes from application state, preserve
  input on failure, and keep routine interactions still. Motion must respect
  reduced-motion preferences and must not delay input or dismissal.

If the workspace selects `specs/interface-design.md`, use it as a rule-owner
map. The W4/W5/W6/W7/W9 definitions belong to `specs/wording.md`. Check both
documents' publication status before relying on them: they may be local
proposals absent from fresh clones. The design checkout's contracts remain
available without these drafts.

Preview with `npm run dev:web` from the design checkout; Web components are at
`http://localhost:4175/components/`. Check the result in the target application
as well as the gallery, including light/dark modes and a narrow viewport.

For shared control changes, run `npm run check:web` and
`npm run check:web:browser`. For design-site navigation or layout changes, build
the site and run `npm run check:pages`. Exercise the changed keyboard, focus and
failure paths; documentation-only changes need link and command checks.
