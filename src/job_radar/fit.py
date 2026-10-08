"""How well the student fits an offer, and what to do about the gaps.

Claude reads the offer and the student's profile (data/profile.md, never in the repository) and
returns, as a validated structure:
- the offer's requirements, each with a short quote from the offer;
- for each one: covered (with a quote from the profile), to confirm (related experience: a CV
  line to add only if it is true), or missing;
- up to three weekend-sized projects to learn the most important missing skills.

Nothing is ever added to the CV automatically, and nothing is taken on trust: every quote is
looked up word for word in the offer and in the profile. A "covered" skill whose profile quote
cannot be found is downgraded to "to confirm", so a skill the profile does not show is never
presented as acquired.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Literal

import anthropic
from pydantic import BaseModel, Field, PrivateAttr

from .rewrite import FALLBACK_BETA, client

DEFAULT_MODEL = os.environ.get("RADAR_CLAUDE_MODEL", "claude-opus-5-5")

SYSTEM = """\
You help a student in France see how well they fit one job offer and what to do about the
gaps. You receive the student's profile (their CV) and the offer. Write every field in French,
addressing the student as "tu".

1. requirements: what the offer concretely asks for: technical skills, tools, languages,
   degree or level, certifications, and soft skills only when the offer names them. At most
   12, the most important first; merge duplicates; skip generic qualities ("dynamique",
   "rigoureux") unless the offer asks for nothing else.
   - level: "required" when the offer demands it, "nice_to_have" for "un plus", "idéalement",
     "apprécié" and the like.
   - evidence: a short verbatim quote from the offer (a few words, copied exactly).
2. Compare each requirement with the profile, strictly:
   - covered: the profile states it, or a project or job in the profile clearly used it.
     profile_evidence: a short verbatim quote from the profile, copied exactly.
   - to_confirm: the profile suggests related experience without stating it (it lists
     PostgreSQL and the offer asks for MySQL; a project probably involved the skill).
     profile_evidence: the related verbatim quote. suggestion: one line the student could add
     to the CV if, and only if, it is true, written as a CV line.
   - missing: nothing in the profile supports it. profile_evidence and suggestion: null.
   Never invent experience, and never suggest adding a skill the profile gives no reason to
   believe the student has.
3. projects: for the most important missing requirements (required ones first), at most 3,
   one small project each that the student can finish in a weekend or a few evenings, with
   free tools and public data where data is needed (data.gouv.fr, INSEE, SNCF open data...),
   ending in something a recruiter can look at (a GitHub repository, a notebook, a
   dashboard). Tie it to the employer's activity when the offer describes it. No links.
4. summary: two sentences: how well the profile fits this offer, and the single most useful
   thing to do before applying."""


class Requirement(BaseModel):
    skill: str = Field(description="Short name: 'SQL', 'Power BI', 'anglais professionnel'")
    level: Literal["required", "nice_to_have"]
    evidence: str = Field(description="Short verbatim quote from the offer")
    status: Literal["covered", "to_confirm", "missing"]
    profile_evidence: str | None = Field(description="Short verbatim quote from the profile")
    suggestion: str | None = Field(description="to_confirm only: a CV line to add if true")
    # set by check(), never part of the schema the model fills
    _evidence_found: bool = PrivateAttr(default=True)
    _note: str = PrivateAttr(default="")

    @property
    def evidence_found(self) -> bool:
        return self._evidence_found

    @property
    def note(self) -> str:
        return self._note


class MiniProject(BaseModel):
    skill: str
    title: str
    goal: str = Field(description="What the student builds, in one sentence")
    steps: list[str] = Field(description="3 to 5 concrete steps")
    duration: str = Field(description="'un week-end', 'trois soirées'")
    shows: str = Field(description="What it proves to this employer")


class Fit(BaseModel):
    summary: str
    requirements: list[Requirement]
    projects: list[MiniProject]


class FitRefused(Exception):
    pass


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[«»“”\"'’`]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .,;:!?-–—()[]")


def quoted(quote: str | None, text: str) -> bool:
    """Is the quote really in the text (ignoring case, spacing and quotation marks)?"""
    q = _norm(quote or "")
    return bool(q) and q in _norm(text)


def check(fit: Fit, offer_text: str, profile: str) -> Fit:
    """Verify every quote; never let an unsupported skill pass as covered."""
    for r in fit.requirements:
        r._evidence_found = quoted(r.evidence, offer_text)
        if r.status == "covered" and not quoted(r.profile_evidence, profile):
            r.status = "to_confirm"
            r._note = "citation introuvable dans ton profil : à vérifier"
        if r.status != "to_confirm":
            r.suggestion = None
        if r.status == "missing":
            r.profile_evidence = None
    fit.projects = fit.projects[:3]
    return fit


def offer_text(
    title: str,
    company: str,
    city: str,
    kinds: list[str],
    weekly_hours: float | None,
    description: str,
) -> str:
    lines = [f"Titre : {title}", f"Entreprise : {company or 'non indiquée'}", f"Lieu : {city}"]
    lines.append(f"Contrat : {', '.join(kinds)}")
    if weekly_hours:
        lines.append(f"Heures : {weekly_hours:g} h par semaine")
    return "\n".join(lines) + f"\n\n{description}"


def analyse(
    offer: str,
    profile: str,
    api: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
) -> Fit:
    if not profile.strip():
        raise ValueError("Sans profil, il n'y a rien à comparer : renseigner data/profile.md.")
    response = (api or client()).beta.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"<profile>\n{profile}\n</profile>\n\n<offer>\n{offer}\n</offer>",
            }
        ],
        output_format=Fit,
        # Weighing a CV against an offer needs some care, not a long reasoning.
        output_config={"effort": "medium"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise FitRefused(f"stop_reason={response.stop_reason}")
    return check(response.parsed_output, offer, profile)
