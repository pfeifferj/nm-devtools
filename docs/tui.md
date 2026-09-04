# Driving nmtui

`bin/nm-tui-drive` runs nmtui against NetworkManager's mock service on a private
session bus, sends keystrokes over a pty, and asserts on what the daemon was
asked to activate. No root, no VM, no rollback: killing the bus is the rollback.

```
NM_SRC=~/rh-src/NetworkManager nm-tui-drive list
NM_SRC=~/rh-src/NetworkManager nm-tui-drive run
NM_SRC=~/rh-src/NetworkManager nm-tui-drive run --nmtui /path/to/nmtui
```

`NM_SRC` must point at a NetworkManager checkout with a build; `nmcli` and
`nmtui` are taken from `$NM_SRC/build/`.

## Why it never reads the screen

Finding the highlighted row in newt's output means parsing SGR attributes, which
breaks with terminal size and theme, and makes the test assert on rendering
rather than behaviour. Instead a case presses Enter at the end and the harness
asks the mock which connection became active. The UUID is the observation.

That still leaves the question of which row the cursor started on. A case does
not answer it. Each case runs twice from a fresh environment, once with a
rebuild injected partway through and once without, and both passes must activate
the same profile. Nothing depends on list ordering, terminal size, or how the
selection is drawn.

An nmtui that drops the selection when the list rebuilds activates the first
profile in the injected pass, and the two passes disagree:

```
FAIL tui-keep-selection-on-rebuild: selection moved across the rebuild:
     ['3333...'] then ['1111...']
```

## Case format

```json
{
  "description": "the selected row survives a connection list rebuild",
  "device": "eth0",
  "profiles": [{"name": "tui-aaa", "uuid": "1111..."}],
  "keys": ["down", "down"],
  "inject": {"name": "tui-zzz", "uuid": "4444..."}
}
```

- `device` is optional and defaults to `eth0`. The mock starts with no devices,
  so one is always added; without it the list has no rows.
- `keys` are sent before the injection. Valid keys: `down`, `up`, `enter`, `esc`.
  A case does not list the final `enter`; the harness sends it.
- `inject` is the profile added mid-run to force a rebuild. Any change to the
  client's connections rebuilds the "Activate a connection" list.

## Traps

- nmtui honours its `connect` argument only when `g_get_prgname()` is exactly
  `nmtui` (see `nmtui.c`). A binary copied to `nmtui-unfixed` opens the main menu
  instead and every case fails for the wrong reason, so `--nmtui` rejects a name
  that is not `nmtui`. To A/B two builds, put each in its own directory.
- `dbus-daemon --session --print-address --nofork` can block a parent that reads
  its stdout through a pipe. The address goes to a file that is polled.
- libnm will not connect before the mock claims the bus name, and nmtui exits
  silently if it starts too early. The harness polls for the name.
- newt redraws lazily, so keystrokes are paced by `SETTLE`. Reading with no delay
  returns a half-drawn screen. The harness drains the pty and discards it.
- The mock reports version 0.9.9.0, so nmcli prints a version-mismatch warning on
  every call. It is noise, not a failure.
