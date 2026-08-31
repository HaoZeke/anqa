# Key overlay examples

Copy a map to `~/.anqa/keys.toml` (or point `ANQA_KEYS` at the file).
The file is diffs only: omitted ids keep catalog defaults, including
`y` (copy the selection or the pane), `/` (search), and
`h`/`l` plus Left/Right (Timeline turns and Diff turns). Left/Right
are not remapped: they are not letters.

```bash
mkdir -p ~/.anqa
cp examples/keys/colemak.toml ~/.anqa/keys.toml
uv run anqa keys --check
```

`colemak.toml` is a home-row nav map plus leader verbs, not a full
layout emulator. `n`/`e` move the list. Space stays select. `g` stays
HUD Turns to Timeline. The recommended leader is `;`. Product default
is no leader.

A bad overlay is refused in full (`anqa keys --check` exits 1) and
the catalog defaults stay active. The TUI and HUD both apply a valid
map to footer, help, and key dispatch.
