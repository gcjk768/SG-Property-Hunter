"""Bounded `claude -p` calls.

Never two calls at once; per run and per day caps through the claude bucket;
CLAUDE_CODE_MAX_RETRIES from config; --fallback-model; usage limit detection. An Opus or Sonnet
limit is retried once on the fallback model; a session or weekly limit raises ClaudeUnavailable
so the caller posts the deterministic version.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .ratelimit import BudgetExceeded, RateLimiter

_ONE_AT_A_TIME = threading.Lock()
_LIMIT_RE = re.compile(r"hit your (session|weekly|opus|sonnet) limit", re.I)
_RESET_RE = re.compile(r"resets?\s+(?:at\s+)?([^\n.\"]{1,60})", re.I)


class ClaudeUnavailable(Exception):
    """Claude cannot be used now (budget, usage limit, auth, timeout). Use the fallback."""

    def __init__(self, reason: str, kind: str = "error", reset: str = ""):
        super().__init__(reason)
        self.reason, self.kind, self.reset = reason, kind, reset


@dataclass
class ClaudeResult:
    structured: dict | None
    text: str
    cost_usd: float | None
    model_used: str
    raw: dict = field(default_factory=dict)
    num_turns: int = 0


def detect_usage_limit(text: str) -> tuple[str, str] | None:
    """Return (kind, reset) when the CLI output reports a usage limit."""
    m = _LIMIT_RE.search(text or "")
    if not m:
        return None
    kind = m.group(1).lower()
    reset = ""
    r = _RESET_RE.search(text[m.end():] if text else "")
    if r:
        reset = r.group(1).strip()
    return kind, reset


def parse_cli_output(stdout: str) -> dict:
    """The CLI prints one JSON object with --output-format json; tolerate log lines before it."""
    stdout = (stdout or "").strip()
    if not stdout:
        raise ValueError("empty output")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    start = stdout.find("{")
    if start >= 0:
        return json.loads(stdout[start:])
    raise ValueError("no JSON object in output")


def extract_structured(data: dict) -> dict | None:
    if isinstance(data.get("structured_output"), dict):
        return data["structured_output"]
    result = data.get("result")
    if isinstance(result, str):
        text = result.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


class ClaudeRunner:
    def __init__(self, settings: Settings, limiter: RateLimiter,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run, journal=None):
        self.journal = journal or (lambda kind, text, data=None: None)
        self.settings = settings
        self.cfg = settings.claude
        self.limiter = limiter
        self._run = run
        self._drop_permission_prompts = False

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["CLAUDE_CODE_MAX_RETRIES"] = str(self.cfg.max_retries)
        env["DISABLE_AUTOUPDATER"] = "1"
        secrets = self.settings.secrets
        if self.cfg.auth == "oauth":
            env.pop("ANTHROPIC_API_KEY", None)
            if secrets.claude_code_oauth_token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = secrets.claude_code_oauth_token
        else:
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
            if secrets.anthropic_api_key:
                env["ANTHROPIC_API_KEY"] = secrets.anthropic_api_key
        return env

    def build_cmd(self, *, brief: str, system_file: Path, schema: dict, allowed_tools: list[str],
                  disallowed_tools: list[str], max_turns: int, max_budget_usd: float | None,
                  model: str, bare: bool = False) -> list[str]:
        cmd = [self.cfg.binary, "-p", brief,
               "--append-system-prompt-file", str(system_file),
               "--output-format", "json",
               "--json-schema", json.dumps(schema)]
        if allowed_tools:
            cmd += ["--allowedTools", ",".join(allowed_tools)]
        if disallowed_tools:
            cmd += ["--disallowedTools", ",".join(disallowed_tools)]
        cmd += ["--permission-mode", "dontAsk"]
        if not self._drop_permission_prompts:
            cmd += ["--permission-prompts", "none"]
        cmd += ["--strict-mcp-config", "--no-session-persistence",
                "--max-turns", str(max_turns), "--model", model,
                "--fallback-model", self.cfg.fallback_model]
        if self.cfg.auth == "apikey":
            if max_budget_usd:
                cmd += ["--max-budget-usd", f"{max_budget_usd:.2f}"]
            if bare:
                cmd.append("--bare")
        return cmd

    def call(self, *, label: str, brief: str, stdin_text: str, system_file: Path, schema: dict,
             allowed_tools: list[str] | None = None, disallowed_tools: list[str] | None = None,
             max_turns: int, timeout: int, max_budget_usd: float | None = None,
             bare: bool = False) -> ClaudeResult:
        model = self.cfg.model
        retried_on_fallback = False
        while True:
            try:
                self.limiter.acquire("claude")
            except BudgetExceeded as exc:
                raise ClaudeUnavailable(f"Claude call budget reached ({exc.scope})", "budget") from exc
            cmd = self.build_cmd(brief=brief, system_file=system_file, schema=schema,
                                 allowed_tools=allowed_tools or [], disallowed_tools=disallowed_tools or [],
                                 max_turns=max_turns, max_budget_usd=max_budget_usd, model=model, bare=bare)
            with _ONE_AT_A_TIME:
                try:
                    proc = self._run(cmd, input=stdin_text, capture_output=True, text=True,
                                     timeout=timeout, env=self._env(), cwd=str(self.settings.base_dir))
                except subprocess.TimeoutExpired as exc:
                    self.limiter.log("claude", label, method="EXEC", url=f"claude:{label}", note="timeout")
                    raise ClaudeUnavailable(f"{label} timed out after {timeout} seconds", "timeout") from exc
                except FileNotFoundError as exc:
                    raise ClaudeUnavailable(f"Claude binary not found: {self.cfg.binary}", "missing") from exc
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if (not self._drop_permission_prompts and proc.returncode != 0
                    and re.search(r"(unknown|unrecognized) option.*permission-prompts", combined, re.I)):
                self._drop_permission_prompts = True
                continue
            limit = detect_usage_limit(combined)
            if limit:
                kind, reset = limit
                self.limiter.log("claude", label, method="EXEC", url=f"claude:{label}",
                                 note=f"usage limit {kind} {reset}")
                if kind in ("opus", "sonnet") and not retried_on_fallback:
                    retried_on_fallback = True
                    model = self.cfg.fallback_model
                    continue
                self.journal("claude_limit", f"{label} hit the {kind} limit, resets {reset}", None)
                raise ClaudeUnavailable(f"{kind} usage limit reached", kind, reset)
            try:
                data = parse_cli_output(proc.stdout)
            except ValueError as exc:
                self.limiter.log("claude", label, method="EXEC", url=f"claude:{label}",
                                 note=f"unparsable exit {proc.returncode}")
                raise ClaudeUnavailable(f"{label}: could not parse CLI output (exit {proc.returncode})") from exc
            cost = data.get("total_cost_usd")
            self.limiter.log("claude", label, method="EXEC", url=f"claude:{label}",
                             status=proc.returncode, note=f"model {model} cost {cost}")
            if data.get("is_error") or data.get("subtype") not in (None, "success"):
                text = str(data.get("result", ""))
                limit = detect_usage_limit(text)
                if limit:
                    kind, reset = limit
                    if kind in ("opus", "sonnet") and not retried_on_fallback:
                        retried_on_fallback = True
                        model = self.cfg.fallback_model
                        continue
                    raise ClaudeUnavailable(f"{kind} usage limit reached", kind, reset)
                raise ClaudeUnavailable(f"{label}: CLI reported {data.get('subtype')}: {text[:200]}")
            used = ",".join((data.get("modelUsage") or {}).keys()) or model
            self.journal("claude_call", f"{label} finished in {data.get('num_turns')} turns",
                         {"cost_usd": cost})
            return ClaudeResult(extract_structured(data), str(data.get("result", "")), cost, used, data,
                                int(data.get("num_turns") or 0))
