"""Profile against offer: the request, the parsed answer, and the checks that keep it honest."""

import json

import pytest

from job_radar import db
from job_radar.fit import FALLBACK_BETA, Fit, FitRefused, analyse, check, offer_text, quoted
from tests.test_db import offer
from tests.test_rewrite import message, stub  # noqa: F401  (stub is a fixture)

PROFILE = "Compétences : SQL, PostgreSQL, Power BI, Python.\nProjet Rail Punctuality : pipeline."
OFFER = offer_text(
    "Alternance Data Analyst",
    "ACME",
    "Mulhouse",
    ["apprenticeship"],
    None,
    "Vous maîtrisez SQL et Power BI. MySQL est un plus. Une première expérience de dbt serait "
    "appréciée. Anglais professionnel exigé.",
)


def requirement(skill, status, evidence, profile_evidence=None, suggestion=None, level="required"):
    return {
        "skill": skill,
        "level": level,
        "evidence": evidence,
        "status": status,
        "profile_evidence": profile_evidence,
        "suggestion": suggestion,
    }


ANSWER = {
    "summary": "Ton profil colle bien. Avant de postuler, mets en avant Rail Punctuality.",
    "requirements": [
        requirement("SQL", "covered", "maîtrisez SQL", "SQL, PostgreSQL"),
        requirement("MySQL", "to_confirm", "MySQL est un plus", "PostgreSQL",
                    "Requêtes SQL sur MySQL et PostgreSQL", level="nice_to_have"),
        # the model claims a skill the profile does not show: must not pass as covered
        requirement("dbt", "covered", "expérience de dbt", "dbt (2 ans)", "Ajoute dbt"),
        requirement("Anglais", "missing", "anglais professionnel exigé", "rien", "Ajoute anglais"),
        requirement("Tableau", "missing", "Tableau obligatoire"),  # not in the offer
    ],
    "projects": [
        {"skill": s, "title": f"Projet {s}", "goal": "Construire", "steps": ["a", "b", "c"],
         "duration": "un week-end", "shows": "que tu sais"}
        for s in ("dbt", "Anglais", "Tableau", "Kafka")
    ],
}  # fmt: skip


def test_quotes_are_found_whatever_the_case_spacing_and_quotation_marks():
    assert quoted("maîtrisez  SQL", "Vous MAÎTRISEZ SQL et Power BI")
    assert quoted("« Power BI »", "SQL et Power BI.")
    assert not quoted("Tableau", "SQL et Power BI")
    assert not quoted("", "anything") and not quoted(None, "anything")


def test_check_never_lets_an_unsupported_skill_pass():
    fit = check(Fit(**json.loads(json.dumps(ANSWER))), OFFER, PROFILE)
    by = {r.skill: r for r in fit.requirements}
    assert by["SQL"].status == "covered" and by["SQL"].suggestion is None
    assert by["MySQL"].status == "to_confirm" and by["MySQL"].suggestion
    assert by["dbt"].status == "to_confirm"  # "dbt (2 ans)" is nowhere in the profile
    assert "introuvable" in by["dbt"].note
    assert by["Anglais"].profile_evidence is None and by["Anglais"].suggestion is None
    assert by["Tableau"].evidence_found is False  # the offer never says it
    assert [p.skill for p in fit.projects] == ["dbt", "Anglais", "Tableau"]  # three at most


def test_request_and_checked_answer(stub):  # noqa: F811
    api, state = stub
    state["reply"] = message(json.dumps(ANSWER))
    fit = analyse(OFFER, PROFILE, api=api, model="m")
    assert {r.skill: r.status for r in fit.requirements}["dbt"] == "to_confirm"

    body = state["body"]
    assert body["model"] == "m"
    assert body["output_config"]["effort"] == "medium"
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in state["headers"]["anthropic-beta"]
    schema = body["output_config"]["format"]["schema"]
    req = schema["$defs"]["Requirement"]["properties"]
    assert set(req) == {"skill", "level", "evidence", "status", "profile_evidence", "suggestion"}
    user = body["messages"][0]["content"]
    assert user.startswith(f"<profile>\n{PROFILE}\n</profile>")
    assert "<offer>\nTitre : Alternance Data Analyst" in user


def test_refusal_and_missing_profile(stub):  # noqa: F811
    api, state = stub
    state["reply"] = message("", stop_reason="refusal")
    with pytest.raises(FitRefused):
        analyse(OFFER, PROFILE, api=api, model="m")
    with pytest.raises(ValueError, match="profile.md"):
        analyse(OFFER, "  ", api=api, model="m")


def test_offer_lookup_and_fit_cache(conn):
    db.upsert_offers(conn, [offer("a", "Mulhouse", description="SQL")])
    o = db.get_offer(conn, "https://example.org/a")
    assert db.get_offer(conn, f"#{o.id}") == o == db.get_offer(conn, str(o.id))
    assert db.get_offer(conn, "#999999") is None

    assert db.get_fit(conn, o, "profile-hash", "m") is None
    db.save_fit(conn, o, "profile-hash", "m", {"summary": "ok"})
    assert db.get_fit(conn, o, "profile-hash", "m") == {"summary": "ok"}
    assert db.get_fit(conn, o, "other-profile", "m") is None  # another profile, another fit

    db.upsert_offers(conn, [offer("a", "Mulhouse", description="SQL et Python")])
    edited = db.get_offer(conn, str(o.id))
    assert db.get_fit(conn, edited, "profile-hash", "m") is None  # the offer changed
