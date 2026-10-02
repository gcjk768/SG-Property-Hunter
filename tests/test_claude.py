import json
import subprocess

import pytest

from propbot.claude import ClaudeRunner, ClaudeUnavailable, detect_usage_limit, extract_structured, parse_cli_output


def proc(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


OK = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": '{"a":1}',
                 "structured_output": {"a": 1}, "total_cost_usd": 0.01, "modelUsage": {"claude-sonnet": {}}})


def test_parse_and_extract():
    data = parse_cli_output("log line\n" + OK)
    assert extract_structured(data) == {"a": 1}
    assert extract_structured({"result": "```json\n{\"b\": 2}\n```"}) == {"b": 2}


def test_usage_limit_detection():
    assert detect_usage_limit("Claude AI usage limit: You've hit your weekly limit · resets Oct 6, 9am") == ("weekly", "Oct 6, 9am")
    assert detect_usage_limit("You've hit your Sonnet limit. resets 3pm")[0] == "sonnet"
    assert detect_usage_limit("all good") is None


def make(settings, limiter, outputs, seen):
    def run(cmd, **kw):
        seen.append((cmd, kw))
        return outputs.pop(0)
    return ClaudeRunner(settings, limiter, run=run)


def call(r, settings):
    return r.call(label="t", brief="b", stdin_text="{}", system_file=settings.base_dir / "x.md",
                  schema={"type": "object"}, disallowed_tools=settings.claude.no_tools, max_turns=4, timeout=10,
                  max_budget_usd=1.0)


def test_command_shape_oauth(settings, limiter):
    seen = []
    r = make(settings, limiter, [proc(OK)], seen)
    res = call(r, settings)
    cmd, kw = seen[0]
    assert res.structured == {"a": 1}
    assert cmd[:3] == ["claude", "-p", "b"]
    assert "--max-budget-usd" not in cmd and "--bare" not in cmd
    assert cmd[cmd.index("--fallback-model") + 1] == "haiku"
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert kw["env"]["CLAUDE_CODE_MAX_RETRIES"] == "4"
    assert "ANTHROPIC_API_KEY" not in kw["env"]


def test_apikey_adds_budget(settings, limiter):
    settings.claude.auth = "apikey"
    seen = []
    call(make(settings, limiter, [proc(OK)], seen), settings)
    assert "--max-budget-usd" in seen[0][0]


def test_sonnet_limit_retries_once_on_fallback(settings, limiter):
    seen = []
    r = make(settings, limiter, [proc("", "You've hit your Sonnet limit · resets 4pm", 1), proc(OK)], seen)
    res = call(r, settings)
    assert res.structured == {"a": 1}
    assert seen[1][0][seen[1][0].index("--model") + 1] == "haiku"


def test_weekly_limit_falls_back_without_retry(settings, limiter):
    seen = []
    r = make(settings, limiter, [proc("", "You've hit your weekly limit · resets Oct 6", 1)], seen)
    with pytest.raises(ClaudeUnavailable) as exc:
        call(r, settings)
    assert exc.value.kind == "weekly" and len(seen) == 1


def test_permission_prompts_flag_dropped_when_rejected(settings, limiter):
    seen = []
    r = make(settings, limiter, [proc("", "error: unknown option '--permission-prompts'", 1), proc(OK)], seen)
    call(r, settings)
    assert "--permission-prompts" in seen[0][0] and "--permission-prompts" not in seen[1][0]


def test_budget_cap(settings, limiter):
    for _ in range(10):
        limiter.acquire("claude")
    with pytest.raises(ClaudeUnavailable) as exc:
        call(make(settings, limiter, [], []), settings)
    assert exc.value.kind == "budget"
