# Saved search filters

Named queries for the session list, Timeline, and Turns search boxes.
Copy this file:

```bash
mkdir -p ~/.anqa
cp examples/filters/filters.toml ~/.anqa/filters.toml
```

Each `[[filter]]` is a name, a scope (`catalog`, `timeline`, or
`turns`), and a query in the existing search language. `harness:{grok,claude}`
is a choice hole; `in:?` is a free-text hole. The terminal command
palette and the desktop Saved pick collect answers, then run the
expanded query.

The terminal app applies, saves, and deletes from Ctrl+P. The desktop
palette uses the Saved pick next to search, plus Save and Delete.
