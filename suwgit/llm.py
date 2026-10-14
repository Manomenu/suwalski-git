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

import copy
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import gitops
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
# How many times a commit message is asked for when the answer comes back unusable.
ANSWER_ATTEMPTS = 2
MAX_WORK_SCOPE_ITEMS = 8
MAX_WORK_SCOPE_ITEM_CHARS = 200
MAX_SUMMARY_CHARS = 300
MAX_REVIEW_ITEMS = 10
MAX_REVIEW_ITEM_CHARS = 300
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

# B2B mode: the commit is also the record of work done for a client, so the
# model lists that work as its own field — a list, not prose, so every item
# becomes one line of the report.
B2B_RESPONSE_SCHEMA = copy.deepcopy(RESPONSE_SCHEMA)
B2B_RESPONSE_SCHEMA["json_schema"]["name"] = "b2b_commit_message"
B2B_RESPONSE_SCHEMA["json_schema"]["schema"]["properties"]["work_scope"] = {
    "type": "array",
    "minItems": 1,
    "maxItems": MAX_WORK_SCOPE_ITEMS,
    "items": {"type": "string"},
}
B2B_RESPONSE_SCHEMA["json_schema"]["schema"]["properties"]["summary"] = {"type": "string"}
B2B_RESPONSE_SCHEMA["json_schema"]["schema"]["required"] += ["summary", "work_scope"]

B2B_PROMPT = (
    SYSTEM_PROMPT
    + f"""
These changes are work done for a client, and the commit is the record that the
work was done. The client reads it in a monthly work report, and the client is
NOT technical: a business owner or manager who knows their product, not its
code. So the JSON object holds two more fields, both written for that reader:

- "summary": what this work is, in one or two plain sentences, at most 40 words.
  Say what is different now for the client or their users, and why it matters.
- "work_scope": the work behind it, as 1 to {MAX_WORK_SCOPE_ITEMS} items. Each item is one
  concrete piece of work, past tense, at most 20 words.

How to write both:
- Describe what people can now do, see or rely on, not how the code does it.
- No jargon: no endpoint, API, refactor, schema, component, migration, commit,
  repository, null, cache, regex, dependency — say what it means instead
  ("the server", "the database", "behind the scenes" are fine). If a technical
  term is unavoidable, say what it is for in the same sentence.
- No file names, function names, code identifiers, file counts or line counts.
- Be concrete and honest: the client must recognise real work, so name the
  screen, feature or problem. Do not inflate small changes or invent benefits.
- Behind-the-scenes work (tests, cleanup, upgrades, tooling) is real work too:
  say what it protects or makes easier, e.g. "added automatic checks so the
  checkout keeps working after future changes".

Good:
  "summary": "Customers can now pay for their order directly in the shop, and
  orders with a wrong total are stopped before payment."
  "work_scope": [
    "added a checkout page where customers enter their address and pay",
    "made the shop double-check the order total before taking payment",
    "added automatic checks that the checkout keeps working after future changes"
  ]

Bad (too technical for this reader):
  "summary": "Implemented POST /api/orders endpoint with Stripe integration
  and refactored CartService."
  "work_scope": ["added OrderSchema validation", "updated checkout.tsx"]
"""
)

# With `-i`, the message also tells a colleague how to check the change and how
# to read it: two checklists, technical this time, ready to paste into a merge
# request. They are their own fields, so suwgit lays them out, not the model.
REVIEW_PROMPT = f"""
The developer also wants the commit message to guide whoever checks and reviews
these changes, a fellow developer reading the merge request. So the JSON object
holds two more fields, each 1 to {MAX_REVIEW_ITEMS} short checklist items, technical
this time — file, class and command names are welcome, in backticks:

- "how_to_test": how to see that the change works. The commands to run and what
  they must show, the screen or endpoint to try and what to expect, the edge
  case worth trying by hand. Only name a command that exists: one in the
  project's task files below, or one the diff itself adds or uses. If none
  fits, say what to try instead of inventing a command.
- "how_to_review": how to read the diff. Where to start, what is moved or
  renamed rather than changed, which decision deserves a second look, what
  would be easy to miss, and what the change deliberately does not do.

Each item is one concrete step, imperative or a short statement, at most 30
words, no leading dash or checkbox. Do not repeat the same point in both lists.
Good:
  "how_to_test": ["`just check` passes", "Start without `Tenant` → the app refuses to start with a clear error"]
  "how_to_review": ["Start with `Extensions.cs`: the bot's files go before environment variables, so those still win"]
"""

REVIEW_FIELDS = {
    "how_to_test": {"type": "array", "minItems": 1, "maxItems": MAX_REVIEW_ITEMS, "items": {"type": "string"}},
    "how_to_review": {"type": "array", "minItems": 1, "maxItems": MAX_REVIEW_ITEMS, "items": {"type": "string"}},
}


def _response_schema(b2b: bool, review: bool) -> dict:
    schema = copy.deepcopy(B2B_RESPONSE_SCHEMA if b2b else RESPONSE_SCHEMA)
    if review:
        body = schema["json_schema"]["schema"]
        body["properties"].update(copy.deepcopy(REVIEW_FIELDS))
        body["required"] += list(REVIEW_FIELDS)
    return schema


@dataclass
class Suggestion:
    """What the model made of the changes: a name, and whether they are safe to commit."""

    message: str
    unsafe: bool = False
    unsafe_reason: str = ""
    work_scope: list[str] = field(default_factory=list)  # B2B mode only
    summary: str = ""  # B2B mode only: the plain-language version, for the client
    how_to_test: list[str] = field(default_factory=list)  # review mode only
    how_to_review: list[str] = field(default_factory=list)  # review mode only


class LlmUnavailable(Exception):
    """The server did not answer, or answered with something unusable."""


class AnswerUnusable(LlmUnavailable):
    """The server answered, but not with a usable message: cut off, malformed or incomplete.

    Unlike an unreachable server, worth asking again: the same request usually
    comes back whole the second time.
    """


class _HttpFailure(Exception):
    """Non-2xx from the server, kept with its status so the caller can react."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _user_prompt(tree: WorkingTree, hint: str = "", tooling: str = "") -> str:
    prompt = (
        f"Repository: {tree.root.name}\n\ngit status --porcelain:\n{tree.status}\ngit diff --stat:\n{tree.stat}\ngit diff:\n{tree.diff}\n"
    )
    if tooling:
        prompt += f"\nThe project's top level and task files, for the commands that exist:\n{tooling}\n"
    if hint:
        # Last, after the diff, so the model reads them with the changes fresh in
        # mind. They shape the wording only: the safety check and the JSON format
        # are not the developer's to switch off from the command line.
        prompt += (
            "\nThe developer's own instructions for this commit's message. Follow them for what the "
            "message says and how, as long as the answer stays the JSON object described above and "
            "the safety check is done as described:\n"
            f"{hint}\n"
        )
    return prompt


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
    if choice.get("finish_reason") == "length":
        used = (answer.get("usage") or {}).get("completion_tokens", "?")
        raise AnswerUnusable(f"the model's answer was cut off after {used} tokens")
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
        raise AnswerUnusable("model returned a message with no description")
    if len(description) > MAX_DESCRIPTION_CHARS:
        cut = description[:MAX_DESCRIPTION_CHARS].rsplit(" ", 1)[0]
        description = (cut or description[:MAX_DESCRIPTION_CHARS]).rstrip(",") + "…"

    return f"[{','.join(kept)}] {description}"


def clean_summary(text) -> str:
    """The client summary on one line, cut at a word if it runs long."""
    text = " ".join(str(text).split()).strip('"')
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(",") + "…"
    return text


def clean_work_scope(items) -> list[str]:
    """Work scope items as single lines: no bullets, no blanks, no runaway length."""
    if isinstance(items, str):
        items = items.splitlines()
    kept = []
    for item in items if isinstance(items, list) else []:
        text = " ".join(str(item).split()).lstrip("-*• ").rstrip(".")
        if not text:
            continue
        if len(text) > MAX_WORK_SCOPE_ITEM_CHARS:
            text = text[:MAX_WORK_SCOPE_ITEM_CHARS].rsplit(" ", 1)[0].rstrip(",") + "…"
        kept.append(text)
    return kept[:MAX_WORK_SCOPE_ITEMS]


def clean_review_items(items) -> list[str]:
    """Checklist items as single lines, without the dash or checkbox suwgit adds itself."""
    if isinstance(items, str):
        items = items.splitlines()
    kept = []
    for item in items if isinstance(items, list) else []:
        text = re.sub(r"^(?:[-*•]\s*)?(?:\[[ xX]?\]\s*)?", "", " ".join(str(item).split()))
        if not text:
            continue
        if len(text) > MAX_REVIEW_ITEM_CHARS:
            text = text[:MAX_REVIEW_ITEM_CHARS].rsplit(" ", 1)[0].rstrip(",") + "…"
        kept.append(text)
    return kept[:MAX_REVIEW_ITEMS]


def review_notes(how_to_test: list[str], how_to_review: list[str]) -> str:
    """The two checklists as they go into the message: markdown, so a merge request renders them."""
    sections = [
        f"{heading}:\n" + "\n".join(f"- [ ] {item}" for item in items)
        for heading, items in (("How to test", how_to_test), ("How to review", how_to_review))
        if items
    ]
    return "\n\n".join(sections)


def parse_structured(content: str) -> Suggestion:
    """Read the JSON object the schema forced the model to produce."""
    text = strip_reasoning(content).strip().strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise AnswerUnusable(f"expected a JSON object, got: {text[:120]}")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AnswerUnusable(f"malformed JSON from the model: {exc}") from None
    if not isinstance(payload, dict):
        raise AnswerUnusable("model returned JSON that is not an object")

    categories = payload.get("categories") or []
    if isinstance(categories, str):
        categories = [c for c in re.split(r"[,\s]+", categories) if c]

    return Suggestion(
        message=build(list(categories), payload.get("description", "")),
        unsafe=bool(payload.get("unsafe_for_commit", False)),
        unsafe_reason=" ".join(str(payload.get("unsafe_reason") or "").split()),
        work_scope=clean_work_scope(payload.get("work_scope") or []),
        summary=clean_summary(payload.get("summary") or ""),
        how_to_test=clean_review_items(payload.get("how_to_test") or []),
        how_to_review=clean_review_items(payload.get("how_to_review") or []),
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


def _complete(config: LlmConfig, system: str, user: str, schema: dict) -> str:
    """One chat completion under `schema`, thinking off; the answer's text.

    A server that rejects the extras — old vLLM, or a proxy in front of it — is
    asked again without them, and the caller parses whatever prose comes back.
    """
    if not config.is_configured:
        raise LlmUnavailable("LLM base_url/model not configured — run `suwgit init`")

    payload = {
        "model": config.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "response_format": schema,
        "chat_template_kwargs": NO_THINKING,
    }

    try:
        return _content(_post(config, payload))
    except _HttpFailure as exc:
        if exc.status not in (400, 404, 422, 501):
            raise LlmUnavailable(f"HTTP {exc.status} from {config.base_url}: {exc.detail}") from None
        # Old vLLM, or a proxy that rejects the extras: ask again with none of them.
        payload.pop("response_format")
        payload.pop("chat_template_kwargs")
        try:
            return _content(_post(config, payload))
        except _HttpFailure as retry_exc:
            raise LlmUnavailable(f"HTTP {retry_exc.status} from {config.base_url}: {retry_exc.detail}") from None


def suggest_commit_message(config: LlmConfig, tree: WorkingTree, b2b: bool = False, hint: str = "", review: bool = False) -> Suggestion:
    """The whole point: uncommitted changes in, one commit message out.

    With `b2b` the suggestion also carries a plain-language summary and a work
    scope for the client, and both are required: a B2B commit without them is
    no record of work, so an answer that lacks one raises instead of falling
    back to a bare title.

    `hint` holds the developer's own words on what the message should
    say — "mention the ticket is only half done", "write it in Polish" — handed
    to the model with the diff.

    With `review` the suggestion also carries `how_to_test` and `how_to_review`,
    checklists for whoever checks the change; required too, for the same reason.
    """
    # A server now and then hands back an answer cut off mid-way, or JSON that
    # does not parse. Asking once more is cheap next to a sweep left uncommitted.
    for attempt in range(ANSWER_ATTEMPTS):
        try:
            return _suggest_once(config, tree, b2b, hint, review)
        except AnswerUnusable as exc:
            if attempt + 1 == ANSWER_ATTEMPTS:
                raise AnswerUnusable(f"{exc} (asked {ANSWER_ATTEMPTS} times)") from None
    raise AssertionError("unreachable")


def _suggest_once(config: LlmConfig, tree: WorkingTree, b2b: bool, hint: str = "", review: bool = False) -> Suggestion:
    system = (B2B_PROMPT if b2b else SYSTEM_PROMPT) + (REVIEW_PROMPT if review else "")
    tooling = gitops.project_tooling(tree.root) if review else ""
    content = _complete(config, system, _user_prompt(tree, hint, tooling), _response_schema(b2b, review))

    if review:
        suggestion = parse_structured(content)
        if not suggestion.how_to_test or not suggestion.how_to_review:
            raise AnswerUnusable("-i needs both checklists, how to test and how to review, and the model left one out")
        if b2b and (not suggestion.work_scope or not suggestion.summary):
            raise AnswerUnusable("B2B mode needs a summary and a work scope, and the model left one out")
        return suggestion

    if b2b:
        suggestion = parse_structured(content)
        if not suggestion.work_scope or not suggestion.summary:
            raise AnswerUnusable("B2B mode needs a summary and a work scope, and the model left one out")
        return suggestion

    try:
        return parse_structured(content)
    except AnswerUnusable:
        # An answer that set out as JSON and broke off is a broken answer, not
        # prose: read as prose, it would commit its own fragments as a message.
        if strip_reasoning(content).lstrip("` \n").startswith(("{", "json")):
            raise
        # 200 OK but prose anyway — a proxy that silently ignored the schema.
        return parse_free_text(content)


# --- the project summary of a report ---------------------------------------------

# How much of a project's recorded work the model reads. Work scopes are short,
# so this holds months of entries; past it the oldest are left out, and the
# model is told so.
MAX_PROJECT_INPUT_CHARS = 40_000
MAX_PROJECT_SUMMARY_CHARS = 700

PROJECT_SUMMARY_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "project_summary",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
}

PROJECT_SUMMARY_PROMPT = """You write the project summary at the top of one project's section in a work
report that a contractor sends to a client. The client is NOT technical: a
business owner or manager who knows their product, not its code.

You get the work recorded for the project in the reporting period: each entry
with its date, a short summary and the pieces of work behind it.

Answer with a JSON object holding one field:
- "summary": what the work in this period achieved for the project, in 2 to 4
  plain sentences, at most 80 words.

How to write it:
- Give the big picture: group the work into its main themes and outcomes rather
  than retelling the entries one by one.
- Say what the client or their users can now do, see or rely on.
- Use only what the entries say. Do not invent results, numbers, deadlines or
  benefits, and do not judge the quality of the work.
- An entry marked "reverted" was undone later: never count it as achieved. Leave
  it out, or say plainly that it was tried and taken back.
- No jargon, no file names, no code identifiers.
- Plain English, past tense, neutral voice — "This period, the shop gained…",
  not "I" or "we".
"""


def _project_input(project: str, first: str, last: str, entries: list[tuple[str, str, list[str]]]) -> str:
    """The recorded work as the model reads it, oldest left out first if it runs long."""
    blocks = []
    for day, summary, scope in entries:
        lines = [f"- {day}: {summary}" if summary else f"- {day}"]
        lines += [f"    - {item}" for item in scope]
        blocks.append("\n".join(lines))

    header = f"Project: {project}\nPeriod: {first} to {last}\n\nRecorded work:\n"

    def note(omitted: int) -> str:
        return f"({omitted} older entries left out for length)\n" if omitted else ""

    omitted = 0
    while blocks and len(header) + len(note(omitted)) + len("\n".join(blocks)) > MAX_PROJECT_INPUT_CHARS:
        blocks.pop(0)
        omitted += 1
    return header + note(omitted) + "\n".join(blocks)


def summarize_project(config: LlmConfig, project: str, first: str, last: str, entries: list[tuple[str, str, list[str]]]) -> str:
    """A plain-language summary of one project's work in a report period.

    `entries` are (day, summary, work scope) for each B2B commit, oldest first.
    Raises LlmUnavailable when there is nothing usable to say.
    """
    content = _complete(config, PROJECT_SUMMARY_PROMPT, _project_input(project, first, last, entries), PROJECT_SUMMARY_SCHEMA)
    text = strip_reasoning(content).strip().strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        try:
            payload = json.loads(text[start : end + 1])
            if isinstance(payload, dict):
                text = str(payload.get("summary") or "")
        except json.JSONDecodeError:
            pass  # prose from a server that ignored the schema: the text is the summary
    summary = " ".join(text.split()).strip('"')
    if not summary:
        raise LlmUnavailable("model returned an empty project summary")
    if len(summary) > MAX_PROJECT_SUMMARY_CHARS:
        summary = summary[:MAX_PROJECT_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(",") + "…"
    return summary
