"""`skill gen [--check]`: write or verify `skills/davinci-axi/SKILL.md` (no Resolve connection)."""

from __future__ import annotations

from pathlib import Path

from davinci_axi import registry, skill_template
from davinci_axi.commands.home import DESCRIPTION
from davinci_axi.errors import AxiError
from davinci_axi.output import collapse_home, hint
from davinci_axi.spec import GLOBAL_FLAGS, CommandSpec, Flag, Invocation, Result

SKILL_PATH = Path("skills") / "davinci-axi" / "SKILL.md"
GEN_HELP = "Run `davinci-axi skill gen` to write it"


def checkout_root() -> Path:
    """The checkout root: three levels above `src/davinci_axi/commands`, else the cwd."""
    root = Path(__file__).resolve().parents[3]
    return root if (root / "pyproject.toml").is_file() else Path.cwd()


def target_path(out: object) -> Path:
    """`--out` (a file, or an existing directory that gets `SKILL.md`), else the default path."""
    if not out:
        return checkout_root() / SKILL_PATH
    path = Path(str(out)).expanduser()
    return path / "SKILL.md" if path.is_dir() else path


def rendered() -> str:
    """The SKILL.md text for whatever the registry holds now."""
    specs = [(name, spec) for name in registry.command_names() if (spec := registry.lookup(name))]
    return skill_template.render(specs, GLOBAL_FLAGS, DESCRIPTION)


def _check(path: Path, shown: str, content: str) -> Result:
    if not path.is_file():
        raise AxiError("SKILL_MISSING", f"{shown} is missing", help=[GEN_HELP])
    current = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    if current != content:
        raise AxiError("SKILL_STALE", f"{shown} is stale", help=[GEN_HELP])
    return Result({"path": shown, "status": "current"})


def run_gen(inv: Invocation) -> Result:
    path = target_path(inv.args.get("out"))
    content = rendered()
    shown = collapse_home(str(path))
    if inv.args.get("check"):
        return _check(path, shown, content)
    encoded = content.encode("utf-8")
    status = "written"
    if path.is_file() and path.read_bytes() == encoded:
        status = "unchanged"
    else:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded)
        except OSError as exc:
            raise AxiError(
                "SKILL_WRITE_FAILED",
                f"cannot write {shown}: {exc.strerror or exc}",
                help=["Pass --out <path> to write the file somewhere else"],
            ) from None
    return Result(
        {"path": shown, "status": status},
        [hint("skill gen --check", "verify the file matches the registry")],
    )


GEN_SPEC = CommandSpec(
    name="gen",
    summary="Write skills/davinci-axi/SKILL.md from the command registry (or verify it)",
    run=run_gen,
    flags=(
        Flag("check", "bool", False, help="exit 1 if the file is missing or stale; write nothing"),
        Flag("out", "str", None, help="write to this file (or directory) instead of the default"),
    ),
    examples=(
        "davinci-axi skill gen",
        "davinci-axi skill gen --check",
        "davinci-axi skill gen --out /tmp/SKILL.md",
    ),
)

SPEC = CommandSpec(
    name="skill",
    summary="Generate the agent skill file for this CLI (no Resolve connection)",
    subcommands={"gen": GEN_SPEC},
    examples=("davinci-axi skill gen", "davinci-axi skill gen --check"),
)
