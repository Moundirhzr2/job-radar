"""A short application message to the person who recruits, for the student to edit and send.

Nothing is ever sent: the student reads, edits and sends the message through the channel the
employer published (the offer's contact or application link). The message may only use facts
the profile states: Claude lists every fact it used with a quote from the profile, and each
quote is looked up word for word; a fact whose quote cannot be found is flagged before sending.
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, Field, PrivateAttr

from .fit import DEFAULT_MODEL, quoted
from .rewrite import FALLBACK_BETA, client

SYSTEM = """\
You write, for a student in France, a short application message to the person who recruits
for one job offer. You receive the student's profile (their CV), the offer, and the contact the
employer published in the offer, if any. The student will read, edit and send it themselves.

- French, first person, from the student. At most 150 words for the body.
- Address the published contact by name when the offer gives one ("Madame Durand,"), otherwise
  "Madame, Monsieur,".
- Say what draws the student to this offer or this employer, using what the offer itself says.
- Give two or three concrete proofs from the profile that match what the offer asks for: a
  project, a job, a result. Mention availability or a work-study rhythm only if the profile
  states it.
- End by proposing a short call or meeting.
- Plain and sincere: no clichés ("passionné", "dynamique", "rigoureux"), no exaggeration.
- Use only facts the profile states. Never mention a skill or an experience the profile does
  not show. List in facts every statement the message makes about the student, each with a
  short verbatim quote from the profile, copied exactly.
- subject: a short e-mail subject naming the offer."""


class Fact(BaseModel):
    claim: str = Field(description="What the message says about the student")
    profile_quote: str = Field(description="Short verbatim quote from the profile")
    _found: bool = PrivateAttr(default=True)

    @property
    def found(self) -> bool:
        return self._found


class Draft(BaseModel):
    subject: str
    body: str
    facts: list[Fact]


class DraftRefused(Exception):
    pass


def check(draft: Draft, profile: str) -> Draft:
    for fact in draft.facts:
        fact._found = quoted(fact.profile_quote, profile)
    return draft


def write(
    offer: str,
    contact: str,
    profile: str,
    api: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
) -> Draft:
    if not profile.strip():
        raise ValueError("Sans profil, rien à raconter : renseigner data/profile.md.")
    content = (
        f"<profile>\n{profile}\n</profile>\n\n<offer>\n{offer}\n</offer>\n\n"
        f"<contact>\n{contact or 'aucun contact publié'}\n</contact>"
    )
    response = (api or client()).beta.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=Draft,
        output_config={"effort": "medium"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise DraftRefused(f"stop_reason={response.stop_reason}")
    return check(response.parsed_output, profile)
