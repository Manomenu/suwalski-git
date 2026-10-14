"""Whatever the model says, a commit message comes out clean — or nothing does.

The cases here are the ones that actually happen with small instruct models:
a chatty preamble, a `<think>` block, code fences, a category nobody offered.
"""

import pytest

from suwgit.llm import (
    CATEGORIES,
    MAX_DESCRIPTION_CHARS,
    LlmUnavailable,
    build,
    parse_free_text,
    parse_structured,
    strip_reasoning,
)


def test_structured_answer():
    assert parse_structured(
        '{"categories": ["feature"], "description": "added revenue chart to main page dashboard", "unsafe_for_commit": false, "unsafe_reason": ""}'
    ).message == ("[feature] added revenue chart to main page dashboard")


def test_structured_answer_with_several_categories():
    payload = '{"categories": ["refactor", "bugfix"], "description": "added builder schema and fixed main tab not opening"}'
    assert parse_structured(payload).message == "[refactor,bugfix] added builder schema and fixed main tab not opening"


def test_structured_answer_wrapped_in_a_think_block_and_fences():
    raw = '<think>Let me look at the diff…</think>\n```json\n{"categories":["docs"],"description":"documented the daemon"}\n```'
    assert parse_structured(raw).message == "[docs] documented the daemon"


def test_structured_answer_with_a_category_outside_the_enum():
    assert parse_structured('{"categories": ["wibble"], "description": "moved things around"}').message == "[chore] moved things around"


def test_structured_answer_missing_a_description_is_rejected():
    with pytest.raises(LlmUnavailable):
        parse_structured('{"categories": ["feature"], "description": ""}')


def test_structured_answer_that_is_not_json_is_rejected():
    with pytest.raises(LlmUnavailable):
        parse_structured("Sure! Here is your commit message.")


# --- the free-text safety net -------------------------------------------------


def test_chatty_preamble_is_thrown_away():
    raw = "Yeah, sure! Here is your commit message: [feature] added revenue chart to main page dashboard"
    assert parse_free_text(raw).message == "[feature] added revenue chart to main page dashboard"


def test_preamble_on_its_own_line_is_thrown_away():
    raw = "Sure thing! Here you go:\n\n[refactor,bugfix] added builder schema and fixed main tab not opening\n\nLet me know if you want another."
    assert parse_free_text(raw).message == "[refactor,bugfix] added builder schema and fixed main tab not opening"


def test_reasoning_block_is_thrown_away():
    raw = "<think>The user changed two files, so probably a bugfix.</think>\n[bugfix] fixed the off-by-one in the parser"
    assert parse_free_text(raw).message == "[bugfix] fixed the off-by-one in the parser"


def test_unterminated_reasoning_block_is_thrown_away():
    raw = "Okay, thinking about this…</think>[docs] documented the daemon"
    assert parse_free_text(raw).message == "[docs] documented the daemon"


def test_json_in_a_free_text_answer_is_still_understood():
    assert (
        parse_free_text('Here you go:\n{"categories":["test"],"description":"covered the parser"}').message == "[test] covered the parser"
    )


def test_quotes_and_fences_are_stripped():
    assert parse_free_text('```\n"[bugfix]   fixed  the  thing."\n```').message == "[bugfix] fixed the thing"


def test_a_bare_sentence_gets_a_category():
    assert parse_free_text("updated the readme").message == "[chore] updated the readme"


def test_only_reasoning_is_rejected():
    with pytest.raises(LlmUnavailable):
        parse_free_text("<think>hmm, I am not sure what changed here</think>")


# --- shared assembly ----------------------------------------------------------


def test_duplicates_are_dropped_but_the_list_is_not_truncated():
    assert build(["feature", "feature", "bugfix", "docs", "test"], "did a lot") == "[feature,bugfix,docs,test] did a lot"


def test_every_category_may_apply_at_once():
    assert build(list(CATEGORIES), "did everything").count(",") == len(CATEGORIES) - 1


def test_long_descriptions_are_truncated_at_a_word_boundary():
    """Derived from the constant on purpose — the cap is a tunable, not a fact."""
    message = build(["docs"], "word " * (MAX_DESCRIPTION_CHARS // 4))
    description = message.split("] ", 1)[1]

    assert len(description) <= MAX_DESCRIPTION_CHARS + 1  # + the ellipsis
    assert message.endswith("…")
    assert description.removesuffix("…").endswith("word")  # cut between words, not inside one


def test_strip_reasoning_leaves_plain_text_alone():
    assert strip_reasoning("[feature] added a thing") == "[feature] added a thing"


# --- an answer that comes back unusable is asked for once more -------------------


def _stub(monkeypatch, *contents, finish="stop"):
    from suwgit import llm

    replies = list(contents)
    calls = []

    def post(cfg, payload):
        calls.append(payload)
        return {"choices": [{"message": {"content": replies.pop(0)}, "finish_reason": finish}], "usage": {"completion_tokens": 3000}}

    monkeypatch.setattr(llm, "_post", post)
    return calls


def _ask(b2b=False):
    from pathlib import Path

    from suwgit import gitops, llm
    from suwgit.config import LlmConfig

    tree = gitops.WorkingTree(root=Path("/r"), status=" M a", stat="", diff="")
    return llm.suggest_commit_message(LlmConfig(base_url="http://x", model="m"), tree, b2b=b2b)


def test_a_cut_off_answer_is_asked_for_again(monkeypatch):
    calls = _stub(
        monkeypatch,
        '{\n  "categories": ["feature"],\n  "description": "dedupl',
        '{"categories": ["feature"], "description": "deduplicated work"}',
    )
    assert _ask().message == "[feature] deduplicated work"
    assert len(calls) == 2


def test_an_incomplete_b2b_answer_is_asked_for_again(monkeypatch):
    calls = _stub(
        monkeypatch,
        '{"categories": ["feature"], "description": "x", "summary": "", "work_scope": ["did x"]}',
        '{"categories": ["feature"], "description": "x", "summary": "Did x.", "work_scope": ["did x"]}',
    )
    assert _ask(b2b=True).summary == "Did x."
    assert len(calls) == 2


def test_two_unusable_answers_give_up_and_say_so(monkeypatch):
    from suwgit.llm import AnswerUnusable

    calls = _stub(monkeypatch, '{"categories": ["feature"], "descr', '{"categories": ["feature"], "descr', finish="length")
    with pytest.raises(AnswerUnusable, match="cut off after 3000 tokens.*asked 2 times"):
        _ask()
    assert len(calls) == 2


def test_an_unreachable_server_is_not_asked_again(monkeypatch):
    from suwgit import llm

    calls = []

    def down(cfg, payload):
        calls.append(payload)
        raise LlmUnavailable("cannot reach http://x")

    monkeypatch.setattr(llm, "_post", down)
    with pytest.raises(LlmUnavailable, match="cannot reach"):
        _ask()
    assert len(calls) == 1


def test_broken_json_is_never_committed_as_prose(monkeypatch):
    from suwgit.llm import AnswerUnusable

    _stub(monkeypatch, '{"categories": ["feature"], "description": "dedupl', '```json\n{"categories": ["feature"], "descr')
    with pytest.raises(AnswerUnusable):
        _ask()


def test_a_hint_reaches_the_model_after_the_diff(monkeypatch):
    from pathlib import Path

    from suwgit import gitops, llm
    from suwgit.config import LlmConfig

    calls = _stub(monkeypatch, '{"categories": ["feature"], "description": "first half of the export"}')
    tree = gitops.WorkingTree(root=Path("/r"), status=" M a", stat="", diff="+x")
    llm.suggest_commit_message(LlmConfig(base_url="http://x", model="m"), tree, hint="say it is only the first half")

    user = calls[0]["messages"][-1]["content"]
    assert user.index("+x") < user.index("say it is only the first half")


def test_no_hint_no_extra_words(monkeypatch):
    calls = _stub(monkeypatch, '{"categories": ["feature"], "description": "x"}')
    _ask()
    assert "instructions" not in calls[0]["messages"][-1]["content"]
