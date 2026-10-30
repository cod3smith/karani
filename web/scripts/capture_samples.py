#!/usr/bin/env python3
"""Build the worked examples the site's pages render, from fabricated data.

Two kinds of thing come out of here:

* **Qualification results**, built as real `QualificationResult` objects
  from `karani/qualification/models.py` and dumped as JSON. Going through
  pydantic is the point: the field names, the score bounds and the
  three-way verdict are karani's own, so a page showing a verdict cannot
  show a shape the pipeline does not produce. A model is never called -- an
  LLM at docs-build time would be billed, non-deterministic and offline
  half the time.

* **Command transcripts**, written out as text in the exact shape
  `karani/cli.py` prints, so the `<RunReport>` plates on the site read like
  a terminal rather than like prose about a terminal. These are
  hand-composed; nothing executes a hunt pass at docs-build time.

Also written: the fabricated allowlist, so `scripts/check-synthetic.mjs`
can verify the built site against the same single source
(`scripts/fixtures.py`) rather than a second copy of the names.

Everything here is invented. See scripts/fixtures.py for the rules.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import CANDIDATE, COMPANIES, RESUME_LINES, allowlist, company  # noqa: E402

LIB = Path(__file__).resolve().parents[1] / "lib"

# The versions the samples are labelled with. Read from karani rather than
# written down, because a fit score is only comparable within the model and
# prompt version that produced it (ADR 0022) and a sample claiming the wrong
# version teaches exactly the wrong lesson.
def provenance() -> tuple[str, str]:
    from karani.qualification.prompts import PROMPT_VERSION

    return "qwen3:4b", PROMPT_VERSION


def build_verdicts() -> dict:
    """Three results: one qualified, one maybe, one skip.

    Each goes through `QualificationResult`, so anything that would not
    validate -- a score over 100, a fourth verdict -- fails here instead of
    rendering on a page.
    """
    from karani.qualification.models import QualGap, QualificationResult, QualStrength

    model, prompt_version = provenance()
    resume_hash = "sha256:0f3c2a" + "e" * 10

    def result(**kwargs) -> QualificationResult:
        return QualificationResult(
            model=model,
            prompt_version=prompt_version,
            resume_hash=resume_hash,
            **kwargs,
        )

    samples: dict[str, dict] = {}

    northwind = company("Northwind Robotics")
    samples["qualified"] = {
        "job": {
            "id": 1482,
            "company": northwind["name"],
            "title": "Staff Backend Engineer, Platform",
        },
        **result(
            fit_score=91,
            verdict="qualified",
            strengths=[
                QualStrength(
                    claim="Has owned an event platform at this scale before",
                    evidence_from_resume=RESUME_LINES["platform"],
                ),
                QualStrength(
                    claim="The posting's deadline-scheduling problem is one "
                    "she has shipped a solver for",
                    evidence_from_resume=RESUME_LINES["dispatch"],
                ),
            ],
            gaps=[
                QualGap(
                    requirement="Kubernetes operator experience",
                    mitigation="She has run workloads on Kubernetes but has "
                    "not written an operator. Name the control-loop work in "
                    "the dispatch planner, which is the same shape.",
                    severity="low",
                ),
            ],
            red_flags=[],
            why_apply="Hires globally at one band, states it in the posting, "
            "and the fleet-scheduling work is a direct match for the "
            "dispatch rebuild. Apply today: posted two days ago.",
            recommended_positioning="Lead with the dispatch planner, not the "
            "event platform. The posting is about reconciling state across "
            "regions on a deadline, and that is the harder of her two "
            "stories.",
        ).model_dump(),
    }

    meridian = company("Meridian Labs")
    samples["maybe"] = {
        "job": {
            "id": 1523,
            "company": meridian["name"],
            "title": "Machine Learning Engineer, Ranking",
        },
        **result(
            fit_score=64,
            verdict="maybe",
            strengths=[
                QualStrength(
                    claim="Has built the measurement layer a ranking team "
                    "needs, if not the ranker",
                    evidence_from_resume=RESUME_LINES["causal"],
                ),
            ],
            gaps=[
                QualGap(
                    requirement="Production ranking or recommender systems",
                    mitigation="Reframe as measurement: the posting's second "
                    "paragraph is about trusting offline evaluation, which is "
                    "the uplift library's whole subject.",
                    severity="high",
                ),
                QualGap(
                    requirement="PyTorch at training scale",
                    mitigation="None worth writing. Say so rather than "
                    "stretching.",
                    severity="medium",
                ),
            ],
            red_flags=[
                "Comp is undisclosed and the company has no published band, "
                "so the SF-band test cannot be applied from the posting."
            ],
            why_apply="A real stretch on the modelling, a real match on the "
            "measurement. Worth one application if the week is light, not "
            "worth the strongest cover letter she has.",
            recommended_positioning="Apply as the person who will tell them "
            "whether their ranker actually improved, not as a second ranking "
            "engineer.",
        ).model_dump(),
    }

    calderwood = company("Calderwood Health")
    samples["skip"] = {
        "job": {
            "id": 1544,
            "company": calderwood["name"],
            "title": "Senior Software Engineer (Hybrid, Boston)",
        },
        **result(
            fit_score=21,
            verdict="skip",
            strengths=[
                QualStrength(
                    claim="The on-call and reliability expectations are ones "
                    "she has met",
                    evidence_from_resume=RESUME_LINES["oncall"],
                ),
            ],
            gaps=[
                QualGap(
                    requirement="Three days a week in the Boston office",
                    mitigation="",
                    severity="high",
                ),
                QualGap(
                    requirement="US work authorization, no sponsorship offered",
                    mitigation="",
                    severity="high",
                ),
            ],
            red_flags=[
                "Hybrid with a named office and no relocation support.",
                "Posting states sponsorship is not available.",
            ],
            why_skip="Hybrid in Boston with no sponsorship fails both "
            "qualifying shapes: it is not global-remote, and it does not "
            "sponsor relocation. The skill match is irrelevant to that.",
            recommended_positioning="",
        ).model_dump(),
    }

    return samples


# The transcripts. Written to match what cli.py prints: a machine-readable
# summary line at the end, counts right-aligned in two or more spaces so the
# RunReport parser puts them in their own column, and nothing invented about
# what karani reports.
TRANSCRIPTS: dict[str, str] = {
    "hunt": """$ karani run
hunting 9 sources
fetched    greenhouse, lever, ashby, workable            612
fetched    remoteok, himalayas, wwr, remotive, aijobs    1,044
dropped    role not engineering-adjacent                 898
dropped    seniority below the floor                     311
dropped    region-locked, no relocation offered          274
dropped    comp below $160k where disclosed              63
dropped    no skill overlap with the profile             51
skipped    already seen this week (canonical hash)       32
flagged    1 source returned 0 rows: `workable`          --
pass complete: 1,656 fetched, 27 pending, 0 model calls, $0.00""",
    "qualify": """$ karani qualify --limit 20
qualifying 20 pending rows on qwen3:4b
qualified  `Staff Backend Engineer, Platform`     91
qualified  `Senior Research Engineer`             83
flagged    `Machine Learning Engineer, Ranking`   64
skipped    `Senior Software Engineer (Hybrid)`    21
skipped    16 rows below the verdict threshold    --
pass complete: 20 judged, 2 qualified, 1 for review, $0.00 (local)""",
    "hourly": """$ karani hourly
hunting 9 sources
fetched    all sources                        1,656
dropped    pre-filter                         1,597
qualified  3 new rows above the fit floor     --
drafted    `Staff Backend Engineer, Platform` at Northwind Robotics
delivered  1 review card to Slack
skipped    2 rows: per-run draft cap reached  --
pass complete in 11m42s: 1 pack drafted, $0.31 of $15.00 this month""",
    "console": """$ karani console
reading 3 packs in drafting or ready
fetched    application questions from greenhouse     1
fetched    application questions from lever          1
flagged    ashby exposes no form; using the standard field set
delivered  data/submission_console.html              3 jobs
pass complete: 0 model calls, $0.00

Open it, send each one, then run:
  karani submitted 1482 1511 1523""",
    "watch": """$ karani watch --no-slack
reading 14 days of mail, 38 messages
skipped    31 already in the ledger       --
fetched    7 new messages                 --
qualified  interview_invite -> job 1482   high
qualified  rejection -> job 1544          high
flagged    2 messages matched no application; reported for manual entry
skipped    3 classified irrelevant        --
pass complete: 2 applications advanced, 1 reply drafted for review""",
}


def ts_module(verdicts: dict, transcripts: dict) -> str:
    """lib/samples.generated.ts.

    A plain literal, not JSON imported at runtime, so Next statically
    includes it and `scripts/generate-llms.mjs` can evaluate it without a
    bundler.
    """
    lines = [
        "// Generated by web/scripts/capture_samples.py. Do not edit:",
        "// the next docs build overwrites it.",
        "//",
        "// Every job, company and person below is FABRICATED",
        "// (web/scripts/fixtures.py). The qualification results are real",
        "// `QualificationResult` objects from karani's own pydantic model,",
        "// built from invented input -- no model was called to produce them.",
        "",
        "export const samples = {",
        f"  verdicts: {json.dumps(verdicts, indent=4, ensure_ascii=False)},",
        f"  transcripts: {json.dumps(transcripts, indent=4, ensure_ascii=False)},",
        "} as const",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    try:
        verdicts = build_verdicts()
    except ImportError as exc:
        print(
            f"error: cannot import karani ({exc}).\n"
            "The samples are built through karani's own pydantic models, so "
            "the docs build needs karani importable. `uv sync` in the repo "
            "root.",
            file=sys.stderr,
        )
        return 1

    # A sample that names a company the fixtures do not declare would slip
    # past check-synthetic.mjs's allowlist, so it fails here instead.
    declared = {c["name"] for c in COMPANIES}
    for key, sample in verdicts.items():
        name = sample["job"]["company"]
        if name not in declared:
            print(
                f"error: the {key} sample names {name!r}, which is not a "
                f"fabricated company in scripts/fixtures.py.",
                file=sys.stderr,
            )
            return 1

    LIB.mkdir(parents=True, exist_ok=True)
    (LIB / "samples.generated.ts").write_text(
        ts_module(verdicts, TRANSCRIPTS), encoding="utf-8"
    )
    (LIB / "fabricated.generated.json").write_text(
        json.dumps(allowlist(), indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"captured samples: {len(verdicts)} verdicts, "
        f"{len(TRANSCRIPTS)} transcripts, all for {CANDIDATE['name']} "
        f"(fabricated)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
