from __future__ import annotations

import pytest

from davinci_axi import fake
from davinci_axi.fake import WRITE_METHODS, FakeWorld, default_world


def test_call_log_records_class_and_method(fake_world: FakeWorld) -> None:
    project = fake_world.resolve.GetCurrentProject()
    assert project is not None
    project.GetName()
    project.GetTimelineByIndex(2)
    assert ("Resolve", "GetCurrentProject", ()) in fake_world.calls
    assert ("Project", "GetName", ()) in fake_world.calls
    assert ("Project", "GetTimelineByIndex", (2,)) in fake_world.calls
    assert fake_world.calls_to("Project.GetTimelineByIndex") == [(2,)]


def test_inject_overrides_a_return(fake_world: FakeWorld) -> None:
    project = fake_world.resolve.GetCurrentProject()
    assert project is not None
    assert project.GetTimelineByIndex(1) is not None
    fake_world.inject("Project.GetTimelineByIndex", None)
    assert project.GetTimelineByIndex(1) is None
    fake_world.inject("Project.GetName", False)
    assert project.GetName() is False


def test_injected_exception_is_raised(fake_world: FakeWorld) -> None:
    fake_world.inject("Resolve.GetCurrentPage", RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        fake_world.resolve.GetCurrentPage()


def test_write_calls_empty_after_reads_and_set_after_open_page(fake_world: FakeWorld) -> None:
    resolve = fake_world.resolve
    project = resolve.GetProjectManager().GetCurrentProject()
    assert project is not None
    timeline = project.GetCurrentTimeline()
    assert timeline is not None
    for kind in ("video", "audio"):
        for index in range(1, timeline.GetTrackCount(kind) + 1):
            timeline.GetItemListInTrack(kind, index)
    resolve.GetCurrentPage()
    assert fake_world.write_calls() == []
    assert fake_world.page_changes() == []
    assert resolve.OpenPage("color") is True
    assert fake_world.write_calls() == ["Resolve.OpenPage"]
    assert fake_world.page_changes() == ["color"]
    assert resolve.GetCurrentPage() == "color"


def test_write_ledger_contents() -> None:
    assert "ProjectManager.ExportProject" in WRITE_METHODS
    assert "Resolve.OpenPage" in WRITE_METHODS
    assert "Timeline.Export" in WRITE_METHODS
    assert "Project.GetName" not in WRITE_METHODS
    assert "TimelineItem.SetProperties" in WRITE_METHODS
    assert len(WRITE_METHODS) == 247


def test_every_modelled_write_method_is_in_the_ledger() -> None:
    from davinci_axi.fake.core import FakeProject, FakeProjectManager, FakeResolve
    from davinci_axi.fake.media import FakeFolder
    from davinci_axi.fake.timeline import FakeTimeline

    modelled = {
        "Resolve.OpenPage": FakeResolve.OpenPage,
        "ProjectManager.LoadProject": FakeProjectManager.LoadProject,
        "ProjectManager.CreateProject": FakeProjectManager.CreateProject,
        "ProjectManager.SaveProject": FakeProjectManager.SaveProject,
        "ProjectManager.ExportProject": FakeProjectManager.ExportProject,
        "Project.SetCurrentTimeline": FakeProject.SetCurrentTimeline,
        "Timeline.Export": FakeTimeline.Export,
        "Folder.Export": FakeFolder.Export,
    }
    assert set(modelled) <= WRITE_METHODS


def test_default_world_shape(fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None and project.name == "davinci-axi-test"
    assert [t.name for t in project.timelines] == ["Edit v1", "Edit v2"]
    edit = project.timelines[0]
    assert [len(edit.tracks[k]) for k in ("video", "audio", "subtitle")] == [2, 1, 0]
    assert sum(len(t.items) for tracks in edit.tracks.values() for t in tracks) == 6
    assert len(project.render_jobs) == 2
    assert project.render_presets and project.quick_export_presets
    assert fake_world.resolve.pm.folder.projects.keys() == {"sample-project", "davinci-axi-test"}


def test_resolve_modelling(fake_world: FakeWorld) -> None:
    resolve = fake_world.resolve
    assert resolve.GetVersion()[:3] == [21, 1, 1]
    assert resolve.IsStudio() is True
    assert resolve.GetProductName() == "DaVinci Resolve Studio"
    assert isinstance(resolve.EXPORT_OTIO, float)  # type: ignore[attr-defined]
    assert resolve.KEYFRAME_MODE_ALL != resolve.KEYFRAME_MODE_COLOR  # type: ignore[attr-defined]
    assert resolve.MARKER_BLUE  # type: ignore[attr-defined]
    pm = resolve.GetProjectManager()
    assert pm.GetCurrentDatabase() == {"DbType": "Disk", "DbName": "Local Database"}
    assert pm.LoadProject("nope") is None
    assert pm.CreateProject("davinci-axi-test") is None
    created = pm.CreateProject("fresh")
    assert created is not None and pm.GetCurrentProject() is created


def test_one_based_indices(fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    assert project.GetTimelineByIndex(0) is None
    assert project.GetTimelineByIndex(3) is None
    assert project.GetTimelineByIndex(1) is project.timelines[0]
    edit = project.timelines[0]
    assert edit.GetItemListInTrack("video", 0) == []
    assert len(edit.GetItemListInTrack("video", 1)) == 3
    assert edit.GetTrackCount("subtitle") == 0


def test_item_getters_model_the_api_shapes(fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    item = project.timelines[0].GetItemListInTrack("video", 1)[0]
    assert (
        isinstance(item.GetStart(), float) and item.GetEnd() - item.GetStart() == item.GetDuration()
    )
    assert item.GetTrackTypeAndIndex() == ["video", 1]
    assert 10 in item.GetMarkers() and item.GetFlagList() == ["Red"]
    assert item.GetMediaPoolItem() is not None
    assert item.GetLinkedItems()[0].GetType() == "Audio"
    assert item.GetTakesCount() == 0 and item.GetTakeByIndex(1) is None
    assert "Opacity" in item.GetProperties()
    media = item.GetMediaPoolItem()
    assert media is not None and media.GetClipProperty("File Path").endswith(".mov")


def test_scriptapp_follows_connection(fake_world: FakeWorld) -> None:
    assert fake.scriptapp("Resolve") is fake_world.resolve
    for connection in ("not_running", "scripting_disabled", "free", "not_installed"):
        fake_world.connection = connection  # type: ignore[assignment]
        assert fake.scriptapp("Resolve") is None


def test_set_world_none_resets_to_a_fresh_default() -> None:
    first = fake.current_world()
    fake.set_world(None)
    assert fake.current_world() is not first
    assert default_world().calls == []


def test_ids_are_unique_and_deterministic() -> None:
    world = FakeWorld()
    assert world.new_id("x") == "x-1" and world.new_id("x") == "x-2" and world.new_id("y") == "y-1"


def test_render_job_status(fake_world: FakeWorld) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    jobs = project.GetRenderJobList()
    statuses = [project.GetRenderJobStatus(j["JobId"])["JobStatus"] for j in jobs]
    assert statuses == ["Complete", "Ready"]
    assert project.GetRenderJobStatus("missing") == {}
    assert project.IsRenderingInProgress() is False
    assert project.GetCurrentRenderFormatAndCodec() == {"format": "mov", "codec": "H264"}


def test_timeline_export_writes_a_file(fake_world: FakeWorld, tmp_path: object) -> None:
    project = fake_world.resolve.pm.current_project
    assert project is not None
    out = f"{tmp_path}/edit.otio"
    assert project.timelines[0].Export(out, fake_world.resolve.EXPORT_OTIO) is True  # type: ignore[attr-defined]
    with open(out, encoding="utf-8") as handle:
        assert "Edit v1" in handle.read()
    assert fake_world.write_calls() == ["Timeline.Export"]
