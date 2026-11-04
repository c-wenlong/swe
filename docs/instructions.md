# Shared instruction chapters

`swe instructions` builds the shared instruction file from small Markdown
chapters. The chapters and their order live under `~/.swe/AGENTS_MD/`.
Harness links read `~/.swe/AGENTS.md`, or `~/.swe/AGENTS.<harness>.md`
when `targets.json` gives that harness its own chapter set.

## Source layout

Create `~/.swe/AGENTS_MD/index.txt` with one relative `.md` path per line:

```text
# Order matters: later chapters follow earlier ones.
01-general.md
02-shipping.md

harnesses/claude.md
```

Write the corresponding chapter files under `~/.swe/AGENTS_MD/`.
Blank lines and lines starting with `#` are ignored. The index explicitly
chooses the chapters and their order; files outside it do not enter the output.
Paths must stay inside the chapter directory. Missing chapters, invalid
paths and malformed sources fail the build and preserve the existing output.

A chapter can be switched off without losing its slot: comment its line out
(`# harnesses/codex.md`) and the file stays on disk, out of every output,
until it is listed again. A comment that names no real chapter file is just
a comment.

## Per-harness chapters

`~/.swe/AGENTS_MD/targets.json` (optional) limits which harnesses a chapter
reaches:

```json
{
  "version": 1,
  "chapters": {
    "harnesses/claude.md": {"only": ["claude"]},
    "machine/packages.md": {"except": ["codex"]}
  }
}
```

Each chapter takes exactly one of `only` or `except`, a list of harness
registry names (the names `swe init --check --json` and `swe list --json`
print). `only: []` means no harness gets the chapter. Rules for chapters
index.txt does not list — including off chapters — are ignored.

A chapter with no rule reaches every harness. When a named harness ends up
with a different chapter set than the default, `build` also writes
`~/.swe/AGENTS.<name>.md` (the generated marker, then a
`<!-- Target: <name> -->` line) and repoints swe's own instruction symlinks
to it — every link that already leads into a swe output, including the
extra account homes `accounts.sync.instructions` covers. Links pointing
anywhere else and real files are left alone, and a retired
`AGENTS.<name>.md` is removed (still carrying the generated marker) after
its links are repointed. `swe init` links to the per-harness file as soon
as it exists, so a plain init also repairs links a build made before the
file was built. A missing `targets.json` means no rules and byte-identical
output; an invalid one fails the build with the file named, leaving every
output untouched.

The compiler produces the entire `~/.swe/AGENTS.md`, including all
chapters in index order. Before adopting chapters, put all rules you want
to retain into the source chapters. Edit those chapters afterward: the next
build replaces edits made directly to the generated file.

On the Mac, the AGENTS.md page's Edit tab is a chapter editor once
`index.txt` exists. Its rail is the AGENTS_MD folder as an indented
outline — folders group chapters, files not in the index read muted —
with a `+` menu to create a chapter or folder (the sheet validates the
path and can include the chapter in AGENTS.md right away). Select a
chapter to edit it in the Raw or Rich editor; every save runs
`swe instructions build`. A View AGENTS.md button opens the compiled file
in a centre popup (Rich or Raw, build state, Rebuild); its dot follows
builds made in a terminal. Before `index.txt` exists, the Edit tab edits
the flat `~/.swe/AGENTS.md` and offers Convert to chapters, which runs
`swe instructions init`. Reordering and unlisting chapters move to the
Compile tab, so they are temporarily unavailable in the app.

The web app has no chapter editor: its AGENTS.md page shows the generated
file read-only, and the iPhone app has no AGENTS.md page. Without the Mac
app, edit the source chapters in a text editor. A running watcher also
replaces direct output edits with the compiled source.

## Commands

```bash
swe instructions build
swe instructions check
swe instructions watch
```

`build` validates the source first, then atomically replaces every expected
output. It compares SHA256 hashes and reports `Unchanged` without rewriting
matching output, then repoints links and removes retired per-harness files.
Running [swe init](../README.md) remains the way to create links that do
not exist yet.

`check` validates the source and compares every expected output without
writing. It exits zero when all outputs match and one when one is missing,
stale, a retired generated `AGENTS.<name>.md` is left over, or the source
is invalid. Use it in a hub's validation checks.

`watch` builds immediately and rebuilds after source edits. It reports
invalid source without replacing the previous valid output, and continues
watching so a corrected source can build. Press Ctrl-C to stop.
Failed builds also retry every five seconds without a file edit, so a
temporary write failure can recover. Repeated identical errors print once
until a build succeeds.

The watcher uses the Python standard library, with no extra package needed.
It polls the index, `targets.json`, listed chapter files and generated
output; unlisted backups, images and repository files are not read. Instruction chapters
require macOS or Linux for file locking and safe no-follow file opens.
Keep it running in a terminal while editing chapters. It does not install
a background service or change harness configuration.

## When harnesses read changes

The generated file stays at the existing shared path, so SWE's clients
and every linked harness see the updated file on disk. A harness may load
instructions only when a chat starts. Start a new chat if the current chat
keeps using the old instructions.
