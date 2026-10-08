"""Query rewriting: turn a student's request into a structured search before it hits the index.

"stage data près de Mulhouse" says little to a full-text index and is a weak embedding query.
Claude rewrites it, with the student's profile, into:
- a sentence describing the ideal offer (embedded for the meaning-based ranking);
- exact terms, French and English, for the word-based ranking (job titles, tools, synonyms);
- the kinds of contract and, when the request names one, a place and a radius.

The answer is a validated Pydantic object (structured outputs), so the search never parses
free text. The key is read from RADAR_ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import os
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

DEFAULT_MODEL = os.environ.get("RADAR_CLAUDE_MODEL", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """\
You prepare searches for a job radar used by students in France. Turn the student's request,
and their profile when given, into a structured search over job offers written in French.

- semantic_query: one or two French sentences describing the ideal offer for this student
  (role, field, level, contract), as an offer would be written. No place names.
- keywords: 4 to 12 exact terms an ideal offer is likely to contain: French and English job
  titles, tools, skills, common abbreviations ("data analyst", "analyste de données", "SQL",
  "Power BI", "BI"). Short terms, no full sentences, no place names, no contract words.
- kinds: the contracts the request asks for, among internship (stage), apprenticeship
  (alternance), student_job (job étudiant, temps partiel), job (CDI, CDD). Empty if the
  request does not say.
- town and radius_km: only if the request names a place or a distance; otherwise null.

Use the profile to choose terms that match what the student can actually do, but never add
requirements the student did not ask for."""


class RewrittenQuery(BaseModel):
    semantic_query: str = Field(description="French description of the ideal offer")
    keywords: list[str] = Field(description="Exact terms, French and English")
    kinds: list[Literal["internship", "apprenticeship", "student_job", "job"]]
    town: str | None
    radius_km: float | None


class RewriteRefused(Exception):
    pass


def client() -> anthropic.Anthropic:
    key = os.environ.get("RADAR_ANTHROPIC_API_KEY", "").strip()
    if not key.startswith("sk-ant-"):
        raise RuntimeError(
            "RADAR_ANTHROPIC_API_KEY manquante ou invalide : la réécriture de requête a besoin "
            "d'une clé de platform.claude.com."
        )
    return anthropic.Anthropic(api_key=key)


def rewrite(
    request: str,
    profile: str = "",
    api: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
) -> RewrittenQuery:
    content = f"Request: {request}"
    if profile:
        content = f"<profile>\n{profile}\n</profile>\n\n{content}"
    response = (api or client()).beta.messages.parse(
        model=model,
        max_tokens=16000,  # room for thinking: a cut-off answer would fail validation
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=RewrittenQuery,
        # A short, well-specified task: low effort keeps it fast and cheap.
        output_config={"effort": "low"},
        # If a safety classifier declines, the API retries on its recommended fallback model.
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RewriteRefused(f"stop_reason={response.stop_reason}")
    return response.parsed_output
