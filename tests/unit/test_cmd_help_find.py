from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

import pytest

from davinci_axi import registry
from davinci_axi.commands import help_find
from davinci_axi.fake import FakeWorld
from davinci_axi.spec import CommandSpec, Flag, Positional, Result

Run = Callable[[list[str]], tuple[int, str, str]]


@pytest.fixture(autouse=True)
def _registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lead's registry splice, applied locally."""
    monkeypatch.setitem(registry.COMMANDS, "help", "davinci_axi.commands.help_find:SPEC")


def _rows(argv: list[str], run_cli: Run) -> dict[str, dict[str, object]]:
    code, out, _ = run_cli([*argv, "--json"])
    assert code == 0
    data = json.loads(out)
    return {row["command"]: row for row in data["matches"]}


def _leaf(**kwargs: object) -> CommandSpec:
    return CommandSpec(
        name="x",
        summary="plain",
        run=lambda inv: Result({}),
        **kwargs,  # type: ignore[arg-type]
    )


def test_loudness_finds_the_audio_family(run_cli: Run, fake_world: FakeWorld) -> None:
    rows = _rows(["help", "loudness"], run_cli)
    for command in ("audio loudness", "render verify", "timeline check", "render start"):
        assert command in rows, command
    assert "media analyze" in rows
    assert rows["audio loudness"]["via"] in ("path", "summary")
    assert str(rows["render verify"]["via"]).startswith("flag --")
    assert fake_world.calls == []


def test_flag_name_matches_by_flag(run_cli: Run, fake_world: FakeWorld) -> None:
    rows = _rows(["help", "lufs"], run_cli)
    assert rows["render start"]["via"] == "flag --lufs"
    assert rows["render verify"]["via"] == "summary"
    assert "timeline check" in rows
    assert fake_world.calls == []


def test_keys_are_stable_in_json_and_toon(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["help", "lufs", "--json"])
    assert code == 0
    assert set(json.loads(out)) == {"word", "matches", "total", "help"}
    toon = run_cli(["help", "lufs"])[1]
    assert "matches[" in toon and "{command,summary,via}" in toon
    assert "\nhelp[" in toon


def test_multi_word_needs_every_word(run_cli: Run, fake_world: FakeWorld) -> None:
    rows = _rows(["help", "title", "track"], run_cli)
    assert "timeline title add" in rows
    assert _rows(["help", "title track"], run_cli).keys() == rows.keys()
    assert "audio loudness" not in rows


def test_title_finds_timeline_and_fusion_title(run_cli: Run, fake_world: FakeWorld) -> None:
    rows = _rows(["help", "title"], run_cli)
    assert "timeline title add" in rows
    assert "fusion insert title" in rows


def test_matching_is_case_insensitive(run_cli: Run, fake_world: FakeWorld) -> None:
    assert _rows(["help", "LUFS"], run_cli).keys() == _rows(["help", "lufs"], run_cli).keys()


def test_nothing_found_exits_zero_with_hint(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, err = run_cli(["help", "zzz"])
    assert (code, err) == (0, "")
    assert "matches: 0 commands matching zzz" in out
    assert "skill gen" in out
    assert fake_world.calls == []


def test_limit_reports_the_rest(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["help", "loudness", "--limit", "1", "--json"])
    assert code == 0
    data = json.loads(out)
    assert len(data["matches"]) == 1 and data["total"] > 1
    assert any("more; raise --limit" in line for line in data["help"])


def test_limit_zero_is_a_usage_error(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["help", "loudness", "--limit", "0"])
    assert code == 2
    assert "--limit" in out
    assert run_cli(["help", "--limit", "0"])[0] == 2


def test_no_word_prints_the_top_help_and_a_note(run_cli: Run, fake_world: FakeWorld) -> None:
    code, out, _ = run_cli(["help"])
    assert code == 0
    assert f"commands[{len(registry.command_names())}]{{command,summary}}" in out
    assert "davinci-axi help <word>" in out
    assert fake_world.calls == []


def test_best_hit_ranks_path_summary_flag_note_example() -> None:
    leaf = _leaf(
        flags=(Flag("zing", "bool", False, help="makes a zing"),),
        notes=("zing in a note",),
        examples=("davinci-axi x zing",),
    )
    fields = help_find._fields(("a", "x"), leaf)
    assert help_find._best_hit("zing", fields) == (2, "flag --zing")
    assert help_find._best_hit("plain", fields) == (1, "summary")
    assert help_find._best_hit("a x", fields) == (0, "path")
    only_note = replace(leaf, flags=(), examples=())
    assert help_find._best_hit("zing", help_find._fields(("a", "x"), only_note)) == (3, "note")
    only_example = replace(leaf, flags=(), notes=())
    assert help_find._best_hit("zing", help_find._fields(("a", "x"), only_example)) == (
        4,
        "example",
    )


def test_positional_help_is_searched() -> None:
    leaf = _leaf(positionals=(Positional("job", help="the render job id"),))
    assert help_find._best_hit("render", help_find._fields(("x",), leaf)) == (2, "arg <job>")


def test_spec_lists_one_limit_and_no_fields() -> None:
    assert [f.name for f in help_find.SPEC.flags] == ["limit"]
    assert help_find.SPEC.methods == ()
