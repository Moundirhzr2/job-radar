"""The real Anthropic SDK against a local stub server: no key, no credit, no network."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import anthropic
import pytest

from job_radar.rewrite import FALLBACK_BETA, RewriteRefused, rewrite

ANSWER = {
    "semantic_query": "Alternance en analyse de données : SQL, Power BI, Python.",
    "keywords": ["data analyst", "analyste de données", "SQL", "Power BI"],
    "kinds": ["apprenticeship"],
    "town": "Mulhouse",
    "radius_km": None,
}


def message(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


@pytest.fixture
def stub():
    state = {"reply": message(json.dumps(ANSWER))}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["headers"] = dict(self.headers)
            state["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = json.dumps(state["reply"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api = anthropic.Anthropic(
        api_key="sk-ant-test", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=0
    )
    yield api, state
    server.shutdown()


def test_request_and_parsed_answer(stub):
    api, state = stub
    q = rewrite("alternance data près de Mulhouse", profile="Python, SQL", api=api, model="m")

    assert q.keywords == ANSWER["keywords"]
    assert q.kinds == ["apprenticeship"] and q.town == "Mulhouse" and q.radius_km is None

    body = state["body"]
    assert body["model"] == "m"
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in state["headers"]["anthropic-beta"]
    assert body["output_config"]["effort"] == "low"
    schema = body["output_config"]["format"]["schema"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert set(schema["required"]) == set(ANSWER)
    user = body["messages"][0]["content"]
    assert user.startswith("<profile>\nPython, SQL\n</profile>")
    assert user.endswith("Request: alternance data près de Mulhouse")


def test_refusal_is_reported(stub):
    api, state = stub
    state["reply"] = message("", stop_reason="refusal")
    with pytest.raises(RewriteRefused):
        rewrite("…", api=api)


def test_missing_key_is_explained(monkeypatch):
    monkeypatch.setenv("RADAR_ANTHROPIC_API_KEY", "ta_cle_anthropic")
    with pytest.raises(RuntimeError, match="platform.claude.com"):
        rewrite("stage data")
