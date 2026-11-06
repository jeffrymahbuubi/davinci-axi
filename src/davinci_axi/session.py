"""A connected Resolve session: typed access to the project, timeline and version gates."""

from __future__ import annotations

from dataclasses import dataclass

from davinci_axi.errors import AxiError, api_failed, not_found
from davinci_axi.resolve_types import (
    ProjectApi,
    ProjectManagerApi,
    ResolveApp,
    TimelineApi,
)


@dataclass
class Session:
    resolve: ResolveApp
    version: tuple[int, int, int]
    product: str
    is_studio: bool

    def project_manager(self) -> ProjectManagerApi:
        manager = self.resolve.GetProjectManager()
        if manager is None:
            raise api_failed("Resolve.GetProjectManager")
        return manager

    def current_project(self) -> ProjectApi | None:
        return self.project_manager().GetCurrentProject()

    def require_project(self) -> ProjectApi:
        project = self.current_project()
        if project is None:
            raise AxiError(
                "NO_PROJECT",
                "no project is open in Resolve",
                help=[
                    "Open a project in Resolve, then retry",
                    "Run `davinci-axi projects` to see which projects exist",
                ],
            )
        return project

    def current_timeline(self) -> TimelineApi | None:
        project = self.current_project()
        return project.GetCurrentTimeline() if project is not None else None

    def find_timeline(self, name: str) -> TimelineApi | None:
        project = self.require_project()
        for index in range(1, project.GetTimelineCount() + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline is not None and timeline.GetName() == name:
                return timeline
        return None

    def require_timeline(self, name: str | None = None) -> TimelineApi:
        project = self.require_project()
        if name is None:
            timeline = project.GetCurrentTimeline()
            if timeline is None:
                raise AxiError(
                    "NO_TIMELINE",
                    f"project {project.GetName()} has no current timeline",
                    help=["Open or create a timeline in Resolve, then retry"],
                )
            return timeline
        found = self.find_timeline(name)
        if found is None:
            names = [
                t.GetName()
                for i in range(1, project.GetTimelineCount() + 1)
                if (t := project.GetTimelineByIndex(i)) is not None
            ]
            listing = ", ".join(names) if names else "(none)"
            raise not_found("timeline", name, f"Timelines in this project: {listing}")
        return found

    def require_version(self, feature: str, minimum: tuple[int, int, int]) -> None:
        if self.version >= minimum:
            return
        have = ".".join(str(n) for n in self.version)
        need = ".".join(str(n) for n in minimum)
        raise AxiError(
            "VERSION_TOO_OLD",
            f"{feature} needs DaVinci Resolve {need} or newer (running {have})",
            help=[f"Update DaVinci Resolve to {need} or newer to use {feature}"],
        )
