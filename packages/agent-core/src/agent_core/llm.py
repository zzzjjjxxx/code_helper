from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping

from .workflow.patch_model import PatchProposal


@dataclass(slots=True)
class LLMPlanResult:
    proposal: PatchProposal
    summary: str
    rationale: str
    model: str
    provider: str = 'openai'


@dataclass(slots=True)
class ReActDecision:
    action: str
    summary: str = ''
    rationale: str = ''
    files_to_read: list[str] = field(default_factory=list)
    proposal: PatchProposal | None = None
    model: str = ''
    provider: str = 'openai'


@dataclass(slots=True)
class ReviewDecision:
    action: str
    summary: str = ''
    rationale: str = ''
    files_to_read: list[str] = field(default_factory=list)
    model: str = ''
    provider: str = 'openai'


class OpenAIPlanner:
    def __init__(self, config: Mapping[str, Any] | None = None):
        data = dict(config or {})
        self.provider = str(data.get('provider', 'openai')).strip().lower()
        self.base_url = str(data.get('base_url', 'https://api.openai.com/v1')).strip().rstrip('/')
        self.api_key_env = str(data.get('api_key_env', 'OPENAI_API_KEY')).strip()
        self.model = str(data.get('model', '')).strip()
        self.temperature = float(data.get('temperature', 0.2))
        self.max_output_tokens = int(data.get('max_output_tokens', 2048))
        self.timeout_seconds = int(data.get('timeout_seconds', 120))

    @property
    def api_key(self) -> str:
        return os.getenv(self.api_key_env, '').strip()

    @property
    def enabled(self) -> bool:
        return self.provider == 'openai' and bool(self.api_key) and bool(self.model) and self.model != 'your-model-name'

    @property
    def disabled_reason(self) -> str:
        if self.provider != 'openai':
            return f'unsupported provider: {self.provider}'
        if not self.model or self.model == 'your-model-name':
            return 'missing model'
        if not self.api_key:
            return f'missing API key from {self.api_key_env}'
        return 'disabled'

    def propose_patch(
        self,
        *,
        task_title: str,
        task_description: str,
        retrieval_hits: list[dict[str, Any]],
        memory_notes: list[dict[str, Any]],
        file_contexts: list[dict[str, Any]],
        baseline_test: dict[str, Any],
    ) -> LLMPlanResult | None:
        if not self.enabled:
            return None

        prompt_payload = {
            'task_title': task_title,
            'task_description': task_description,
            'retrieval_hits': retrieval_hits[:6],
            'memory_notes': memory_notes[:6],
            'file_contexts': [
                {
                    'path': entry.get('path', ''),
                    'content': str(entry.get('content', ''))[:5000],
                }
                for entry in file_contexts[:8]
            ],
            'baseline_test': baseline_test,
            'instructions': (
                'Return one JSON object only. Choose the smallest safe patch. '
                'The JSON object must include path, old, new, summary, and rationale.'
            ),
        }
        system_prompt = (
            'You are a code-repair planner. Study the task, retrieval hits, memory notes, and file context. '
            'Return a single JSON object with keys path, old, new, summary, and rationale. '
            'path should be a file path relative to the workspace if possible. '
            'old and new must be exact replacement strings. Do not wrap the answer in markdown.'
        )
        raw_text = self._invoke_responses_api(
            instructions=system_prompt,
            input_text=json.dumps(prompt_payload, ensure_ascii=False, indent=2),
        )
        if not raw_text:
            return None

        payload = _extract_json_object(raw_text)
        if not isinstance(payload, dict):
            return None

        path = str(payload.get('path', '')).strip()
        old = str(payload.get('old', ''))
        new = str(payload.get('new', ''))
        summary = str(payload.get('summary', '')).strip()
        rationale = str(payload.get('rationale', '')).strip()
        if not path or not old or not new:
            return None

        return LLMPlanResult(
            proposal=PatchProposal(path=path, old=old, new=new),
            summary=summary or 'Patch verified successfully.',
            rationale=rationale,
            model=self.model,
            provider=self.provider,
        )

    def plan_next_step(self, *, state: Mapping[str, Any]) -> ReActDecision | None:
        if not self.enabled:
            return None

        system_prompt = (
            'You are the planner in a ReAct code-repair loop. '
            'Choose exactly one action: read_more, patch, or finish. '
            'If you need more code context, return read_more with files_to_read. '
            'If you know the fix, return patch with a single patch proposal. '
            'If the task is complete or no safe action exists, return finish. '
            'Consider any review_feedback in the state and use it to choose a better branch. '
            'Return one JSON object only with keys action, summary, rationale, files_to_read, and patch.'
        )
        raw_text = self._invoke_responses_api(
            instructions=system_prompt,
            input_text=json.dumps(state, ensure_ascii=False, indent=2),
        )
        if not raw_text:
            return None

        payload = _extract_json_object(raw_text)
        if not isinstance(payload, dict):
            return None

        action = str(payload.get('action', '')).strip().lower()
        if action not in {'read_more', 'patch', 'finish'}:
            return None

        files_to_read: list[str] = []
        for value in payload.get('files_to_read', []) or []:
            if isinstance(value, str) and value.strip():
                files_to_read.append(value.strip())

        proposal_payload = payload.get('patch')
        proposal: PatchProposal | None = None
        if isinstance(proposal_payload, dict):
            path = str(proposal_payload.get('path', '')).strip()
            old = str(proposal_payload.get('old', ''))
            new = str(proposal_payload.get('new', ''))
            if path and old and new:
                proposal = PatchProposal(path=path, old=old, new=new)

        return ReActDecision(
            action=action,
            summary=str(payload.get('summary', '')).strip(),
            rationale=str(payload.get('rationale', '')).strip(),
            files_to_read=files_to_read,
            proposal=proposal,
            model=self.model,
            provider=self.provider,
        )

    def review_result(self, *, state: Mapping[str, Any]) -> ReviewDecision | None:
        if not self.enabled:
            return None

        system_prompt = (
            'You are the reviewer in a collaborative code-repair loop. '
            'Judge whether the current branch should be approved, revised, or rejected. '
            'Return approve when the patch is safe and tests passed. '
            'Return revise when the branch needs more context or a different patch. '
            'Return reject when the branch is unsafe or fundamentally wrong. '
            'Return one JSON object only with keys action, summary, rationale, files_to_read.'
        )
        raw_text = self._invoke_responses_api(
            instructions=system_prompt,
            input_text=json.dumps(state, ensure_ascii=False, indent=2),
        )
        if not raw_text:
            return None

        payload = _extract_json_object(raw_text)
        if not isinstance(payload, dict):
            return None

        action = str(payload.get('action', '')).strip().lower()
        if action not in {'approve', 'revise', 'reject'}:
            return None

        files_to_read: list[str] = []
        for value in payload.get('files_to_read', []) or []:
            if isinstance(value, str) and value.strip():
                files_to_read.append(value.strip())

        return ReviewDecision(
            action=action,
            summary=str(payload.get('summary', '')).strip(),
            rationale=str(payload.get('rationale', '')).strip(),
            files_to_read=files_to_read,
            model=self.model,
            provider=self.provider,
        )

    def _invoke_responses_api(self, *, instructions: str, input_text: str) -> str:
        payload = {
            'model': self.model,
            'instructions': instructions,
            'input': input_text,
            'temperature': self.temperature,
            'max_output_tokens': self.max_output_tokens,
        }
        request = urllib.request.Request(
            f'{self.base_url}/responses',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'OpenAI request failed: {exc.code} {detail}') from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f'OpenAI request failed: {exc.reason}') from exc

        if isinstance(data, dict) and isinstance(data.get('error'), dict):
            error = data['error']
            message = error.get('message', 'Unknown error') if isinstance(error, dict) else 'Unknown error'
            raise RuntimeError(f'OpenAI request failed: {message}')

        return _response_text(data)


def _response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ''

    output_text = payload.get('output_text')
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: list[str] = []
    output = payload.get('output', [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get('content', [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                for key in ('text', 'output_text'):
                    value = block.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
    return '\n'.join(parts).strip()


def _extract_json_object(text: str) -> Any:
    cleaned = _strip_code_fences(text.strip())
    candidate = _first_json_object(cleaned)
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith('```'):
        stripped = re.sub(r'^```(?:json)?\s*', '', stripped, flags=re.IGNORECASE)
        stripped = re.sub(r'\s*```$', '', stripped)
    return stripped.strip()


def _first_json_object(text: str) -> str | None:
    start = text.find('{')
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None
