from __future__ import annotations

import json
import time
import traceback
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass
class RepairIssue:
    """A single issue detected by a validator."""
    severity: str
    message: str
    category: str
    fix_suggestion: str = ""
    raw_context: dict = field(default_factory=dict)


@dataclass
class RepairAttempt:
    """Record of one repair attempt."""
    attempt: int
    issues: List[RepairIssue]
    fix_applied: str
    duration_ms: float
    success: bool


@dataclass
class RepairTrace:
    """Full trace of a repair cycle for experience learning."""
    task_name: str
    context_snapshot: dict
    total_attempts: int
    final_success: bool
    attempts: List[RepairAttempt]
    started_at: float
    ended_at: float

    @property
    def duration_ms(self) -> float:
        return (self.ended_at - self.started_at) * 1000


class Validator(Protocol):
    """A callable that inspects a result and returns a list of issues."""
    async def __call__(self, result: Any, context: dict) -> List[RepairIssue]:
        ...


class RepairExperienceStore:
    """Structured memory for past repair experiences."""

    def __init__(self, agent=None):
        self._agent = agent
        self._experiences: dict = {}

    async def record(self, trace: RepairTrace) -> None:
        if not trace.attempts:
            return
        last = trace.attempts[-1]
        if not last.success:
            return
        for attempt in trace.attempts:
            if not attempt.issues:
                continue
            key = self._experience_key(trace.task_name, attempt.issues)
            entry = {
                "task_name": trace.task_name,
                "context": {
                    k: v for k, v in trace.context_snapshot.items()
                    if isinstance(v, (str, int, float, bool))
                },
                "issue_categories": [i.category for i in attempt.issues],
                "issue_messages": [i.message for i in attempt.issues],
                "fix_applied": attempt.fix_applied,
                "duration_ms": attempt.duration_ms,
                "success": attempt.success,
                "timestamp": time.time(),
            }
            store = self._experiences.setdefault(key, [])
            store.append(entry)
            if len(store) > 3:
                store.pop(0)
        if self._agent and hasattr(self._agent, "memory_engine"):
            try:
                summary = {
                    "type": "repair_experience",
                    "task": trace.task_name,
                    "final_success": trace.final_success,
                    "attempts": len(trace.attempts),
                    "total_duration_ms": trace.duration_ms,
                }
                await self._agent.memory_engine.add_workflow_pattern(summary)
            except Exception as exc:
                logger.debug(f"Repair experience persistence skipped: {exc}")

    async def query(self, task_name: str, issues: List[RepairIssue]) -> Optional[str]:
        key = self._experience_key(task_name, issues)
        entries = self._experiences.get(key, [])
        if not entries:
            return None
        for entry in reversed(entries):
            if entry.get("success"):
                return entry.get("fix_applied")
        return None

    def _experience_key(self, task_name: str, issues: List[RepairIssue]) -> str:
        categories = sorted({i.category for i in issues})
        return f"{task_name}::{''.join(categories)}"


class RepairHook:
    """Enactive-AI self-healing engine.

    Orchestrates: execute -> validate -> detect issues -> generate fix -> apply -> re-execute.
    """

    LIGHTWEIGHT_MODELS = {"gpt-4o-mini", "gpt-3.5-turbo", "gemini-2.0-flash-lite", "qwen2.5-coder-7b", "llama-3.1-8b"}

    def __init__(self, agent=None, llm_client=None, model: str = None, max_retries: int = None):
        self._agent = agent
        self._client = llm_client or (agent.client if agent else None)
        self._model = model or (agent.model if agent else "gpt-4o-mini")
        self._extra_params = getattr(agent, "extra_params", {}) if agent else {}
        self.experience_store = RepairExperienceStore(agent)
        self.max_attempts = max_retries or 3
        # Auto-detect lightweight mode: skip LLM-based fix, reduce retries
        model_lower = (self._model or "").lower()
        self._lightweight = any(m in model_lower for m in self.LIGHTWEIGHT_MODELS)
        if self._lightweight and max_retries is None:
            self.max_attempts = min(self.max_attempts, 2)

    def execute(self, func: Callable[[], Any]) -> Any:
        """Legacy synchronous retry helper."""
        last_error = None
        for _attempt in range(1, self.max_attempts + 1):
            try:
                return func()
            except Exception as exc:
                last_error = exc
                if _attempt >= self.max_attempts:
                    raise
                time.sleep(0)
        if last_error:
            raise last_error

    async def repair(
        self,
        task: Callable[[], Any],
        *,
        task_name: str = "unknown_task",
        context: dict = None,
        validators: List[Validator] = None,
        max_attempts: int = None,
    ) -> dict:
        if max_attempts is None:
            max_attempts = self.max_attempts
        context = context or {}
        validators = validators or []
        started_at = time.time()
        traces: List[RepairAttempt] = []

        for attempt_num in range(1, max_attempts + 1):
            attempt_start = time.time()
            try:
                result = await task()
            except Exception as exc:
                issues = [
                    RepairIssue(
                        severity="error",
                        message=f"Task execution raised: {exc}",
                        category="execution_error",
                        fix_suggestion=f"Fix the runtime error: {exc}",
                        raw_context={"exception": str(exc), "traceback": traceback.format_exc()},
                    )
                ]
                traces.append(RepairAttempt(
                    attempt=attempt_num,
                    issues=issues,
                    fix_applied=self._auto_derive_fix(issues, context),
                    duration_ms=(time.time() - attempt_start) * 1000,
                    success=False,
                ))
                break

            all_issues: List[RepairIssue] = []
            for validator in validators:
                try:
                    issues = await validator(result, context) or []
                    all_issues.extend(issues)
                except Exception:
                    pass

            if not all_issues:
                traces.append(RepairAttempt(
                    attempt=attempt_num,
                    issues=[],
                    fix_applied="",
                    duration_ms=(time.time() - attempt_start) * 1000,
                    success=True,
                ))
                trace = RepairTrace(
                    task_name=task_name,
                    context_snapshot=context,
                    total_attempts=attempt_num,
                    final_success=True,
                    attempts=traces,
                    started_at=started_at,
                    ended_at=time.time(),
                )
                await self.experience_store.record(trace)
                return {"status": "success", "result": result, "trace": trace}

            fix = await self._generate_fix(task_name, all_issues, context, result)
            if not fix:
                traces.append(RepairAttempt(
                    attempt=attempt_num,
                    issues=all_issues,
                    fix_applied="No fix generated",
                    duration_ms=(time.time() - attempt_start) * 1000,
                    success=False,
                ))
                break

            traces.append(RepairAttempt(
                attempt=attempt_num,
                issues=all_issues,
                fix_applied=fix,
                duration_ms=(time.time() - attempt_start) * 1000,
                success=False,
            ))

            if attempt_num < max_attempts:
                logger.info(
                    f"[RepairHook:{task_name}] attempt {attempt_num}/{max_attempts} "
                    f"found {len(all_issues)} issue(s). Fix: {fix[:120]}. Retrying..."
                )

        final_result = result if "result" in locals() else None
        trace = RepairTrace(
            task_name=task_name,
            context_snapshot=context,
            total_attempts=len(traces),
            final_success=False,
            attempts=traces,
            started_at=started_at,
            ended_at=time.time(),
        )
        await self.experience_store.record(trace)
        return {"status": "error", "result": final_result, "trace": trace}

    async def _generate_fix(self, task_name, issues, context, current_result):
        # Lightweight: skip LLM-based fix, use rules only
        if self._lightweight:
            return self._auto_derive_fix(issues, context)
        known = await self.experience_store.query(task_name, issues)
        if known:
            return known
        derived = self._auto_derive_fix(issues, context)
        if derived:
            return derived
        if self._client is None:
            return None
        prompt = (
            "You are a self-healing agent. A research skill task failed.\n"
            f"Task: {task_name}\n"
            f"Issues:\n"
        )
        for i, issue in enumerate(issues, 1):
            prompt += f"  {i}. [{issue.severity}] {issue.message} (category: {issue.category})\n"
        prompt += (
            "\nReturn JSON: {\"fix_strategy\": \"...\", \"parameters_update\": {...}}\n"
            "Return ONLY valid JSON.\n"
        )
        try:
            res = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are an expert research tool debugger. Return JSON only."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                **self._extra_params,
            )
            fix = json.loads(res.choices[0].message.content)
            return fix.get("fix_strategy", "") or json.dumps(fix.get("parameters_update", {}), ensure_ascii=False)
        except Exception:
            return None

    def _auto_derive_fix(self, issues, context):
        ec = {i.category for i in issues if i.severity == "error"}
        if "empty_result" in ec:
            return "Broaden query or switch to fallback API/source."
        if "api_error" in ec:
            return "Check connectivity, API key, rate limits, then retry with backoff."
        if "execution_error" in ec:
            return "Fix the reported execution error and retry. Check for syntax errors, missing imports, or environment issues."
        if "integrity" in ec:
            return "Output file issue detected. Verify the output path and ensure the process completed successfully."
        return None


try:
    import builtins

    if not hasattr(builtins, "RepairHook"):
        builtins.RepairHook = RepairHook
except Exception:
    pass


async def validate_not_empty(result, context=None):
    if result is None:
        return [RepairIssue(severity="error", message="Result is None", category="empty_result")]
    if isinstance(result, (list, tuple)) and len(result) == 0:
        return [RepairIssue(severity="error", message="Result list is empty", category="empty_result")]
    if isinstance(result, dict) and not result:
        return [RepairIssue(severity="error", message="Result dict is empty", category="empty_result")]
    if isinstance(result, str) and not result.strip():
        return [RepairIssue(severity="error", message="Result string is empty", category="empty_result")]
    return []


async def validate_has_field(field_name, severity="warning"):
    async def _validate(result, context=None):
        issues = []
        items = result if isinstance(result, list) else [result]
        for idx, item in enumerate(items):
            if isinstance(item, dict) and not item.get(field_name):
                issues.append(RepairIssue(
                    severity=severity,
                    message=f"Item {idx} missing '{field_name}'",
                    category="missing_field",
                    raw_context={"item_index": idx, "field": field_name},
                ))
        return issues
    return _validate


async def validate_min_count(min_count):
    async def _validate(result, context=None):
        if isinstance(result, (list, tuple)) and len(result) < min_count:
            return [RepairIssue(
                severity="warning" if len(result) > 0 else "error",
                message=f"Only {len(result)} results (minimum {min_count})",
                category="quality_low",
                raw_context={"count": len(result), "min_count": min_count},
            )]
        return []
    return _validate


# ── Enhanced validators (v2) ──────────────────────────

async def validate_json_schema(result, schema: dict = None):
    """Validate that a JSON result conforms to a schema (or is at least valid JSON)."""
    issues = []
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError as e:
            return [RepairIssue(
                severity="error", message=f"Result is not valid JSON: {e}",
                category="json_parse_error", fix_suggestion="Fix the JSON syntax and retry.",
            )]
    elif isinstance(result, dict):
        parsed = result
    else:
        return []

    if schema:
        _validate_against_schema(parsed, schema, "", issues)
    return issues


def _validate_against_schema(instance, schema, path, issues):
    """Recursively validate instance against a simple JSON Schema subset."""
    if not schema or not isinstance(schema, dict):
        return

    s_type = schema.get("type")
    if s_type and s_type != "object" and not isinstance(instance, dict):
        return

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for field in required:
        if field not in instance or instance.get(field) is None:
            issues.append(RepairIssue(
                severity="error",
                message=f"Missing required field: {path}.{field}" if path else f"Missing required field: {field}",
                category="missing_required_field",
                fix_suggestion=f"Add the '{field}' field to the output.",
                raw_context={"field": field, "path": path},
            ))

    for field, field_schema in (properties or {}).items():
        if field not in instance:
            continue
        value = instance[field]
        expected = field_schema.get("type", "")
        if expected == "array" and not isinstance(value, list):
            issues.append(RepairIssue(
                severity="error",
                message=f"Field {path}.{field} should be an array, got {type(value).__name__}",
                category="type_mismatch",
                fix_suggestion=f"Ensure {path}.{field} is a JSON array.",
            ))
        elif expected == "number" and not isinstance(value, (int, float)):
            issues.append(RepairIssue(
                severity="warning",
                message=f"Field {path}.{field} should be a number",
                category="type_mismatch",
            ))
        elif expected == "string" and not isinstance(value, str):
            issues.append(RepairIssue(
                severity="warning",
                message=f"Field {path}.{field} should be a string",
                category="type_mismatch",
            ))

        # Check nested properties
        if isinstance(value, dict) and isinstance(field_schema, dict) and "properties" in field_schema:
            _validate_against_schema(value, field_schema, f"{path}.{field}" if path else field, issues)


async def validate_exit_code(result, expected_code=0):
    """Validate shell command exit code."""
    if isinstance(result, dict) and "exit_code" in result:
        code = result.get("exit_code", -1)
        if code != expected_code:
            stderr = str(result.get("stderr", ""))[:300]
            return [RepairIssue(
                severity="error" if code != 0 else "warning",
                message=f"Command exited with code {code}: {stderr}",
                category="non_zero_exit",
                fix_suggestion="Read the error output, identify the root cause, and fix the issue.",
                raw_context={"exit_code": code, "stderr": stderr},
            )]
    return []


async def validate_code_quality(result):
    """Validate code output: check for lint/test/build failures in result."""
    issues = []
    if not isinstance(result, dict):
        return issues

    # Check for embedded quality gate results
    gates = result.get("quality_gates") or result.get("verification") or {}
    if isinstance(gates, dict):
        if gates.get("lint") is False:
            issues.append(RepairIssue(
                severity="warning", message="Lint check failed",
                category="lint_failure",
                fix_suggestion="Run the linter, review errors, and fix formatting/syntax issues.",
            ))
        if gates.get("typecheck") is False:
            issues.append(RepairIssue(
                severity="error", message="Type check failed",
                category="typecheck_failure",
                fix_suggestion="Run tsc --noEmit or mypy, fix type errors.",
            ))
        if gates.get("test") is False:
            issues.append(RepairIssue(
                severity="error", message="Tests failed after change",
                category="test_failure",
                fix_suggestion="Read test failure output, find the root cause, and fix. Do not guess.",
            ))

    # Check stdout for known failure patterns
    stdout = str(result.get("stdout", ""))
    if "FAILED" in stdout or "FAIL" in stdout.splitlines():
        issues.append(RepairIssue(
            severity="warning", message="Test failure detected in output",
            category="test_output_failure",
            fix_suggestion="Review the test failures above and fix the code.",
        ))
    if "error TS" in stdout or "TypeError" in stdout or "SyntaxError" in stdout:
        issues.append(RepairIssue(
            severity="error", message="Compilation/type error in output",
            category="compile_error",
            fix_suggestion="Fix the reported compilation error before proceeding.",
        ))

    return issues


async def validate_content_quality(result, min_length=20):
    """Validate that textual output is substantive (not just boilerplate)."""
    issues = []
    content = ""
    if isinstance(result, dict):
        content = str(result.get("content", result.get("answer", result.get("stdout", ""))))
    elif isinstance(result, str):
        content = result

    if not content.strip():
        return [RepairIssue(
            severity="error", message="Output content is empty",
            category="empty_content",
            fix_suggestion="The tool produced no output. Check inputs and retry.",
        )]

    if len(content.strip()) < min_length:
        return [RepairIssue(
            severity="warning",
            message=f"Output is very short ({len(content)} chars) — may be incomplete",
            category="short_output",
            fix_suggestion="Check for truncation or early termination.",
        )]

    # Detect common error boilerplate in otherwise "successful" results
    error_markers = ["Traceback (most recent call last)", "Error: ENOENT", "command not found",
                     "ModuleNotFoundError", "ImportError", "Cannot find module"]
    for marker in error_markers:
        if marker in content:
            return [RepairIssue(
                severity="error",
                message=f"Error detected in output: '{marker}'",
                category="hidden_error",
                fix_suggestion="The tool appears to have succeeded but the output contains an error. Fix the underlying issue.",
            )]

    return issues

