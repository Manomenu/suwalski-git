"""One real HTTP round trip against a stub that speaks vLLM's dialect —
the payload shape and the timeout path are otherwise untested guesses."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from suwgit.config import LlmConfig
from suwgit.gitops import WorkingTree
from suwgit.llm import LlmUnavailable, suggest_commit_message


def _tree(tmp_path):
    return WorkingTree(root=tmp_path, status=" M a.py\n", stat=" a.py | 2 +-\n", diff="@@ -1 +1 @@\n-a\n+b\n")


class _Handler(BaseHTTPRequestHandler):
    received: ClassVar[dict] = {}
    reply_status = 200
    calls: ClassVar[list] = []
    reply_body: ClassVar[dict] = {
        "choices": [
            {
                "message": {
                    "content": '{"categories":["bugfix"],"description":"fixed the off-by-one in the parser",'
                    '"unsafe_for_commit":false,"unsafe_reason":""}'
                }
            }
        ]
    }

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        _Handler.received = {
            "path": self.path,
            "body": json.loads(self.rfile.read(length)),
            "auth": self.headers.get("Authorization"),
        }
        _Handler.calls.append(_Handler.received)
        payload = json.dumps(_Handler.reply_body).encode()
        self.send_response(_Handler.reply_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


@pytest.fixture
def stub_server():
    _Handler.calls = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    server.server_close()


def test_asks_the_server_and_returns_the_message(stub_server, tmp_path):
    config = LlmConfig(base_url=stub_server, model="qwen2.5-coder", api_key="k")

    suggestion = suggest_commit_message(config, _tree(tmp_path))

    assert suggestion.message == "[bugfix] fixed the off-by-one in the parser"
    assert suggestion.unsafe is False
    assert _Handler.received["path"] == "/v1/chat/completions"
    assert _Handler.received["auth"] == "Bearer k"
    body = _Handler.received["body"]
    assert body["model"] == "qwen2.5-coder"
    assert body["stream"] is False
    assert "@@ -1 +1 @@" in body["messages"][1]["content"]
    schema = body["response_format"]["json_schema"]["schema"]
    assert "bugfix" in schema["properties"]["categories"]["items"]["enum"]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_a_model_that_only_thinks_says_so_plainly(stub_server, tmp_path, monkeypatch):
    """An empty message with reasoning present is a token-budget problem, and the
    log must say that rather than 'empty message'."""
    monkeypatch.setattr(
        _Handler,
        "reply_body",
        {
            "choices": [{"message": {"content": "", "reasoning": "hmm, so the user changed…"}, "finish_reason": "length"}],
            "usage": {"completion_tokens": 1500},
        },
    )
    with pytest.raises(LlmUnavailable, match="whole budget reasoning"):
        suggest_commit_message(LlmConfig(base_url=stub_server, model="qwen"), _tree(tmp_path))


def test_an_empty_api_key_still_sends_a_bearer_header(stub_server, tmp_path):
    """vLLM ignores it, but a proxy in front of it may not."""
    suggest_commit_message(LlmConfig(base_url=stub_server, model="qwen"), _tree(tmp_path))
    assert _Handler.received["auth"] == "Bearer dummy"


def test_a_server_without_guided_decoding_is_asked_again_in_prose(stub_server, tmp_path, monkeypatch):
    """Old vLLM answers 400 to response_format — the retry must drop it, not give up."""
    original = _Handler.do_POST

    def refuse_schema(handler):
        length = int(handler.headers["Content-Length"])
        body = json.loads(handler.rfile.read(length))
        _Handler.calls.append(body)
        if "response_format" in body:
            handler.send_response(400)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        payload = json.dumps({"choices": [{"message": {"content": "Sure! Here it is: [feature] added a chart"}}]}).encode()
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    monkeypatch.setattr(_Handler, "do_POST", refuse_schema)
    try:
        suggestion = suggest_commit_message(LlmConfig(base_url=stub_server, model="qwen"), _tree(tmp_path))
    finally:
        monkeypatch.setattr(_Handler, "do_POST", original)

    assert suggestion.message == "[feature] added a chart"
    assert len(_Handler.calls) == 2
    assert "response_format" not in _Handler.calls[1]
    assert "chat_template_kwargs" not in _Handler.calls[1]


def test_a_proxy_that_ignores_the_schema_still_yields_a_message(stub_server, tmp_path, monkeypatch):
    """200 OK with prose instead of JSON: parse it rather than losing the sweep."""
    monkeypatch.setattr(
        _Handler,
        "reply_body",
        {"choices": [{"message": {"content": "<think>hmm</think>Here you go: [docs] documented the daemon"}}]},
    )
    suggestion = suggest_commit_message(LlmConfig(base_url=stub_server, model="qwen"), _tree(tmp_path))
    assert suggestion.message == "[docs] documented the daemon"
    assert suggestion.unsafe is False  # a prose answer carries no verdict


def test_server_error_is_llm_unavailable(stub_server, tmp_path, monkeypatch):
    monkeypatch.setattr(_Handler, "reply_status", 500)  # not a "schema unsupported" code — no retry
    with pytest.raises(LlmUnavailable):
        suggest_commit_message(LlmConfig(base_url=stub_server, model="qwen"), _tree(tmp_path))


def test_nothing_listening_is_llm_unavailable(tmp_path):
    config = LlmConfig(base_url="http://127.0.0.1:1/v1", model="qwen", timeout_seconds=2)
    with pytest.raises(LlmUnavailable):
        suggest_commit_message(config, _tree(tmp_path))


def test_unconfigured_llm_never_hits_the_network(tmp_path):
    with pytest.raises(LlmUnavailable, match="not configured"):
        suggest_commit_message(LlmConfig(), _tree(tmp_path))
