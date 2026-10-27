"""LinkedIn's official data export — the supported path.

Scraping LinkedIn violates their terms and fights bot detection for
worse data. The export ("Get a copy of your data") is a ZIP of CSVs
the member downloads themselves: Positions, Education, Skills,
Projects, Profile. Point karani at the ZIP or the unpacked folder.

Column names drift between exports, so every lookup goes through a
candidate list and a missing column yields "" rather than raising —
a changed header should cost one field, not the whole import.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from pathlib import Path

from .model import Education, Position, Profile, Project

log = logging.getLogger(__name__)


def _pick(row: dict, *names: str) -> str:
    for name in names:
        for key, value in row.items():
            if key and key.strip().lower() == name.lower() and value:
                return str(value).strip()
    return ""


def _read_csv(blob: bytes) -> list[dict]:
    try:
        text = blob.decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(text)))
    except Exception as exc:
        log.warning("unreadable CSV in LinkedIn export: %s", exc)
        return []


def _load_files(path: Path) -> dict[str, list[dict]]:
    """{lowercased filename stem: rows} from a ZIP or a directory."""
    out: dict[str, list[dict]] = {}
    if path.is_dir():
        for f in path.glob("*.csv"):
            out[f.stem.lower()] = _read_csv(f.read_bytes())
        return out
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".csv"):
                    stem = Path(name).stem.lower()
                    out[stem] = _read_csv(zf.read(name))
    except (zipfile.BadZipFile, OSError) as exc:
        log.warning("could not open LinkedIn export %s: %s", path, exc)
    return out


def parse_export(path: str | Path) -> Profile:
    """Profile from an export ZIP/folder; empty Profile if unreadable."""
    files = _load_files(Path(path))
    if not files:
        return Profile()

    positions = tuple(
        Position(
            company=_pick(r, "Company Name", "Company"),
            title=_pick(r, "Title", "Position"),
            started=_pick(r, "Started On", "Start Date"),
            ended=_pick(r, "Finished On", "End Date"),
            location=_pick(r, "Location"),
            description=_pick(r, "Description"),
        )
        for r in files.get("positions", [])
        if _pick(r, "Company Name", "Company") or _pick(r, "Title")
    )
    education = tuple(
        Education(
            school=_pick(r, "School Name", "School"),
            degree=_pick(r, "Degree Name", "Degree"),
            field=_pick(r, "Notes", "Field Of Study"),
            ended=_pick(r, "End Date", "Finished On"),
        )
        for r in files.get("education", []) if _pick(r, "School Name", "School")
    )
    projects = tuple(
        Project(name=_pick(r, "Title", "Name"),
                description=_pick(r, "Description"),
                url=_pick(r, "Url", "URL"))
        for r in files.get("projects", []) if _pick(r, "Title", "Name")
    )
    skills = tuple(
        s for s in (_pick(r, "Name", "Skill") for r in files.get("skills", []))
        if s
    )

    headline = location = name = summary = ""
    for r in files.get("profile", []):
        first, last = _pick(r, "First Name"), _pick(r, "Last Name")
        name = f"{first} {last}".strip() or name
        headline = _pick(r, "Headline") or headline
        location = _pick(r, "Geo Location", "Location") or location
        summary = _pick(r, "Summary") or summary
        break

    return Profile(name=name, headline=headline, location=location,
                   summary=summary, positions=positions, projects=projects,
                   education=education, skills=skills,
                   sources=("linkedin-export",))
