"""A local model through Ollama: the schema is required, the answer is validated."""

import json

import httpx
import pytest
from pydantic import ValidationError

from job_radar.llm import is_local, ollama_parse
from job_radar.rewrite import RewrittenQuery

ANSWER = {
    "semantic_query": "Alternance de data analyst",
    "keywords": ["data analyst", "SQL"],
    "kinds": ["apprenticeship"],
    "town": None,
    "radius_km": None,
}


def fake_ollama(content):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": content}})

    return httpx.Client(transport=httpx.MockTransport(handler)), sent


def test_the_model_name_chooses_the_engine():
    assert is_local("ollama:qwen3.5:4b") and not is_local("claude-opus-5-5")


def test_a_local_answer_follows_the_schema_and_is_validated():
    client, sent = fake_ollama(json.dumps(ANSWER))
    out = ollama_parse("ollama:qwen3.5:4b", RewrittenQuery, "system", "Request: data", client)
    assert out.kinds == ["apprenticeship"]
    (body,) = sent
    assert body["model"] == "qwen3.5:4b" and body["stream"] is False
    assert body["format"] == RewrittenQuery.model_json_schema()  # the structure is required
    assert body["messages"][0] == {"role": "system", "content": "system"}


def test_an_answer_outside_the_schema_is_refused():
    client, _ = fake_ollama(json.dumps(ANSWER | {"kinds": ["freelance"]}))
    with pytest.raises(ValidationError):
        ollama_parse("ollama:qwen3.5:4b", RewrittenQuery, "s", "c", client)


def test_contract_words_of_the_request_outrank_the_model():
    from job_radar.rewrite import with_asked_kinds

    wrong = RewrittenQuery(**ANSWER | {"kinds": ["internship"]})  # what qwen3.5:2b answered
    assert with_asked_kinds(wrong, "alternance data analyst").kinds == ["apprenticeship"]
    both = with_asked_kinds(wrong, "stage ou alternance en cybersécurité").kinds
    assert both == ["apprenticeship", "internship"]
    assert with_asked_kinds(wrong, "data analyst à Lyon").kinds == ["internship"]  # nothing said
