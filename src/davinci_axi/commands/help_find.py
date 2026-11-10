"""`help [word ...]`: find commands by word, or show the top-level help. No Resolve call."""

from __future__ import annotations

from davinci_axi import helptext, registry
from davinci_axi.errors import AxiError
from davinci_axi.output import apply_limit, empty_state, hint, page_meta
from davinci_axi.spec import CommandSpec, Flag, Invocation, Positional, Result

DEFAULT_MATCH_LIMIT = 20
MAX_WORDS = 4
# Lower rank sorts first: a hit in the path or summary beats a hit in a flag, note or example.
RANKS = ("path", "summary", "flag", "note", "example")
EXTRA_WORDS = tuple(
    Positional(f"word{n}", required=False, help="another word every match must contain")
    for n in range(2, MAX_WORDS + 1)
)

Field = tuple[str, str, str]


def _walk(spec: CommandSpec, path: tuple[str, ...]) -> list[tuple[tuple[str, ...], CommandSpec]]:
    found: list[tuple[tuple[str, ...], CommandSpec]] = []
    if spec.run is not None:
        found.append((path, spec))
    for name, sub in spec.subcommands.items():
        found.extend(_walk(sub, (*path, name)))
    return found


def _fields(path: tuple[str, ...], spec: CommandSpec) -> list[Field]:
    """(kind, via label, lower-cased text) for every searchable piece of one command."""
    fields: list[Field] = [
        ("path", "path", " ".join(path).lower()),
        ("summary", "summary", spec.summary.lower()),
    ]
    for flag in spec.flags:
        text = f"{flag.name} {flag.help} {' '.join(flag.choices)}".lower()
        fields.append(("flag", f"flag --{flag.name}", text))
    for pos in spec.positionals:
        fields.append(("flag", f"arg <{pos.name}>", f"{pos.name} {pos.help}".lower()))
    if spec is SPEC:  # its own examples and notes would match every query
        return fields
    fields.extend(("note", "note", note.lower()) for note in spec.notes)
    fields.extend(("example", "example", example.lower()) for example in spec.examples)
    return fields


def _best_hit(term: str, fields: list[Field]) -> tuple[int, str] | None:
    hits = [(RANKS.index(kind), via) for kind, via, text in fields if term in text]
    return min(hits, key=lambda hit: hit[0]) if hits else None


def find(word: str) -> list[dict[str, object]]:
    """Every runnable command whose text holds all terms of `word`: `{command, summary, via}`.

    Ordered by how directly the best hit names the command (path, summary, flag, note, example),
    then by command path.
    """
    terms = word.lower().split()
    if not terms:
        return []
    scored: list[tuple[int, str, dict[str, object]]] = []
    for top in registry.command_names():
        root = registry.lookup(top)
        if root is None:
            continue
        for path, spec in _walk(root, (top,)):
            fields = _fields(path, spec)
            hits = [_best_hit(term, fields) for term in terms]
            found = [hit for hit in hits if hit is not None]
            if len(found) < len(terms):
                continue
            rank, via = min(found, key=lambda hit: hit[0])
            command = " ".join(path)
            row: dict[str, object] = {"command": command, "summary": spec.summary, "via": via}
            scored.append((rank, command, row))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [row for _, _, row in scored]


def _top() -> Result:
    from davinci_axi import cli

    rows: list[tuple[str, str]] = []
    for name in registry.command_names():
        spec = registry.lookup(name)
        if spec is not None:
            rows.append((name, spec.summary))
    doc = helptext.render_top(cli.DESCRIPTION, rows, cli.TOP_EXAMPLES)
    doc["note"] = f"Run `{cli.BIN} help <word>` to find commands by word"
    return Result(doc)


def run(inv: Invocation) -> Result:
    raw_limit = inv.args.get("limit")
    limit = raw_limit if isinstance(raw_limit, int) else DEFAULT_MATCH_LIMIT
    if limit < 1:
        raise AxiError("VALIDATION_ERROR", "--limit must be 1 or more", help=["Valid: --limit 20"])
    word = " ".join(inv.positionals[p.name] for p in SPEC.positionals if p.name in inv.positionals)
    if not word.strip():
        return _top()
    rows = find(word)
    shown, total = apply_limit(rows, limit)
    if not rows:
        return Result(
            {"word": word, "matches": empty_state("commands", f"matching {word}", {}), "total": 0},
            ["Try a flag name (lufs, track) or run `davinci-axi skill gen` for the full table"],
        )
    help_lines = [hint(f"{rows[0]['command']} --help", "see its flags and examples")]
    if total > len(shown):
        help_lines.append(f"{total - len(shown)} more; raise --limit")
    return Result({"word": word, "matches": shown, **page_meta(len(shown), total)}, help_lines)


SPEC = CommandSpec(
    name="help",
    summary="Find commands by word, or show the top-level help",
    run=run,
    positionals=(
        Positional("word", required=False, help="a word to look for in commands, flags and notes"),
        *EXTRA_WORDS,
    ),
    flags=(Flag("limit", "int", DEFAULT_MATCH_LIMIT, help="maximum matches to show"),),
    examples=(
        "davinci-axi help loudness",
        "davinci-axi help title track",
        "davinci-axi help",
    ),
    reads_only=True,
    methods=(),
    notes=(
        "Matching is case-insensitive and every word must appear in the command's path, summary, "
        "flags, notes or examples; `via` names the best hit. No --fields: the three columns are "
        "fixed (the --fields/--limit guard allowlists `help`).",
    ),
)
