"""Application message drafts: the request, and the check on what the message says."""

import json

import pytest

from job_radar.draft import FALLBACK_BETA, Draft, DraftRefused, check, write
from tests.test_fit import OFFER, PROFILE
from tests.test_rewrite import message, stub  # noqa: F401  (stub is a fixture)

ANSWER = {
    "subject": "Candidature : alternance Data Analyst",
    "body": "Madame Durand,\nVotre offre...",
    "facts": [
        {"claim": "Je maîtrise SQL et Power BI", "profile_quote": "SQL, PostgreSQL, Power BI"},
        {"claim": "J'ai deux ans d'expérience en dbt", "profile_quote": "dbt (2 ans)"},
    ],
}


def test_facts_not_in_the_profile_are_flagged():
    draft = check(Draft(**json.loads(json.dumps(ANSWER))), PROFILE)
    assert [f.found for f in draft.facts] == [True, False]


def test_request_carries_the_published_contact(stub):  # noqa: F811
    api, state = stub
    state["reply"] = message(json.dumps(ANSWER))
    draft = write(OFFER, "ACME - Mme Durand | https://acme.example/postuler", PROFILE, api=api)
    assert draft.subject == ANSWER["subject"] and not draft.facts[1].found

    body = state["body"]
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in state["headers"]["anthropic-beta"]
    assert set(body["output_config"]["format"]["schema"]["properties"]) == set(ANSWER)
    user = body["messages"][0]["content"]
    assert user.endswith("<contact>\nACME - Mme Durand | https://acme.example/postuler\n</contact>")


def test_no_contact_refusal_and_missing_profile(stub):  # noqa: F811
    api, state = stub
    state["reply"] = message(json.dumps(ANSWER))
    write(OFFER, "", PROFILE, api=api)
    assert "aucun contact publié" in state["body"]["messages"][0]["content"]
    state["reply"] = message("", stop_reason="refusal")
    with pytest.raises(DraftRefused):
        write(OFFER, "", PROFILE, api=api)
    with pytest.raises(ValueError, match="profile.md"):
        write(OFFER, "", "", api=api)
