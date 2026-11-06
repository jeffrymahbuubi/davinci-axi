# davinci-axi

A command line tool that reports the live state of DaVinci Resolve Studio in a compact form an AI agent can read. It follows the AXI conventions: TOON output by default, JSON on request, a `help[]` list of suggested next commands on every result, and exit codes that mean something. Reads are always safe. Every command that changes Resolve is a dry run until you add `--force`, and before it writes it saves a snapshot of the project to disk that `snapshot restore` can bring back as a new project, because the Resolve scripting API has no undo.

## Prerequisites

- **macOS.** v0.1 is tested on macOS only. Windows and Linux are untested; the licence and scripting paths there are best guesses, and `DAVINCI_AXI_LICENSE_DIR` and `RESOLVE_SCRIPT_API` override them.
- DaVinci Resolve **Studio** 21.1 or newer, running. The free edition does not allow external scripting.
- In Resolve, set **Preferences > System > General > External scripting using** to **Local**, then restart Resolve.
- Python 3.10 or newer and [uv](https://docs.astral.sh/uv/). The tool has no runtime dependencies.

If Resolve is not ready, the tool says why and how to fix it. The connection failures are `RESOLVE_NOT_INSTALLED`, `RESOLVE_NOT_RUNNING`, `SCRIPTING_DISABLED`, `FREE_EDITION` and `RESOLVE_TIMEOUT`.

## Install

```bash
uv tool install git+https://github.com/jeffrymahbuubi/davinci-axi
```

To run it once without installing:

```bash
uvx --from git+https://github.com/jeffrymahbuubi/davinci-axi davinci-axi
```

The `otio` extra pulls in OpenTimelineIO for features that need it. Nothing in this version does.

## Commands

Run `davinci-axi` with no arguments for the home view: the Resolve version and edition, the current project and timelines, the render queue counts, and the commands to try next.

| Command | What it does | Example |
| --- | --- | --- |
| `davinci-axi home` | Show live Resolve state (also the default with no arguments) | `davinci-axi` |
| `davinci-axi projects` | List the projects in the current project-manager folder | `davinci-axi projects --fields name,created` |
| `davinci-axi timelines` | List the timelines of the current project | `davinci-axi timelines` |
| `davinci-axi timeline` | Create, duplicate, rename, delete or open a timeline; show one timeline in detail (its clip in the media pool, the current video item); set settings and timecode; edit tracks and markers (including custom marker data); export to or import from OTIO, FCPXML, DRT, EDL or AAF; append or build from media pool clips, make compound clips, insert generators, OFX generators and titles, import an AAF into the timeline; set mark in/out and output blanking, convert to stereo, analyze Dolby Vision, list the selection (dry run unless `--force`; export is disk-only and never overwrites without `--overwrite`; mark in/out takes no snapshot) | `davinci-axi timeline append A003_dusk --track 2 --force` |
| `davinci-axi items` | List the items of a timeline across tracks | `davinci-axi items --track-type audio` |
| `davinci-axi item` | Show one timeline item in detail, or change it: set properties, rename, enable/disable, colour, flags, markers (including custom marker data), speed, fades, delete; link and unlink items, add transitions, update sidecars, manage takes, set output blanking, read stereo convergence and floating windows (dry run unless `--force`) | `davinci-axi item link V1:1,A1:1 --force` |
| `davinci-axi media` | List and show media pool clips, or change them: import, rename, move, relink, unlink, delete, metadata (and metadata export), colour, flags, markers (including custom marker data), proxies, folders (and folder import/export); refresh, selection, third-party metadata, clip properties, replace, full-res link, growing-file monitor, mark in/out, stereo clips (dry run unless `--force`) | `davinci-axi media rename A001_beach --to beach_wide --force` |
| `davinci-axi render` | List presets, formats and the queue; add, start, stop or delete render jobs; quick export; export the current frame; set format, codec and mode; save, update, delete, load, import or export render presets (dry run unless `--force`; jobs report progress on stderr) | `davinci-axi render start job-2 --force` |
| `davinci-axi fusion` | Read the Fusion comps of a timeline item (comps, tools, inputs, graph, registry, undo stack), or change them: add, set, connect, rename, move, bypass or delete tools, keyframes, undo, comp add/import/export/load/rename/delete, Fusion clips, inserts and the output cache (dry run unless `--force`) | `davinci-axi fusion tool add Blur --item V1:2 --force` |
| `davinci-axi color` | Read and change grades: node graphs, nodes, LUTs, DRX, CDL, grade copy, versions, colour groups, stills and albums, cache, thumbnails and LUT export (dry run unless `--force`) | `davinci-axi color graph --item V1:2` |
| `davinci-axi audio` | Fairlight and audio: normalize, align, voice isolation, audio mapping, Fairlight presets, insert audio, sync, transcribe, classify and speech (dry run unless `--force`; jobs report progress on stderr) | `davinci-axi audio normalize A1:1 --loudness -24 --force` |
| `davinci-axi ai` | Studio AI analysis: `ai status` shows the job statuses; slate, smart reframe, scene cuts and subtitles are polled jobs (progress on stderr, `--no-wait`); intellisearch, magic mask, stabilize, deblur and multicam create/smart-switch/flatten run once. A False from Resolve is `NOT_AVAILABLE` (needs Studio with AI Extras); `flatten` and `intellisearch reset` are irreversible (dry run unless `--force`) | `davinci-axi ai scene-cuts --force` |
| `davinci-axi storage` | Browse Media Storage and add media to the pool; clip and timeline mattes; the media clone tool (`clone start` polls, takes no snapshot because it writes outside the project) (dry run unless `--force`) | `davinci-axi storage volumes` |
| `davinci-axi presets` | List, load, save, update, delete, import or export layout, keyboard, user-preference and burn-in presets; `update` and `delete` export a copy first (dry run unless `--force`) | `davinci-axi presets layout list` |
| `davinci-axi dctl` | Validate a DCTL file, or write an encrypted copy | `davinci-axi dctl validate ~/LUT/grade.dctl` |
| `davinci-axi project snapshot` | Export the current project to a .drp plus a JSON state dump on disk | `davinci-axi project snapshot --reason before-regrade` |
| `davinci-axi project` | Create, open, save, rename, close, delete, import, export or archive projects; manage project folders, settings and settings presets; `restore`, `db` and `cloud` (dry run unless `--force`) | `davinci-axi project rename --to new-name --force` |
| `davinci-axi snapshot` | List, show, restore or delete project snapshots; `restore` imports a snapshot as a new project and never overwrites (dry run unless `--force`) | `davinci-axi snapshot restore 20261027-013000-sample-project --force` |
| `davinci-axi page` | Show the open Resolve page or switch to another (`media cut edit fusion color fairlight deliver`); UI state only, no snapshot (dry run unless `--force`) | `davinci-axi page set color --force` |
| `davinci-axi app` | Resolve-level status (`app show`: version string, page, current project/timeline, media pool id, gallery album) and switches: high priority on/off, keyframe mode show/set (`all color sizing`), disable background tasks until Resolve restarts; no snapshot (dry run unless `--force`) | `davinci-axi app keyframe-mode set color --force` |
| `davinci-axi skill gen` | Write skills/davinci-axi/SKILL.md from the command registry (or verify it) | `davinci-axi skill gen --check` |
| `davinci-axi hook install` | Add or remove a SessionStart hook that runs `davinci-axi` when an agent session begins | `davinci-axi hook install --agent claude-code --scope project` |

Run `davinci-axi <command> --help` for the flags and examples of one command. Flags are checked before the tool connects to Resolve, so a usage error never touches the application.

Global flags:

- `--json`: emit JSON instead of TOON.
- `--debug`: print tracebacks to stderr.
- `--timeout <seconds>`: how long to wait for Resolve (default 3, env `DAVINCI_AXI_TIMEOUT`).

List commands also take `--fields a,b,c` and `--limit N` (default 100). `items` takes `--timeline`, `--track-type video|audio|subtitle` and `--track`. `item` takes `--timeline` and `--full`.

`project snapshot` takes `--out <dir>`, `--no-stills-luts` and `--reason <text>`. `hook install` takes `--agent claude-code|codex` (default `claude-code`), `--scope project|user` (default `project`), `--dir <path>` (project scope only, default the current directory), `--remove` and `--check`.

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
- The tool never opens, closes, saves or deletes projects to answer a question; it reads the current state.
- Tests never reach a real Resolve unless you pass `--live` (see below).

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
