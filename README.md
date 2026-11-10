# davinci-axi

A command line tool that reports the live state of DaVinci Resolve Studio in a compact form an AI agent can read. It follows the AXI conventions: TOON output by default, JSON on request, a `help[]` list of suggested next commands on every result, and exit codes that mean something. Reads are always safe. Every command that changes Resolve is a dry run until you add `--force`, and before it writes it saves a snapshot of the project to disk that `snapshot restore` can bring back as a new project, because the Resolve scripting API has no undo.

## Prerequisites

- **macOS.** v0.1 is tested on macOS only. Windows and Linux are untested; the licence and scripting paths there are best guesses, and `DAVINCI_AXI_LICENSE_DIR` and `RESOLVE_SCRIPT_API` override them.
- DaVinci Resolve **Studio** 21.1 or newer, running. The free edition does not allow external scripting.
- In Resolve, set **Preferences > System > General > External scripting using** to **Local**, then restart Resolve.
- Python 3.10 or newer and [uv](https://docs.astral.sh/uv/). The tool has no runtime dependencies.
- Optional: `ffmpeg` and `ffprobe` on `PATH` (or `FFMPEG`/`FFPROBE`) for `media analyze`, `audio loudness`, `audio gaps`, `timeline check --audio`, `render verify` (and `render start --verify`), `render master`, `render sheet` and the contact-sheet fallback.

If Resolve is not ready, the tool says why and how to fix it. The connection failures are `RESOLVE_NOT_INSTALLED`, `RESOLVE_NOT_RUNNING`, `SCRIPTING_DISABLED`, `FREE_EDITION` and `RESOLVE_TIMEOUT`. Just after Resolve launches, `RESOLVE_STARTING` means it is still loading: the tool retries within `--timeout`, so retry or raise it.

## Install

```bash
uv tool install git+https://github.com/jeffrymahbuubi/davinci-axi
```

`davinci-axi --version` prints `0.2.0+<commit>` for an install from a git URL. An install from a local
checkout adds a note that the commit is the checkout's HEAD, so reinstall after new commits.

To run it once without installing:

```bash
uvx --from git+https://github.com/jeffrymahbuubi/davinci-axi davinci-axi
```

The `otio` extra pulls in OpenTimelineIO for features that need it. Nothing in this version does. The `sheet` extra installs Pillow for the contact sheet of `timeline stills --sheet` and `media stills --sheet` (`uv tool install --with pillow ...`); without it the command falls back to ffmpeg when present.

## Commands

Run `davinci-axi` with no arguments for the home view: the Resolve version and edition, the current project and timelines, the render queue counts, and the commands to try next.

| Group | What it is for | Key commands |
| --- | --- | --- |
| `home` | Live Resolve state: version, project, timelines, queue, next commands (default with no arguments) | `davinci-axi` |
| `projects`, `timelines`, `items` | Lists: projects in the current folder, timelines of the project, items across tracks | `items --fields name,fade_in,volume`, `items --track-type audio`, `projects --fields name,created` |
| `project` | Project lifecycle, folders, settings, databases; `project snapshot` writes a restorable .drp plus state dump | `project snapshot --reason before-regrade`, `project settings show` |
| `timeline` | Create, open, inspect and lint timelines; tracks, markers, in/out marks, settings; import and export; append, build, insert, compound; batch transitions and fades; title cards and music beds; storyboard stills; diff two timelines | `timeline check --target 2m-5m`, `timeline append A003_dusk --track 2 --force`, `timeline title add 'Dusk' --at 01:00:32:00 --force` |
| `item` | One timeline item: show it, set properties (one item or a row file), speed, fades, markers, transitions, takes; delete lists | `item V1:2`, `item set --edl props.csv --force`, `item delete V1:2,V1:5 --ripple --force` |
| `media` | Media pool: list, show, import, folders, properties, relink, markers, marks, stills; `analyze` measures source files with ffmpeg | `media --fields name,resolution,frames`, `media analyze --folder Master --rgb`, `media property set --used 'Super Scale=1' --force` |
| `render` | Presets, formats and the queue; add, start, stop, delete jobs; `deliver` to a size cap; `verify`, `master` and `sheet` a rendered file with ffmpeg; `frame` stills; project render settings | `render deliver --target-dir ~/out --max-mb 50 --mkdir --force`, `render verify ~/out/edit.mp4 --max-mb 50 --lufs -14`, `render master ~/out/edit.mp4 --lufs -14 --force` |
| `fusion` | Fusion comps of an item: tools, wiring, keyframes, undo; insert titles and generators | `fusion tool add Blur --item V1:2 --force`, `fusion insert title 'Text+' --force` |
| `color` | Grades: node graphs, LUTs, DRX, CDL (one item or a row file), versions, groups, gallery stills | `color cdl set V1:1 'NodeIndex=1,Slope=1 1 1,...' --force`, `color cdl set --edl grades.csv --force`, `color still grab --force` |
| `audio` | Fairlight: normalize, gain, voice isolation, mappings, transcribe, speech; `loudness` and `gaps` measure the timeline's sound with ffmpeg | `audio normalize A1:1 --loudness -24 --force`, `audio loudness`, `audio gaps --out gaps.csv` |
| `ai` | Studio AI jobs with an Extras preflight: scene cuts, subtitles, reframe, magic mask, multicam | `ai extras`, `ai scene-cuts --force` |
| `storage` | Media Storage browsing, adding media, mattes, the clone tool | `storage volumes`, `storage clone start /src /dst --force` |
| `presets` | Layout, keyboard, user-preference and burn-in presets | `presets layout list` |
| `dctl` | Validate or encrypt DCTL files | `dctl validate ~/LUT/grade.dctl` |
| `snapshot` | List, show, diff, restore, delete and prune the safety snapshots | `snapshot diff <id>`, `snapshot restore <id> --force`, `snapshot prune --keep 5` |
| `page`, `app` | The open page and Resolve-level switches (UI state, no snapshot) | `page set color --force`, `app show` |
| `skill gen`, `hook install` | Generate the agent skill file; add the SessionStart hook | `skill gen --check`, `hook install --agent claude-code` |

Every command that changes Resolve is a dry run until `--force`. Run `davinci-axi <group> --help` for the subcommands and `davinci-axi <group> <verb> --help` for flags, notes and examples; the notes hold the Resolve quirks measured on 21.1.1. `davinci-axi help <word>` finds a command by word. Flags are checked before the tool connects to Resolve, so a usage error never touches the application.

Global flags:

- `--json`: emit JSON instead of TOON.
- `--debug`: print tracebacks to stderr.
- `--timeout <seconds>`: how long to wait for Resolve (default 3, env `DAVINCI_AXI_TIMEOUT`).

List commands also take `--fields a,b,c` and `--limit N` (default 100); a list cut by `--limit` carries `truncated: true`. A bool flag that defaults to on shows as `--x/--no-x` in `--help`. Renamed flags keep working under their old names with a note: `media folder add --in` is now `--parent`, and `color still grab --all` is now `--source`.

### Output and exit codes

Output is TOON: `key: value` lines and `name[N]{fields}:` tables. Errors print `error`, `code` and `help[]` on stdout. Exit codes are `0` for success, `1` for a runtime error and `2` for a usage error.

## Agent skill and session hook

`skills/davinci-axi/SKILL.md` is generated from the command registry. Regenerate it with `davinci-axi skill gen`; `davinci-axi skill gen --check` exits 1 if the file is missing or stale.

`davinci-axi hook install` is opt-in. It adds a `SessionStart` entry to `.claude/settings.json` (Claude Code) or `.codex/hooks.json` (Codex), marked with `# davinci-axi` so a second run changes nothing and a stale path is repaired in place. Codex also needs `[features].hooks = true` in its config. `--remove` deletes the entry and `--check` exits 1 if it is missing or stale.

## Snapshots

`project snapshot` writes a `.drp` export and a JSON state dump into `~/.davinci-axi/snapshots`. Set `DAVINCI_AXI_HOME` to move the state directory, or pass `--out <dir>` for a single run. To restore, import the `.drp` from Resolve's Project Manager.

## Safety

- Commands that change Resolve are dry runs: without `--force` they print the planned `changes[]` with `applied: false` and write nothing.
- With `--force` a command saves the project, writes a snapshot to `~/.davinci-axi/snapshots/<id>`, makes the change, then reads it back. `VERIFY_FAILED` means the read-back differed; the error names the snapshot.
- `snapshot restore <id>` imports a snapshot as a new project and never overwrites an existing one.
- Irreversible deletes are marked `irreversible: true` with a `warning`, in the dry run and in the result.
- `--no-snapshot` is refused unless `DAVINCI_AXI_ALLOW_UNSNAPSHOTTED=1` is set.
- Batches under one snapshot: take one with `davinci-axi project snapshot --reason before-batch`, then run each change with `DAVINCI_AXI_ALLOW_UNSNAPSHOTTED=1 davinci-axi ... --force --no-snapshot`. `snapshot restore <id>` brings the whole batch back. For many items at once, prefer the row files (`item set --edl`, `color cdl set --edl`, `timeline marker add --edl`) and the list verbs (`media property set --used`) and the batch verbs (`timeline fades`, `timeline transitions`, `item delete <list>`), which take one snapshot each.
- The tool never opens, closes, saves or deletes projects to answer a question; it reads the current state.
- Tests never reach a real Resolve unless you pass `--live` (see below).

### Known API limits (Resolve 21.1.1)

The scripting API cannot: add OFX to a node or add a node (`color grade init` refuses unless `--try`); read a CDL back; create the first node from a DRX; read a magic mask back; measure loudness; read a render bit rate back; trim an item; set a still's duration on append (`--duration` on append covers it); pick the track for a title insert (inserts land on V1; `timeline title add` works around it with a compound clip); render effects added through the API to an item's Fusion comp (standalone Text+ titles do render). Each command's `--help` notes say which of these it hits.

### Long-running jobs

Render jobs, quick exports and analysis jobs wait until they finish. Progress lines go to stderr only when the status or percentage changes; the final result goes to stdout. `--no-wait` returns right after the start call, `--poll-timeout <seconds>` (default 600) bounds the wait (`POLL_TIMEOUT`) and `--poll-interval <seconds>` sets the read interval. A failed or cancelled job exits 1 with `JOB_FAILED`.

### Fusion

Fusion commands target one comp of a timeline item (`--item`, `--comp`) on the current timeline; a non-current `--timeline` is refused rather than switched. Fusion changes run inside one undo group named `davinci-axi: <command>`, so Fusion's own Undo reverts them in one step, and first export the comp to `~/.davinci-axi/snapshots/fusion/<ts>-<item>-<comp>.comp`. Each Fusion call has a timeout (`DAVINCI_AXI_FUSION_TIMEOUT`, default 10 seconds).

## Fake mode

Set `DAVINCI_AXI_FAKE=1` to run against a built-in fake Resolve with sample projects, timelines and items:

```bash
DAVINCI_AXI_FAKE=1 davinci-axi
DAVINCI_AXI_FAKE=1 davinci-axi items --json
```

The test suite and CI run only in fake mode.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run davinci-axi skill gen --check
```

CI runs the same checks on Python 3.10 and 3.13, plus `npx -y axi-axi validate "uv run davinci-axi" --dir . --strict` in fake mode.

### Live tests

Tests marked `live` are collected but skipped by default. To run them, open Resolve Studio with a project named exactly `davinci-axi-test` as the current project, then:

```bash
uv run pytest --live -m live
```

The `live_session` fixture fails the test (it does not skip) if the current project has any other name, so a mistaken run cannot touch your own projects. The live tests only read, and the snapshot test writes into a pytest temporary directory.

## License

MIT
