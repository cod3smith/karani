"""The candidate as data, not prose.

A resume is a rendering of a career, and karani has been tailoring by
rewriting that rendering — which is why tailored resumes drift and why
a bullet's numbers have to be preserved by prompt instruction rather
than by construction. The profile is the source: positions, projects,
skills and education as records, merged from every source the
candidate has (resume, LinkedIn export, portfolio site).

Sources disagree, so merging is explicit: later sources fill gaps in
earlier ones and never overwrite a value that is already there. The
resume wins, because it is the document the candidate curates.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Position(BaseModel):
    company: str = ""
    title: str = ""
    started: str = ""          # free text: "2021-03", "Mar 2021"
    ended: str = ""            # "" means current
    location: str = ""
    description: str = ""
    bullets: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Identity for merging — same company and title is the same job."""
        return f"{self.company.strip().lower()}|{self.title.strip().lower()}"


class Project(BaseModel):
    name: str = ""
    description: str = ""
    url: str = ""


class Education(BaseModel):
    school: str = ""
    degree: str = ""
    field: str = ""
    ended: str = ""


class Profile(BaseModel):
    name: str = ""
    headline: str = ""
    location: str = ""
    summary: str = ""
    contacts: tuple[str, ...] = ()
    positions: tuple[Position, ...] = ()
    projects: tuple[Project, ...] = ()
    education: tuple[Education, ...] = ()
    skills: tuple[str, ...] = ()
    sources: tuple[str, ...] = Field(default=())

    def merged_with(self, other: "Profile") -> "Profile":
        """Fill gaps from `other`; never overwrite what this one has."""
        positions = {p.key: p for p in other.positions}
        for p in self.positions:            # ours wins on conflict
            existing = positions.get(p.key)
            positions[p.key] = _merge_position(p, existing) if existing else p

        seen_projects = {p.name.lower() for p in self.projects}
        seen_schools = {e.school.lower() for e in self.education}
        return Profile(
            name=self.name or other.name,
            headline=self.headline or other.headline,
            location=self.location or other.location,
            summary=self.summary or other.summary,
            contacts=tuple(dict.fromkeys(self.contacts + other.contacts)),
            positions=tuple(positions.values()),
            projects=self.projects + tuple(
                p for p in other.projects if p.name.lower() not in seen_projects),
            education=self.education + tuple(
                e for e in other.education
                if e.school.lower() not in seen_schools),
            skills=tuple(dict.fromkeys(self.skills + other.skills)),
            sources=tuple(dict.fromkeys(self.sources + other.sources)),
        )


def _merge_position(primary: Position, extra: Position) -> Position:
    return Position(
        company=primary.company or extra.company,
        title=primary.title or extra.title,
        started=primary.started or extra.started,
        ended=primary.ended or extra.ended,
        location=primary.location or extra.location,
        description=primary.description or extra.description,
        bullets=primary.bullets or extra.bullets,
    )
