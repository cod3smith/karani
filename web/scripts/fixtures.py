"""The fabricated candidate and companies every example on the site uses.

The repository is public and so are the docs, so **no real posting, no real
company, and nothing from `data/resume.md` may appear anywhere in the built
site**. This module is the one place the invented names live, so there is a
single thing to read when asking "is this example real?", and so
`scripts/check-synthetic.mjs` has something to check the built output
against.

The names match the ones in the README's hero screenshot, which was
rendered from the same set, so the site and the README show the same
fictional hunt.

Rules for anything added here:

* A company name must not be a real company. These are deliberately
  generic-industrial ("Northwind Robotics") or invented-compound ("Helix
  Data") rather than near-misses of real employers.
* A slug must not be a real ATS slug. They are derived from the invented
  names, and the URLs they appear in resolve to nothing.
* The candidate is Ada Mwangi, who does not exist, and her resume lines are
  written for the examples rather than taken from anyone's.
"""

from __future__ import annotations

# The candidate in every example. Not a real person.
CANDIDATE = {
    "name": "Ada Mwangi",
    "first_name": "Ada",
    "last_name": "Mwangi",
    "email": "ada@example.com",
    "based_in": "Nairobi, Kenya",
    "headline": "Senior backend and data-platform engineer",
}

# The companies in every example. None of them exists.
COMPANIES = (
    {
        "name": "Northwind Robotics",
        "slug": "northwind-robotics",
        "ats": "greenhouse",
        "apply_url": "https://boards.greenhouse.io/northwind-robotics/jobs/4402185",
    },
    {
        "name": "Helix Data",
        "slug": "helixdata",
        "ats": "lever",
        "apply_url": "https://jobs.lever.co/helixdata/9f21c4a8",
    },
    {
        "name": "Meridian Labs",
        "slug": "meridian-labs",
        "ats": "ashby",
        "apply_url": "https://jobs.ashbyhq.com/meridian-labs/0b7e5512",
    },
    {
        "name": "Calderwood Health",
        "slug": "calderwood",
        "ats": "workable",
        "apply_url": "https://apply.workable.com/calderwood/j/A1B2C3D4E5/",
    },
)

# Slugs used illustratively rather than as an example posting: "the `acme`
# in boards.greenhouse.io/acme". They are not companies and never carry a
# candidate, a verdict or a pack -- they only show the shape of a URL -- so
# they are declared separately from COMPANIES and the ATS-URL check accepts
# them. `acme` is the long-standing placeholder for exactly this.
PLACEHOLDER_SLUGS = ("acme",)

# Resume lines the examples quote as evidence. Written for the examples.
# A real run quotes `data/resume.md`; nothing here is from it.
RESUME_LINES = {
    "dispatch": (
        "Rebuilt the dispatch planner for a Nairobi logistics operator: "
        "replaced a nightly batch with an incremental solver, cutting "
        "median delivery time from 2h10m to 45m across 1,400 daily drops."
    ),
    "platform": (
        "Owned the event platform at a 40-engineer company: Kafka to "
        "ClickHouse, 11 downstream teams, 2.4bn events a day, p99 ingest "
        "lag under 90 seconds."
    ),
    "causal": (
        "Built the uplift-measurement library the growth team now ships "
        "every experiment through: doubly-robust estimation with "
        "sensitivity bounds, so a result that depends on an untestable "
        "assumption says so."
    ),
    "oncall": (
        "Ran the on-call rotation for three years and wrote the incident "
        "review process; mean time to acknowledge fell from 22 to 4 "
        "minutes after the paging rewrite."
    ),
}


def company(name: str) -> dict:
    """One fabricated company by name, or a clear failure.

    A typo'd lookup must not silently fall through to a real-looking
    string, so this raises rather than returning a default.
    """
    for entry in COMPANIES:
        if entry["name"] == name:
            return entry
    known = ", ".join(repr(c["name"]) for c in COMPANIES)
    raise KeyError(f"{name!r} is not a fabricated company. Known: {known}")


def allowlist() -> dict:
    """Everything a checker needs to verify the built site is synthetic."""
    return {
        "candidate": CANDIDATE,
        "companies": [
            {"name": c["name"], "slug": c["slug"], "ats": c["ats"]} for c in COMPANIES
        ],
        "placeholder_slugs": list(PLACEHOLDER_SLUGS),
    }
