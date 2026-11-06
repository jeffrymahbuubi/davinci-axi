from __future__ import annotations

from collections.abc import Callable

import pytest

from davinci_axi import flags
from davinci_axi.errors import AxiError
from davinci_axi.fake import FakeWorld
from davinci_axi.spec import CommandSpec, Flag, Positional

SPEC = CommandSpec(
    name="items",
    summary="List items",
    positionals=(Positional("id", required=False, help="item id"),),
    flags=(
        Flag("timeline", help="timeline name"),
        Flag("track-type", choices=("video", "audio", "subtitle")),
        Flag("limit", "int", 100),
        Flag("scale", "float", 1.0),
        Flag("fields", "list"),
        Flag("full", "bool"),
        Flag("order", renamed_from="sort"),
    ),
)


def test_value_forms_and_defaults() -> None:
    args, positionals = flags.parse(SPEC, ["--timeline", "Edit v1", "--limit=5", "--no-full"])
    assert args["timeline"] == "Edit v1"
    assert args["limit"] == 5
    assert args["full"] is False
    assert args["scale"] == 1.0
    assert args["fields"] is None
    assert positionals == {}


def test_bool_flag_and_hyphen_key() -> None:
    args, _ = flags.parse(SPEC, ["--full", "--track-type", "audio"])
    assert args["full"] is True
    assert args["track_type"] == "audio"


def test_list_flag_is_comma_split() -> None:
    args, _ = flags.parse(SPEC, ["--fields", "name, id,start"])
    assert args["fields"] == ["name", "id", "start"]


def test_positional_and_double_dash() -> None:
    _, positionals = flags.parse(SPEC, ["V1:2"])
    assert positionals == {"id": "V1:2"}
    _, positionals = flags.parse(SPEC, ["--", "--odd-id"])
    assert positionals == {"id": "--odd-id"}


def test_unknown_flag_names_valid_flags() -> None:
    with pytest.raises(AxiError) as caught:
        flags.parse(SPEC, ["--stat"])
    err = caught.value
    assert err.code == "VALIDATION_ERROR"
    assert err.exit_code == 2
    assert err.message == "unknown flag --stat for `items`"
    assert err.help[0].startswith("Valid flags for `items`: --timeline, --track-type")
    assert "--json" in err.help[0]
    assert "davinci-axi items --help" in err.help[1]


def test_renamed_flag_gets_targeted_hint() -> None:
    with pytest.raises(AxiError) as caught:
        flags.parse(SPEC, ["--sort", "x"])
    assert "renamed to --order" in caught.value.message


def test_bad_values() -> None:
    with pytest.raises(AxiError, match="expects int"):
        flags.parse(SPEC, ["--limit", "many"])
    with pytest.raises(AxiError, match="invalid value"):
        flags.parse(SPEC, ["--track-type", "midi"])
    with pytest.raises(AxiError, match="needs a value"):
        flags.parse(SPEC, ["--timeline"])
    with pytest.raises(AxiError, match="unknown flag --no-limit"):
        flags.parse(SPEC, ["--no-limit"])


def test_required_flag_and_positionals() -> None:
    strict = CommandSpec(
        name="item",
        summary="One item",
        positionals=(Positional("id"),),
        flags=(Flag("to", required=True),),
    )
    with pytest.raises(AxiError, match="missing required flag --to"):
        flags.parse(strict, ["x"])
    with pytest.raises(AxiError, match="missing <id>"):
        flags.parse(strict, ["--to", "y"])
    with pytest.raises(AxiError, match="unexpected argument"):
        flags.parse(strict, ["--to", "y", "a", "b"])


def test_no_positionals_accepted_when_none_declared() -> None:
    bare = CommandSpec(name="home", summary="Home")
    with pytest.raises(AxiError, match="takes no positional"):
        flags.parse(bare, ["stray"])


@pytest.mark.parametrize("argv", [["--json", "home"], ["home", "--json"]])
def test_global_flags_on_both_sides_of_the_command(
    run_cli: Callable[[list[str]], tuple[int, str, str]],
    fake_world: FakeWorld,
    argv: list[str],
) -> None:
    code, out, _ = run_cli(argv)
    assert code == 0
    assert out.startswith("{") and '"resolve": "reachable"' in out


def test_unknown_flag_makes_no_resolve_call(
    run_cli: Callable[[list[str]], tuple[int, str, str]], fake_world: FakeWorld
) -> None:
    code, out, _ = run_cli(["home", "--stat"])
    assert code == 2
    assert "Valid flags for `home`" in out
    assert fake_world.calls == []
