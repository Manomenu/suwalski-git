"""Asking a vLLM server to name a commit.

vLLM speaks the OpenAI chat-completions dialect, so this is one POST to
`{base_url}/chat/completions` with urllib — no SDK, no dependency.

The answer is taken through **structured output** (`response_format` with a JSON
schema, which vLLM enforces with grammar-constrained decoding), not free text.
A small instruct model left to write prose will happily answer "Yeah, sure! Here
is your commit message: [feature] …", and a thinking model like Qwen3 will wrap
the whole thing in `<think>`. With the schema in force, the categories can only
come from the enum and the description is its own field, so there is nothing to
scrape off the front.

Thinking is switched off in the request: naming a commit is not a reasoning task,
and a model that reasons can exhaust its token budget before it answers at all.

`parse_free_text` is the safety net for a server that rejects guided decoding —
it strips reasoning and preambles and finds the message inside whatever came back.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import LlmConfig
from .gitops import WorkingTree

# The vocabulary the model may use. A closed list — enforced by the schema —
# keeps messages greppable instead of inventing a new word for every commit.
CATEGORIES = (
    "feature",
    "bugfix",
    "refactor",
    "docs",
    "test",
    "chore",
    "style",
    "perf",
    "build",
    "config",
    "remove",
)

MAX_DESCRIPTION_CHARS = 210
# Headroom for a backend that ignores the switch below and thinks anyway. With
# thinking off the answer is ~36 tokens, so this costs nothing when it is honoured.
MAX_TOKENS = 3000

# Qwen3 reasons before answering, and naming a commit is not a reasoning task:
# measured against a local Qwen3-8B the same diff took 952 completion tokens and
# ~18 s with thinking on (sometimes overrunning the budget and returning an EMPTY
# message — a silently lost sweep), against 36 tokens and 0.8 s with it off, for
# a byte-identical answer. vLLM forwards this to the chat template; a template
# that does not know the flag ignores it.
NO_THINKING = {"enable_thinking": False}

MESSAGE_RE = re.compile(r"\[(?P<cats>[^\]\n]*)\]\s*(?P<desc>[^\n]*)")
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
OPEN_THINK_RE = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)

RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "commit_message",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": len(CATEGORIES),
                    "items": {"type": "string", "enum": list(CATEGORIES)},
                },
                "description": {"type": "string"},
                "unsafe_for_commit": {"type": "boolean"},
                "unsafe_reason": {"type": "string"},
            },
            "required": ["categories", "description", "unsafe_for_commit", "unsafe_reason"],
            "additionalProperties": False,
        },
    },
}

SYSTEM_PROMPT = """You name git commits for a developer's working tree, and you check
that the changes are safe to commit at all.

Answer with a JSON object holding four fields:
- "categories": every kind of change that applies, from the allowed list
- "description": what was actually done, plain English, at most 15 words
- "unsafe_for_commit": true if the changes contain anything that must not go into
  a git history, false otherwise
- "unsafe_reason": when unsafe_for_commit is true, name what you found and the file
  it is in, in one short sentence. Leave it as "" when false.

Write the description as a developer would: say what changed and why it matters,
never a file count, never a line count, no trailing period.

Good descriptions:
- "added builder schema for raport creator and fixed main tab not opening"
- "added revenue chart to main page dashboard"

Set unsafe_for_commit to true for a real secret in the added lines: an API key,
access token, bearer token, session cookie, password, database connection string
with credentials in it, a private key block, a cloud provider credential, or a
filled-in .env file.

Set it to false for the ordinary things that only look alarming: the word
"password" or "api_key" as a variable name, a placeholder like "dummy",
"changeme", "xxx" or "your-key-here", an example or template file, a public key,
a test fixture with an obviously fake value, or a key that is being REMOVED by
this diff.
"""


@dataclass
class Suggestion:
    """What the model made of the changes: a name, and whether they are safe to commit."""

    message: str
    unsafe: bool = False
    unsafe_reason: str = ""


class LlmUnavailable(Exception):
    """The server did not answer, or answered with something unusable."""


class _HttpFailure(Exception):
    """Non-2xx from the server, kept with its status so the caller can react."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _user_prompt(tree: WorkingTree) -> str:
    return (
        f"Repository: {tree.root.name}\n\ngit status --porcelain:\n{tree.status}\ngit diff --stat:\n{tree.stat}\ngit diff:\n{tree.diff}\n"
    )


def _post(config: LlmConfig, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{config.base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # vLLM ignores the value, but a proxy in front of it may not, and OpenAI
    # clients refuse an empty one — so send whatever is configured.
    request.add_header("Authorization", f"Bearer {config.api_key or 'dummy'}")
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise _HttpFailure(exc.code, exc.read().decode("utf-8", errors="replace")[:300]) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LlmUnavailable(f"cannot reach {config.base_url}: {exc}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LlmUnavailable(f"unreadable answer from {config.base_url}: {exc}") from None


def _content(answer: dict) -> str:
    """The answer proper. Reasoning lives in its own field and is dropped here."""
    try:
        choice = answer["choices"][0]
        message = choice["message"]
        content = message.get("content")
    except (KeyError, IndexError, TypeError):
        raise LlmUnavailable("answer had no choices[0].message") from None

    if not isinstance(content, str) or not content.strip():
        # Worth naming precisely: it means the model thought instead of answering.
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        if reasoning:
            used = (answer.get("usage") or {}).get("completion_tokens", "?")
            raise LlmUnavailable(
                f"model spent its whole budget reasoning ({used} tokens, finish_reason="
                f"{choice.get('finish_reason')}) and returned no message — raise max_tokens "
                f"or keep thinking disabled"
            )
        raise LlmUnavailable("model returned an empty message")
    return content


def strip_reasoning(text: str) -> str:
    """Remove `<think>…</think>` from servers with no reasoning parser configured."""
    text = THINK_RE.sub("", text)
    if "</think>" in text.lower():
        text = OPEN_THINK_RE.sub("", text)
    return text.strip()


def build(categories: list[str], description: str) -> str:
    """Assemble `[cat,cat] description`, with the same limits either path took."""
    kept = []
    for candidate in categories:
        candidate = str(candidate).strip().lower()
        if candidate in CATEGORIES and candidate not in kept:
            kept.append(candidate)
    kept = kept or ["chore"]

    description = " ".join(str(description).split()).strip('"').strip("'").rstrip(".")
    if not description:
        raise LlmUnavailable("model returned a message with no description")
    if len(description) > MAX_DESCRIPTION_CHARS:
        cut = description[:MAX_DESCRIPTION_CHARS].rsplit(" ", 1)[0]
        description = (cut or description[:MAX_DESCRIPTION_CHARS]).rstrip(",") + "…"

    return f"[{','.join(kept)}] {description}"


def parse_structured(content: str) -> str:
    """Read the JSON object the schema forced the model to produce."""
    text = strip_reasoning(content).strip().strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LlmUnavailable(f"expected a JSON object, got: {text[:120]}")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LlmUnavailable(f"malformed JSON from the model: {exc}") from None
    if not isinstance(payload, dict):
        raise LlmUnavailable("model returned JSON that is not an object")

    categories = payload.get("categories") or []
    if isinstance(categories, str):
        categories = [c for c in re.split(r"[,\s]+", categories) if c]

    return Suggestion(
        message=build(list(categories), payload.get("description", "")),
        unsafe=bool(payload.get("unsafe_for_commit", False)),
        unsafe_reason=" ".join(str(payload.get("unsafe_reason") or "").split()),
    )


def parse_free_text(content: str) -> Suggestion:
    """Dig a commit message out of unconstrained prose.

    Only used when the server cannot do guided decoding. Everything a chatty
    model puts around the answer — reasoning, "Sure! Here is your commit
    message:", code fences — is thrown away rather than committed.

    A prose answer carries no secret-check flag, so this path returns `unsafe`
    unset. Say so in the docs rather than pretend the check ran.
    """
    text = strip_reasoning(content).replace("```", " ")
    if not text.strip():
        raise LlmUnavailable("model returned only reasoning")

    if "{" in text and "categories" in text:
        try:
            return parse_structured(text)
        except LlmUnavailable:
            pass

    # The bracketed form, wherever it sits — this is what drops a preamble.
    for match in MESSAGE_RE.finditer(text):
        raw = match.group("cats").replace("/", ",")
        categories = [c.strip().lower() for c in raw.split(",")]
        if any(c in CATEGORIES for c in categories) and match.group("desc").strip():
            return Suggestion(build(categories, match.group("desc")))

    # No brackets at all: the answer is the last thing it said.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise LlmUnavailable("model returned nothing usable")
    return Suggestion(build([], lines[-1]))


def suggest_commit_message(config: LlmConfig, tree: WorkingTree) -> Suggestion:
    """The whole point: uncommitted changes in, one commit message out."""
    if not config.is_configured:
        raise LlmUnavailable("LLM base_url/model not configured — run `suwgit init`")

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(tree)},
        ],
        "temperature": 0.2,
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "response_format": RESPONSE_SCHEMA,
        "chat_template_kwargs": NO_THINKING,
    }

    try:
        content = _content(_post(config, payload))
    except _HttpFailure as exc:
        if exc.status not in (400, 404, 422, 501):
            raise LlmUnavailable(f"HTTP {exc.status} from {config.base_url}: {exc.detail}") from None
        # Old vLLM, or a proxy that rejects the extras: ask again with none of them.
        payload.pop("response_format")
        payload.pop("chat_template_kwargs")
        try:
            content = _content(_post(config, payload))
        except _HttpFailure as retry_exc:
            raise LlmUnavailable(f"HTTP {retry_exc.status} from {config.base_url}: {retry_exc.detail}") from None

    try:
        return parse_structured(content)
    except LlmUnavailable:
        # 200 OK but prose anyway — a proxy that silently ignored the schema.
        return parse_free_text(content)
