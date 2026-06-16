import os
import sys
import json
import asyncio
import urllib.request
import urllib.parse
import hashlib
import re
import shutil
import subprocess
import importlib.metadata
import uuid
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import aiofiles

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from runtime_engine import BaseTool
from evolution import EvolutionError
from repair_hook import RepairHook
from skills.unified_validators import validate_not_empty as _validate_not_empty
from skills.research.research_validators import validate_paper_count_in_range as _validate_paper_count


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def ok(**kwargs) -> str:
    data = {"status": "success"}
    data.update(kwargs)
    return to_json(data)


def err(message: str, completed: bool = False, **kwargs) -> str:
    data = {"status": "error", "message": str(message), "completed": completed}
    data.update(kwargs)
    return to_json(data)


def denied(message: str = "Execution denied by user.", completed: bool = True) -> str:
    return to_json({"status": "denied", "message": message, "completed": completed})


def normalize_tool_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name or "")


def safe_decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip() if data else ""


class SearchMemoryTool(BaseTool):
    def __init__(self, agent):
        self.name = "search_long_term_memory"
        self.description = "Retrieve historical data and long-term memory."
        self.parameters_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
        self.agent = agent

    async def execute(self, query: str, session_id: str = None):
        return await self.agent.execute_memory_search(query, session_id)


class MemorizeFactTool(BaseTool):
    def __init__(self, agent):
        self.name = "memorize_critical_fact"
        self.description = "Commit a critical fact or state change to persistent memory."
        self.parameters_schema = {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}
        self.agent = agent

    async def execute(self, fact: str):
        return await self.agent.execute_memorize_fact(fact)


class AmendMemoryTool(BaseTool):
    def __init__(self, agent):
        self.name = "amend_or_forget_memory"
        self.description = "Erase or correct erroneous facts from long-term memory databases."
        self.parameters_schema = {"type": "object", "properties": {"target_fact": {"type": "string"}}, "required": ["target_fact"]}
        self.agent = agent

    async def execute(self, target_fact: str):
        return await self.agent.execute_amend_memory(target_fact)


class UpdateCoreMemoryTool(BaseTool):
    def __init__(self, agent):
        self.name = "update_core_memory"
        self.description = "Update the Core Memory scratchpad with dynamic short-term states."
        self.parameters_schema = {"type": "object", "properties": {"updates": {"type": "string"}}, "required": ["updates"]}
        self.agent = agent

    async def execute(self, updates: str, session_id: str = None):
        return await self.agent.execute_update_core(session_id, updates)


class UpdateClinicalRuleTool(BaseTool):
    def __init__(self, agent):
        self.name = "update_clinical_rule"
        self.description = "Add a new mandatory guideline to procedural memory."
        self.parameters_schema = {"type": "object", "properties": {"rule": {"type": "string"}}, "required": ["rule"]}
        self.agent = agent

    async def execute(self, rule: str):
        return await self.agent.execute_update_clinical_rule(rule)


class ExecuteSystemCommandTool(BaseTool):
    def __init__(self, agent):
        self.name = "execute_system_command"
        self.description = "Execute a local system command safely."
        self.parameters_schema = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
        self.agent = agent

    async def execute(self, command: str):
        return await self.agent.execute_system_cmd(command)


class WriteAndExecuteScriptTool(BaseTool):
    def __init__(self, agent):
        self.name = "write_and_execute_script"
        self.description = "Execute Python script in isolated temporary virtual environment."
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "description": {"type": "string"},
                "code": {"type": "string"}
            },
            "required": ["filename", "description", "code"]
        }
        self.agent = agent

    async def execute(self, filename: str, description: str, code: str, session_id: str = None):
        return await self.agent.execute_write_and_run(filename, description, code, session_id)


class RegisterNewToolTool(BaseTool):
    def __init__(self, agent):
        self.name = "register_new_tool"
        self.description = "Register a new or upgraded extension tool."
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "tool_description": {"type": "string"},
                "parameters_schema": {"type": "string"},
                "code": {"type": "string"}
            },
            "required": ["tool_name", "tool_description", "parameters_schema", "code"]
        }
        self.agent = agent

    async def execute(self, tool_name: str, tool_description: str, parameters_schema: str, code: str):
        return await self.agent.execute_register_tool(tool_name, tool_description, parameters_schema, code)


class DelegateToSubagentsTool(BaseTool):
    def __init__(self, agent):
        self.name = "delegate_to_subagents"
        self.description = "Split complex task into multiple parallel subtasks and run them with sub-agents."
        self.parameters_schema = {"type": "object", "properties": {"subtasks": {"type": "array", "items": {"type": "string"}}}, "required": ["subtasks"]}
        self.agent = agent

    async def execute(self, subtasks: List[str], session_id: str = None):
        if self.agent.broadcast_event:
            await self.agent.broadcast_event("subagent_start", {"subtasks": subtasks})
        results = await self.agent.spawn_subagents(subtasks, parent_session_id=session_id or "default")
        if self.agent.broadcast_event:
            await self.agent.broadcast_event("subagent_end", {"results_preview": str(results)[:200]})
        return {"status": "success", "subresults": results}


class DelegateToRemoteAgentTool(BaseTool):
    def __init__(self, agent):
        self.name = "delegate_to_remote_agent"
        self.description = "Delegate a task to a configured remote peer agent through the swarm protocol."
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "peer_id": {"type": "string", "description": "Configured remote peer id."},
                "task_prompt": {"type": "string", "description": "Task to delegate to the remote agent."},
                "scopes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Requested permission scopes for the remote task."
                }
            },
            "required": ["peer_id", "task_prompt"]
        }
        self.agent = agent

    async def execute(self, peer_id: str, task_prompt: str, scopes: List[str] = None, session_id: str = None):
        return await self.agent.delegate_remote_agent(peer_id, task_prompt, session_id=session_id, scopes=scopes or ["chat:delegate"])


class InstallGithubSkillTool(BaseTool):
    def __init__(self, agent):
        self.name = "install_github_skill"
        self.description = "Download and install a new skill from a Git repository URL to expand capabilities."
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "repo_url": {"type": "string"},
                "skill_name": {"type": "string"}
            },
            "required": ["repo_url", "skill_name"]
        }
        self.agent = agent

    async def execute(self, repo_url: str, skill_name: str, session_id: str = None):
        skill_name = normalize_tool_name(skill_name).strip("_")
        if not skill_name:
            return err("Invalid skill_name.", completed=True)
        prompt = f"Install new skill '{skill_name}' from {repo_url}?"
        if hasattr(self.agent, "_request_user_confirmation"):
            confirmed = await self.agent._request_user_confirmation(session_id=session_id or "default", prompt=prompt)
            if not confirmed:
                return denied(completed=True)
        target_dir = self.agent.skills_dir / skill_name
        if target_dir.exists():
            return err("Skill already exists", completed=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", repo_url, str(target_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                await self.agent._load_skills()
                return ok(message=f"Skill {skill_name} installed and loaded.", completed=True)
            return err(safe_decode(stderr) or safe_decode(stdout), completed=True)
        except Exception as e:
            return err(str(e), completed=True)


class SearchSkillMarketTool(BaseTool):
    def __init__(self, agent):
        self.name = "search_skill_market"
        self.description = "Search the global open-source skill market for tools or skills."
        self.parameters_schema = {"type": "object", "properties": {"keyword": {"type": "string"}}, "required": ["keyword"]}
        self.agent = agent

    async def execute(self, keyword: str, session_id: str = None):
        try:
            loop = asyncio.get_running_loop()
            def fetch(url: str):
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.loads(response.read().decode("utf-8"))
            queries = [f"{keyword} openclaw", f"{keyword} agent skill"]
            items = []
            for q in queries:
                url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(q)}&sort=stars&order=desc&per_page=5"
                data = await loop.run_in_executor(None, lambda u=url: fetch(u))
                items = data.get("items", [])
                if items:
                    break
            if not items:
                return err(f"No matching skills found for '{keyword}'.")
            results = [
                {
                    "skill_name": item.get("name"),
                    "description": item.get("description"),
                    "repo_url": item.get("clone_url"),
                    "stars": item.get("stargazers_count")
                }
                for item in items
            ]
            return {"status": "success", "message": "Found potential skills in the market.", "skills": results}
        except Exception as e:
            return {"status": "error", "message": f"Search failed: {str(e)}"}


class RunSkillScriptTool(BaseTool):
    def __init__(self, agent):
        self.name = "run_skill_script"
        self.description = "Execute a Python script from an installed skill. args_string must be one valid JSON object string matching the skill parameters."
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string"},
                "args_string": {"type": "string"}
            },
            "required": ["skill_name", "args_string"]
        }
        self.agent = agent

    def _resolve_skill(self, skill_name: str) -> Tuple[Optional[str], Optional[dict]]:
        if not skill_name:
            return None, None
        if skill_name in self.agent.loaded_skills:
            return skill_name, self.agent.loaded_skills[skill_name]
        lowered = skill_name.lower()
        for name, info in self.agent.loaded_skills.items():
            if name.lower() == lowered:
                return name, info
        for name, info in self.agent.loaded_skills.items():
            name_lower = name.lower()
            if name_lower in lowered or lowered in name_lower:
                return name, info
        return None, None

    def _parse_args_object(self, raw: Any) -> Tuple[Optional[dict], Optional[str]]:
        if raw is None:
            return {}, None
        if isinstance(raw, dict):
            return raw, None
        if not isinstance(raw, str):
            return None, "args_string must be a JSON object string."
        value = raw.strip()
        if not value:
            return {}, None
        try:
            data = json.loads(value)
        except Exception as e:
            return None, f"args_string is not valid JSON: {e}"
        if not isinstance(data, dict):
            return None, "args_string must decode to a JSON object."
        return data, None

    def _schema_properties(self, parameters: Any) -> Dict[str, Any]:
        if not isinstance(parameters, dict):
            return {}
        if parameters.get("type") == "object" and isinstance(parameters.get("properties"), dict):
            return parameters.get("properties") or {}
        return {k: v for k, v in parameters.items() if isinstance(v, dict)}

    def _required_params(self, parameters: Any) -> List[str]:
        if not isinstance(parameters, dict):
            return []
        if parameters.get("type") == "object":
            required = parameters.get("required", [])
            return [str(x) for x in required] if isinstance(required, list) else []
        required = []
        for name, spec in parameters.items():
            if isinstance(spec, dict) and spec.get("required") is True:
                required.append(name)
        return required

    def _type_matches(self, value: Any, expected_type: str) -> bool:
        if expected_type in (None, "", "any"):
            return True
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "object":
            return isinstance(value, dict)
        return True

    def _validate_skill_args(self, skill_name: str, skill_info: dict, args_string: Any) -> Tuple[Optional[dict], Optional[str]]:
        args_obj, parse_error = self._parse_args_object(args_string)
        if parse_error:
            return None, parse_error
        parameters = skill_info.get("parameters", {})
        required = self._required_params(parameters)
        properties = self._schema_properties(parameters)
        missing = [name for name in required if name not in args_obj or args_obj.get(name) in ("", None)]
        if missing:
            return None, f"Missing required parameters for skill '{skill_name}': {missing}. Do not call this skill with empty args."
        type_errors = []
        for name, value in args_obj.items():
            spec = properties.get(name)
            if isinstance(spec, dict):
                expected_type = spec.get("type")
                if expected_type and not self._type_matches(value, expected_type):
                    type_errors.append({"name": name, "expected": expected_type, "actual": type(value).__name__})
        if type_errors:
            return None, f"Invalid parameter types for skill '{skill_name}': {type_errors}"
        return args_obj, None

    def _find_entry_script(self, skill_name: str, skill_info: dict) -> Optional[Path]:
        skill_dir = skill_info["dir"]
        scripts_dir = skill_dir / "scripts"
        candidates = []
        if scripts_dir.exists():
            candidates.extend([
                scripts_dir / "main.py",
                scripts_dir / f"{skill_name}.py",
                scripts_dir / f"{skill_name.replace('-', '_')}.py"
            ])
            py_files = sorted(scripts_dir.glob("*.py"))
            candidates.extend(py_files)
        candidates.extend([
            skill_dir / "main.py",
            skill_dir / f"{skill_name}.py",
            skill_dir / f"{skill_name.replace('-', '_')}.py"
        ])
        candidates.extend(sorted(skill_dir.glob("*.py")))
        seen = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                if resolved.exists() and resolved.is_file():
                    return resolved
            except Exception:
                continue
        return None

    def _repair_mojibake_values(self, value: Any) -> Any:
        repair = getattr(self.agent, "_repair_mojibake_text", None)
        if isinstance(value, str) and callable(repair):
            return repair(value)
        if isinstance(value, dict):
            return {key: self._repair_mojibake_values(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._repair_mojibake_values(item) for item in value]
        return value

    def _detect_completed_from_output(self, skill_name: str, stdout_text: str) -> bool:
        text = stdout_text or ""
        markers = [
            "DOWNLOAD_PATH:", "Files saved to:", "File saved:", "Saved to:",
            "下载并处理完成", "下载完成", "最终文件:", "最终文件：", "合并完成:", "处理完成",
            "Task completed", '"completed": true', "'completed': True"
        ]
        if any(marker in text for marker in markers):
            return True
        if skill_name and any(x in skill_name.lower() for x in ["download", "save"]):
            if re.search(r'([a-zA-Z]:[\\/][^\n\r\t<>"]+\.(mp4|mkv|webm|mp3|m4a|wav|flac|pdf|docx|xlsx|pptx))', text, re.I):
                return True
        return False

    def _extract_produces_from_output(self, stdout_text: str) -> dict:
        text = stdout_text or ""
        produces = {}
        paths = []
        for pattern in [
            r"DOWNLOAD_PATH:\s*([^\n\r]+)", r"Files saved to:\s*([^\n\r]+)",
            r"File saved:\s*([^\n\r]+)", r"Saved to:\s*([^\n\r]+)",
            r"最终文件\s*[:：]\s*([^\n\r]+)", r"合并完成\s*[:：]\s*([^\n\r]+)"
        ]:
            for match in re.findall(pattern, text, re.I):
                cleaned = str(match).strip().strip('"').strip("'")
                if cleaned and cleaned not in paths:
                    paths.append(cleaned)
        if not paths:
            for match in re.findall(r'([a-zA-Z]:[\\/][^\n\r\t<>"]+\.(?:mp4|mkv|webm|mp3|m4a|wav|flac|pdf|docx|xlsx|pptx))', text, re.I):
                cleaned = str(match[0] if isinstance(match, tuple) else match).strip().strip('"').strip("'")
                if cleaned and cleaned not in paths:
                    paths.append(cleaned)
        if paths:
            produces["file_path"] = paths[0]
            produces["file_paths"] = paths
        return produces

    def _normalize_script_success_output(self, skill_name: str, stdout_text: str) -> dict:
        completed = self._detect_completed_from_output(skill_name, stdout_text)
        produces = self._extract_produces_from_output(stdout_text)
        response = {
            "status": "success",
            "output": stdout_text,
            "skill_name": skill_name,
            "completed": completed
        }
        if produces:
            response["produces"] = produces
        return response

    async def execute(self, skill_name: str, args_string: str = "", params_json: str = None, session_id: str = "default"):
        session_id = session_id or "default"
        if self.agent.broadcast_event:
            await self.agent.broadcast_event("skill_start", {"skill_name": skill_name, "args": args_string[:200], "session_id": session_id})

        if params_json and not args_string:
            args_string = params_json
        args_obj_for_repair, parse_error = self._parse_args_object(args_string)
        if not parse_error:
            args_string = json.dumps(self._repair_mojibake_values(args_obj_for_repair), ensure_ascii=False)
        resolved_name, skill_info = self._resolve_skill(skill_name)
        if not skill_info:
            return err(f"Skill '{skill_name}' not found.", completed=False)
        args_obj, validation_error = self._validate_skill_args(resolved_name, skill_info, args_string)
        if validation_error:
            return err(validation_error, completed=False)
        if hasattr(self.agent, "check_skill_scopes"):
            allowed, message, required_scopes = self.agent.check_skill_scopes(resolved_name, skill_info, session_id=session_id)
            if not allowed:
                return err(message, completed=True, required_scopes=required_scopes)
        normalized_args_string = json.dumps(args_obj or {}, ensure_ascii=False)
        entry_py = self._find_entry_script(resolved_name, skill_info)
        if not entry_py:
            return err(f"No Python entry script found for skill '{resolved_name}'.", completed=False)
        try:
            async with aiofiles.open(entry_py, "r", encoding="utf-8") as f:
                code_preview = await f.read()
        except Exception:
            code_preview = "Could not read code."
        script_hash = hashlib.sha256(f"skill_{resolved_name}_{str(entry_py)}".encode()).hexdigest()
        prompt = f"Request to execute skill script:\n- Skill: {resolved_name}\n- Arguments: {normalized_args_string[:500]}"
        if hasattr(self.agent, "_request_user_confirmation"):
            confirmed = await self.agent._request_user_confirmation(
                session_id=session_id,
                prompt=prompt,
                script_hash=script_hash,
                code_preview=code_preview
            )
            if not confirmed:
                return denied(completed=False)
        runtime_cfg = getattr(self.agent, "config", {}).get("runtime", {}) if getattr(self.agent, "config", None) else {}
        timeout_sec = int(skill_info.get("timeout_sec") or runtime_cfg.get("skill_script_timeout_sec", 180))
        last_repair_error = {"message": ""}
        proc = None
        _run_repair = None
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            args_list = [normalized_args_string]
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(entry_py),
                *args_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(skill_info["dir"]),
                env=env
            )

            async def _retry_skill_execution():
                """Re-run the skill subprocess for auto-repair retries."""
                env_r = os.environ.copy()
                env_r["PYTHONIOENCODING"] = "utf-8"
                args_list = [normalized_args_string]
                proc_r = await asyncio.create_subprocess_exec(
                    sys.executable, str(entry_py), *args_list,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    cwd=str(skill_info["dir"]), env=env_r
                )
                try:
                    rc = getattr(self.agent, "config", {}).get("runtime", {}) if getattr(self.agent, "config", None) else {}
                    t_sec = int(skill_info.get("timeout_sec") or rc.get("skill_script_timeout_sec", 180))
                    stdout_r, stderr_r = await asyncio.wait_for(proc_r.communicate(), timeout=t_sec)
                    sd = safe_decode(stdout_r)
                    ed = safe_decode(stderr_r)
                    if proc_r.returncode == 0:
                        return self._normalize_script_success_output(resolved_name, sd)
                    last_repair_error["message"] = ed or sd or f"Process exited with code {proc_r.returncode}"
                    return None
                except asyncio.TimeoutError:
                    if proc_r.returncode is None:
                        try: proc_r.kill(); await proc_r.communicate()
                        except Exception: pass
                    last_repair_error["message"] = f"Script execution timed out after {t_sec}s."
                    return None
                except Exception as ex:
                    last_repair_error["message"] = str(ex)
                    return None

            skill_marker_text = f"{resolved_name} {skill_info.get('dir', '')}".lower()
            research_markers = ("research", "paper", "citation", "zotero", "review", "literature", "journal")
            _repair_validators = [_validate_paper_count(1)] if any(marker in skill_marker_text for marker in research_markers) else [_validate_not_empty]

            async def _run_repair(original_message):
                """Run RepairHook auto-repair; returns (result_dict_or_None, repair_trace_dict)."""
                hook = RepairHook(agent=self.agent)
                result = await hook.repair(
                    task=_retry_skill_execution,
                    task_name=resolved_name,
                    context={"error_type": "skill_failure", "message": original_message},
                    validators=_repair_validators,
                    max_attempts=2,
                )
                if result["status"] == "success":
                    repaired = result["result"]
                    if isinstance(repaired, dict) and repaired.get("status") == "success":
                        return repaired, None
                trace = result.get("trace")
                rt = {"attempts": trace.total_attempts, "final_success": trace.final_success, "last_error": last_repair_error["message"]} if trace else {}
                return None, rt

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            stdout_dec = safe_decode(stdout)
            stderr_dec = safe_decode(stderr)
            if proc.returncode == 0:
                response = self._normalize_script_success_output(resolved_name, stdout_dec)
                if self.agent.broadcast_event:
                    await self.agent.broadcast_event("skill_end", {"skill_name": resolved_name, "status": "success", "output_preview": stdout_dec[:200], "session_id": session_id})
                return to_json(response)
            message = stderr_dec or stdout_dec or f"Process exited with code {proc.returncode}"
            repaired_result, repair_trace = await _run_repair(message)
            if repaired_result is not None:
                if self.agent.broadcast_event:
                    await self.agent.broadcast_event("skill_end", {"skill_name": resolved_name, "status": "success", "skill_repair": True, "output_preview": str(repaired_result)[:200], "session_id": session_id})
                return to_json(repaired_result)
            if self.agent.broadcast_event:
                await self.agent.broadcast_event("skill_end", {"skill_name": resolved_name, "status": "error", "message": message[:200], "session_id": session_id})
            return err(message, skill_name=resolved_name, completed=False, repair_trace=repair_trace)
        except asyncio.TimeoutError:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass
            message = f"Script execution timed out after {timeout_sec}s."
            if _run_repair is None:
                repair_trace = {"attempts": 0, "final_success": False, "last_error": message}
                repaired_result = None
            else:
                repaired_result, repair_trace = await _run_repair(message)
            if repaired_result is not None:
                if self.agent.broadcast_event:
                    await self.agent.broadcast_event("skill_end", {"skill_name": resolved_name, "status": "success", "skill_repair": True, "output_preview": str(repaired_result)[:200], "session_id": session_id})
                return to_json(repaired_result)
            if self.agent.broadcast_event:
                await self.agent.broadcast_event("skill_end", {"skill_name": resolved_name, "status": "error", "message": message[:200], "session_id": session_id})
            return err(message, skill_name=resolved_name, completed=False, repair_trace=repair_trace)
        except Exception as e:
            message = str(e)
            if _run_repair is None:
                repair_trace = {"attempts": 0, "final_success": False, "last_error": message}
                repaired_result = None
            else:
                repaired_result, repair_trace = await _run_repair(message)
            if repaired_result is not None:
                if self.agent.broadcast_event:
                    await self.agent.broadcast_event("skill_end", {"skill_name": resolved_name, "status": "success", "skill_repair": True, "output_preview": str(repaired_result)[:200], "session_id": session_id})
                return to_json(repaired_result)
            if self.agent.broadcast_event:
                await self.agent.broadcast_event("skill_end", {"skill_name": resolved_name, "status": "error", "message": str(e)[:200], "session_id": session_id})
            return err(str(e), skill_name=resolved_name, completed=False, repair_trace=repair_trace)


class ReloadSkillsTool(BaseTool):
    def __init__(self, agent):
        self.name = "reload_skills"
        self.description = "Reload all skills from the skills directory."
        self.parameters_schema = {"type": "object", "properties": {}}
        self.agent = agent

    async def execute(self, session_id: str = None):
        await self.agent._load_skills()
        return {"status": "success", "skills": list(self.agent.loaded_skills.keys())}


class SaveAsSkillTool(BaseTool):
    def __init__(self, agent):
        self.name = "save_as_skill"
        self.description = "Save a successfully executed script as a reusable skill for future use."
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string"},
                "description": {"type": "string"},
                "code": {"type": "string"},
                "parameters": {"type": "object"}
            },
            "required": ["skill_name", "description", "code"]
        }
        self.agent = agent

    async def execute(self, skill_name: str, description: str, code: str, parameters: dict = None, session_id: str = None):
        skill_name = normalize_tool_name(skill_name).strip("_")
        if not skill_name:
            return err("Invalid skill_name.", completed=True)
        skill_dir = self.agent.skills_dir / skill_name
        if skill_dir.exists():
            return err(f"Skill '{skill_name}' already exists.", completed=True)
        skill_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        try:
            skill_md = (
                "---\n"
                f"name: {skill_name}\n"
                f"description: {description}\n"
                f"parameters: {json.dumps(parameters or {}, ensure_ascii=False)}\n"
                "---\n\n"
                f"# {skill_name}\n\n"
                f"{description}\n"
            )
            async with aiofiles.open(skill_dir / "SKILL.md", "w", encoding="utf-8") as f:
                await f.write(skill_md)
            async with aiofiles.open(scripts_dir / "main.py", "w", encoding="utf-8") as f:
                await f.write(code)
            await self.agent._load_skills()
            return ok(message=f"Skill '{skill_name}' saved and loaded.", completed=True)
        except Exception as e:
            return err(str(e), completed=True)


class PromoteScriptToSkillTool(BaseTool):
    def __init__(self, agent):
        self.name = "promote_script_to_skill"
        self.description = (
            "Promote a previously successful write_and_execute_script run into a reusable installed skill. "
            "Use this after the user confirms a generated script worked and should become a durable capability."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "script_hash": {
                    "type": "string",
                    "description": "Hash returned by write_and_execute_script. If omitted, the latest candidate for the session is used."
                },
                "skill_name": {
                    "type": "string",
                    "description": "Optional preferred snake_case skill name."
                },
                "description": {
                    "type": "string",
                    "description": "Optional user-facing skill description."
                },
                "force": {
                    "type": "boolean",
                    "description": "Overwrite an existing generated skill directory when true."
                }
            }
        }
        self.agent = agent

    async def execute(
        self,
        script_hash: str = None,
        skill_name: str = None,
        description: str = None,
        force: bool = False,
        session_id: str = "default"
    ):
        return await self.agent.promote_script_candidate(
            script_hash=script_hash,
            session_id=session_id,
            preferred_name=skill_name,
            preferred_description=description,
            force=force
        )


class ProposeEvolutionTool(BaseTool):
    def __init__(self, agent):
        self.name = "propose_evolution_change"
        self.description = (
            "Create a controlled self-evolution proposal for skills, docs, tests, or project files. "
            "This only records a reviewable proposal; target files are not changed until a user applies it."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short proposal title."},
                "summary": {"type": "string", "description": "What changes and why."},
                "kind": {
                    "type": "string",
                    "enum": ["skill", "project", "doc", "test", "other"],
                    "description": "Primary proposal category."
                },
                "files": {
                    "type": "array",
                    "description": "File changes to stage for review.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Workspace-relative path."},
                            "action": {"type": "string", "enum": ["write", "delete"], "description": "Default is write."},
                            "content": {"type": "string", "description": "Full replacement content for write actions."},
                            "summary": {"type": "string", "description": "Short per-file rationale."}
                        },
                        "required": ["path"]
                    }
                },
                "notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional review notes, risks, or verification commands."
                }
            },
            "required": ["title", "summary", "files"]
        }
        self.agent = agent

    async def execute(self, title: str, summary: str, files: List[dict], kind: str = "project", notes: List[str] = None, session_id: str = None):
        try:
            proposal = self.agent.evolution_store.create_proposal(
                title=title,
                summary=summary,
                kind=kind,
                files=files,
                author=f"agent:{session_id or 'default'}",
                notes=notes if isinstance(notes, list) else [],
            )
            return ok(message="Evolution proposal created for review.", proposal=proposal, completed=True)
        except EvolutionError as exc:
            return err(str(exc), completed=True)
        except Exception as exc:
            return err(f"Failed to create evolution proposal: {exc}", completed=True)


class ScheduleTaskTool(BaseTool):
    def __init__(self, agent):
        self.name = "schedule_future_task"
        self.description = "Schedule, list, remove, pause, or resume cron tasks."
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove", "pause", "resume"],
                    "description": "Operation to perform"
                },
                "task_prompt": {"type": "string", "description": "Task description (required for add)"},
                "cron_expr": {"type": "string", "description": "Cron expression (required for add)"},
                "job_id": {"type": "string", "description": "Job ID (required for remove/pause/resume)"},
                "channel": {"type": "string", "description": "Notification channel: notification, websocket, chat"}
            },
            "required": ["action"]
        }
        self.agent = agent

    async def execute(self, action: str = "add", task_prompt: str = None, cron_expr: str = None,
                      job_id: str = None, channel: str = "notification", session_id: str = None):
        scheduler = self.agent.scheduler
        if action == "add":
            if not task_prompt or not cron_expr:
                return err("Missing task_prompt or cron_expr")
            job = scheduler.add_job(
                self.agent.scheduled_task_executor,
                "cron",
                args=[session_id, task_prompt, channel],
                id=job_id or str(uuid.uuid4()),
                replace_existing=True,
                **self._parse_cron(cron_expr)
            )
            if self.agent.broadcast_event:
                await self.agent.broadcast_event("schedule_updated", {
                    "action": "add",
                    "job_id": job.id,
                    "cron_expr": cron_expr,
                    "channel": channel,
                    "session_id": session_id
                })
            return ok(message=f"Task added: {job.id} [{cron_expr}] {task_prompt}")
        elif action == "list":
            jobs = scheduler.get_jobs()
            return ok(jobs=[{"id": j.id, "next_run_time": str(j.next_run_time), "args": j.args} for j in jobs])
        elif action == "remove":
            if not job_id:
                return err("Missing job_id")
            scheduler.remove_job(job_id)
            if self.agent.broadcast_event:
                await self.agent.broadcast_event("schedule_updated", {"action": "remove", "job_id": job_id, "session_id": session_id})
            return ok(message=f"Task {job_id} removed")
        elif action == "pause":
            if not job_id:
                return err("Missing job_id")
            scheduler.pause_job(job_id)
            if self.agent.broadcast_event:
                await self.agent.broadcast_event("schedule_updated", {"action": "pause", "job_id": job_id, "session_id": session_id})
            return ok(message=f"Task {job_id} paused")
        elif action == "resume":
            if not job_id:
                return err("Missing job_id")
            scheduler.resume_job(job_id)
            if self.agent.broadcast_event:
                await self.agent.broadcast_event("schedule_updated", {"action": "resume", "job_id": job_id, "session_id": session_id})
            return ok(message=f"Task {job_id} resumed")
        else:
            return err(f"Unknown action: {action}")

    def _parse_cron(self, expr: str):
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError("Cron expression must have 5 parts")
        return {
            "minute": parts[0],
            "hour": parts[1],
            "day": parts[2],
            "month": parts[3],
            "day_of_week": parts[4]
        }


class RAGSearchTool(BaseTool):
    """Search the RAG knowledge base with hybrid retrieval."""

    def __init__(self, agent):
        self.name = "rag_search"
        self.description = (
            "Search the document knowledge base using hybrid retrieval "
            "(vector similarity + full-text + entity graph). "
            "Returns ranked chunks with source citations and entity context. "
            "Use this for answering questions about ingested documents."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "strategy": {
                    "type": "string",
                    "enum": ["auto", "local", "global", "fused"],
                    "description": "Search strategy. auto detects from query type.",
                },
                "top_k": {"type": "integer", "description": "Number of results. Default 10."},
                "owner_id": {"type": "string", "description": "Owner filter for multi-tenant."},
            },
            "required": ["query"],
        }
        self.agent = agent

    async def execute(self, query: str, strategy: str = "auto",
                      top_k: int = 10, owner_id: str = "default",
                      session_id: str = None):
        from rag_retrieval import hybrid_search
        try:
            engine = getattr(self.agent, "_rag_engine", None)
            config = getattr(self.agent, "config", {})
            result = await hybrid_search(
                query=query, owner_id=owner_id, scope="shared",
                strategy=strategy, top_k=top_k,
                engine=engine, config=config,
            )
            return json.dumps({
                "status": "success",
                "chunks": [
                    {"text": c["text"][:500], "doc_id": c["doc_id"],
                     "score": c["score"], "source": c["source"]}
                    for c in result.get("chunks", [])[:top_k]
                ],
                "entities": result.get("entities", []),
                "communities": result.get("communities", []),
                "strategy": result.get("strategy"),
                "elapsed_ms": result.get("elapsed_ms"),
                "from_cache": result.get("from_cache", False),
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})


class RAGIngestTool(BaseTool):
    """Ingest a document into the RAG knowledge base."""

    def __init__(self, agent):
        self.name = "rag_ingest"
        self.description = (
            "Ingest a document (PDF, Markdown, TXT, HTML, Office, code) "
            "into the knowledge base. The document is chunked, embedded, "
            "and entities are extracted into the graph."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Path to the document."},
                "owner_id": {"type": "string", "description": "Owner for multi-tenant."},
            },
            "required": ["filepath"],
        }
        self.agent = agent

    async def execute(self, filepath: str, owner_id: str = "default",
                      session_id: str = None):
        from rag_ingest import RAGIngestionPipeline
        try:
            config = getattr(self.agent, "config", {})
            memory = getattr(self.agent, "memory_engine", None)
            pipeline = RAGIngestionPipeline(memory, config)
            result = await pipeline.ingest_file(filepath, owner_id)
            return json.dumps({
                "status": "success",
                "doc_id": result.doc_id,
                "title": result.title,
                "file_type": result.file_type,
                "chunk_count": result.chunk_count,
                "entity_count": result.entity_count,
                "elapsed_ms": result.elapsed_ms,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})


class ImportLogsTool(BaseTool):
    """Import training data from external agent logs (Claude Code, Codex, OpenClaw)."""

    def __init__(self, agent):
        self.name = "import_training_logs"
        self.description = (
            "Import agent trajectories from external log sources: "
            "Claude Code transcripts, Codex logs, and OpenClaw/Hermes sessions. "
            "These trajectories are used to train the companion reward model."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["claude_code", "codex", "openclaw", "auto"],
                    "description": "Log source to import. auto scans all known locations.",
                },
                "path": {
                    "type": "string",
                    "description": "Custom path to logs. If omitted, uses default locations.",
                },
            },
        }
        self.agent = agent

    async def execute(self, source: str = "auto", path: str = None,
                      session_id: str = None):
        imported = {"text": 0, "visual": 0, "errors": []}

        if source in ("auto", "claude_code"):
            claude_dir = path or os.path.expanduser("~/.claude/projects/")
            if os.path.isdir(claude_dir):
                try:
                    from claude_code_parser import ClaudeCodeParser
                    parser = ClaudeCodeParser()
                    turns = parser.parse_directory(claude_dir)
                    trajs = parser.to_trajectories(turns, source="claude_code")
                    store = getattr(self.agent, '_trajectory_store', None)
                    if store:
                        for t in trajs:
                            try:
                                store.store(t)
                                imported["text"] += 1
                            except Exception:
                                pass
                except Exception as e:
                    imported["errors"].append(f"claude_code: {e}")

        if source in ("auto", "openclaw"):
            oc_dir = path or os.path.expanduser("~/.openclaw/sessions/")
            if os.path.isdir(oc_dir):
                try:
                    from openclaw_importer import OpenClawImporter
                    importer = OpenClawImporter()
                    result = importer.parse_directory(oc_dir)
                    store = getattr(self.agent, '_trajectory_store', None)
                    for t in result.get("text_trajectories", []):
                        try:
                            store.store(t)
                            imported["text"] += 1
                        except Exception:
                            pass
                    try:
                        from visual_trajectory_store import VisualTrajectoryStore
                        vs = VisualTrajectoryStore()
                        for t in result.get("visual_trajectories", []):
                            try:
                                vs.store(t)
                                imported["visual"] += 1
                            except Exception:
                                pass
                    except Exception:
                        pass
                except Exception as e:
                    imported["errors"].append(f"openclaw: {e}")

        if source in ("auto", "codex"):
            codex_dir = path or os.path.expanduser("~/.codex/logs/")
            if os.path.isdir(codex_dir):
                try:
                    from trajectory_importer import TrajectoryImporter
                    importer = TrajectoryImporter()
                    trajs = importer.parse_path(codex_dir, format="codex", source="codex")
                    store = getattr(self.agent, '_trajectory_store', None)
                    if store:
                        for t in trajs:
                            try:
                                store.store(t)
                                imported["text"] += 1
                            except Exception:
                                pass
                except Exception as e:
                    imported["errors"].append(f"codex: {e}")

        # Build training datasets after import
        dataset_result = None
        if imported["text"] > 0 or imported["visual"] > 0:
            try:
                dataset_result = self.agent._build_training_datasets()
            except Exception:
                pass

        return json.dumps({
            "status": "success",
            "imported_text": imported["text"],
            "imported_visual": imported["visual"],
            "errors": imported["errors"],
            "datasets_built": dataset_result is not None,
        }, ensure_ascii=False)


class ScreenshotTool(BaseTool):
    """Capture the screen and return a data URI for multimodal LLM input."""

    def __init__(self, agent):
        self.name = "screenshot"
        self.description = (
            "Capture the current screen and return a base64-encoded image. "
            "Use this before making GUI automation decisions — the image "
            "can be passed to vision-capable models for UI understanding."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "save_path": {
                    "type": "string",
                    "description": "Optional file path to save the screenshot PNG.",
                },
            },
        }
        self.agent = agent

    async def execute(self, save_path: str = None, session_id: str = None):
        from screen_capture import capture_fullscreen
        try:
            result = capture_fullscreen(save_path)
            return json.dumps({
                "status": "success",
                "width": result["width"],
                "height": result["height"],
                "base64_length": len(result["base64"]),
                "data_uri": f"data:image/png;base64,{result['base64']}",
                "saved_to": save_path,
                "elapsed_ms": result["elapsed_ms"],
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})


class ExecuteGUIActionTool(BaseTool):
    """Execute a GUI automation action (click, type, scroll, etc.) via PyAutoGUI."""

    def __init__(self, agent):
        self.name = "execute_gui_action"
        self.description = (
            "Execute a GUI automation action on the desktop: click, type, scroll, "
            "drag, hotkey, press key, move mouse, sleep, or run a sequence of actions. "
            "Use screenshot first to see the screen, then call this tool to interact."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["click", "type", "scroll", "drag", "hotkey", "press",
                             "move", "sleep", "screenshot", "sequence"],
                    "description": "The GUI action to perform.",
                },
                "x": {"type": "number", "description": "X coordinate for click/move."},
                "y": {"type": "number", "description": "Y coordinate for click/move."},
                "relative": {
                    "type": "boolean",
                    "description": "If true, coordinates in [0,1000] range.",
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "description": "Mouse button for click.",
                },
                "text": {"type": "string", "description": "Text to type."},
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Hotkey combination.",
                },
                "key": {"type": "string", "description": "Single key to press."},
                "clicks": {"type": "integer", "description": "Scroll wheel clicks."},
                "seconds": {"type": "number", "description": "Sleep duration."},
                "actions": {
                    "type": "array",
                    "description": "List of actions for sequence mode.",
                },
            },
            "required": ["action"],
        }
        self.agent = agent

    async def execute(self, action: str, **kwargs):
        from gui_actions import dispatch_action, execute_sequence
        try:
            if action == "sequence":
                actions_list = kwargs.get("actions", [])
                results = execute_sequence(actions_list)
                return json.dumps({
                    "status": "success",
                    "action": "sequence",
                    "total": len(results),
                    "results": results,
                }, ensure_ascii=False)
            elif action == "screenshot":
                from screen_capture import capture_fullscreen
                result = capture_fullscreen(kwargs.get("save_path"))
                result["status"] = "success"
                result["base64_length"] = len(result.get("base64", ""))
                result["base64"] = result.get("base64", "")[:200] + "..."
                return json.dumps(result, ensure_ascii=False)
            else:
                result = dispatch_action(action, kwargs)
                return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "action": action, "message": str(e)})


class BackupSkillTool(BaseTool):
    def __init__(self, agent):
        self.name = "backup_skill"
        self.description = "Create a backup snapshot of a skill directory before modifying it."
        self.parameters_schema = {"type": "object", "properties": {"skill_name": {"type": "string"}}, "required": ["skill_name"]}
        self.agent = agent

    async def execute(self, skill_name: str, session_id: str = None):
        skill_dir = self.agent.skills_dir / skill_name
        if not skill_dir.exists():
            return err(f"Skill '{skill_name}' not found.", completed=True)
        backup_root = self.agent.skills_dir.parent / "skills_backup"
        backup_root.mkdir(exist_ok=True)
        backup_path = backup_root / f"{skill_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copytree(skill_dir, backup_path)
            return ok(message=f"Backup created at {backup_path}", backup_path=str(backup_path), completed=True)
        except Exception as e:
            return err(f"Backup failed: {str(e)}", completed=True)


class RestoreSkillTool(BaseTool):
    def __init__(self, agent):
        self.name = "restore_skill"
        self.description = "Restore a skill from a backup snapshot."
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string"},
                "backup_path": {"type": "string"}
            },
            "required": ["skill_name"]
        }
        self.agent = agent

    async def execute(self, skill_name: str, backup_path: str = None, session_id: str = None):
        skill_dir = self.agent.skills_dir / skill_name
        backup_root = self.agent.skills_dir.parent / "skills_backup"
        if backup_path:
            src_path = Path(backup_path)
            if not src_path.exists():
                return err(f"Backup path {backup_path} does not exist.", completed=True)
        else:
            candidates = sorted(backup_root.glob(f"{skill_name}_*"), key=lambda p: p.name, reverse=True)
            if not candidates:
                return err(f"No backups found for skill '{skill_name}'.", completed=True)
            src_path = candidates[0]
        try:
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
            shutil.copytree(src_path, skill_dir)
            await self.agent._load_skills()
            return ok(message=f"Skill '{skill_name}' restored from {src_path} and reloaded.", completed=True)
        except Exception as e:
            return err(f"Restore failed: {str(e)}", completed=True)


class GitResetSkillTool(BaseTool):
    def __init__(self, agent):
        self.name = "git_reset_skill"
        self.description = "Reset a Git-based skill to its original cloned state."
        self.parameters_schema = {"type": "object", "properties": {"skill_name": {"type": "string"}}, "required": ["skill_name"]}
        self.agent = agent

    async def execute(self, skill_name: str, session_id: str = None):
        skill_dir = self.agent.skills_dir / skill_name
        if not (skill_dir / ".git").exists():
            return err("Not a Git repository.", completed=True)
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(skill_dir), "reset", "--hard", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            await self.agent._load_skills()
            return ok(message="Skill reset to last commit.", output=safe_decode(stdout), completed=True)
        return err(safe_decode(stderr) or safe_decode(stdout), completed=True)


class DynamicOpenAPITool(BaseTool):
    def __init__(self, operation_id: str, description: str, schema: Dict[str, Any], url: str, method: str, base_headers: dict = None):
        self.name = normalize_tool_name(operation_id)
        self.description = description
        self.parameters_schema = schema
        self.url = url
        self.method = method.upper()
        self.base_headers = base_headers or {}

    async def execute(self, **kwargs) -> Any:
        url = self.url
        headers = self.base_headers.copy()
        args = dict(kwargs)
        for key, value in list(args.items()):
            if f"{{{key}}}" in url:
                url = url.replace(f"{{{key}}}", str(value))
                args.pop(key)
        json_data = args if self.method in {"POST", "PUT", "PATCH"} else None
        params = None if json_data is not None else args
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.request(self.method, url, headers=headers, json=json_data, params=params) as resp:
                    resp.raise_for_status()
                    try:
                        return await resp.json()
                    except Exception:
                        return await resp.text()
        except ImportError as e:
            return {"status": "error", "message": f"API Request requires aiohttp and dependencies: {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": f"API Request failed: {str(e)}"}


class OpenAPIBridge:
    def __init__(self, tool_manager, base_url: str, spec_dict: dict, auth_headers: dict = None):
        self.tool_manager = tool_manager
        self.base_url = base_url.rstrip("/")
        self.spec = spec_dict
        self.auth_headers = auth_headers or {}

    def _operation_schema(self, details: dict) -> dict:
        schema = {"type": "object", "properties": {}, "required": []}
        for param in details.get("parameters", []) or []:
            name = param.get("name")
            if not name:
                continue
            param_schema = param.get("schema", {}) or {}
            schema["properties"][name] = {
                "type": param_schema.get("type", "string"),
                "description": param.get("description", "")
            }
            if param.get("required"):
                schema["required"].append(name)
        request_body = details.get("requestBody", {}) or {}
        content = request_body.get("content", {}) or {}
        app_json = content.get("application/json", {}) or {}
        body_schema = app_json.get("schema", {}) or {}
        if body_schema.get("type") == "object":
            for key, spec in (body_schema.get("properties") or {}).items():
                schema["properties"][key] = spec if isinstance(spec, dict) else {"type": "string"}
            for key in body_schema.get("required", []) or []:
                if key not in schema["required"]:
                    schema["required"].append(key)
        return schema

    def register_all(self):
        paths = self.spec.get("paths", {})
        count = 0
        for path, methods in paths.items():
            for method, details in methods.items():
                if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                    continue
                operation_id = details.get("operationId", f"{method}_{path.replace('/', '_')}")
                description = details.get("summary") or details.get("description") or "No description provided."
                schema = self._operation_schema(details)
                self.tool_manager.register(DynamicOpenAPITool(
                    operation_id=operation_id,
                    description=description,
                    schema=schema,
                    url=f"{self.base_url}{path}",
                    method=method,
                    base_headers=self.auth_headers
                ))
                count += 1
        return count


class MCPProxiedTool(BaseTool):
    def __init__(self, mcp_session: ClientSession, name: str, description: str, schema: Dict[str, Any]):
        self.remote_name = name
        self.name = f"mcp_{normalize_tool_name(name)}"
        self.description = f"[Remote MCP Tool] {description}"
        self.parameters_schema = schema
        self.mcp_session = mcp_session

    async def execute(self, **kwargs) -> Any:
        try:
            result = await self.mcp_session.call_tool(self.remote_name, arguments=kwargs)
            output = []
            for item in result.content:
                if item.type == "text":
                    output.append(item.text)
                else:
                    output.append(f"[Media content: {item.type}]")
            return "\n".join(output)
        except Exception as e:
            return {"status": "error", "message": f"MCP Tool execution failed: {str(e)}"}


class MCPServerBridge:
    def __init__(self, tool_manager, server_command: str, server_args: list):
        self.tool_manager = tool_manager
        self.server_params = StdioServerParameters(
            command=server_command,
            args=server_args,
            env=None
        )
        self.session = None
        self._client_context = None

    async def connect_and_register(self):
        self._client_context = stdio_client(self.server_params)
        read_stream, write_stream = await self._client_context.__aenter__()
        self.session = ClientSession(read_stream, write_stream)
        await self.session.__aenter__()
        await self.session.initialize()
        tools_response = await self.session.list_tools()
        for remote_tool in tools_response.tools:
            self.tool_manager.register(MCPProxiedTool(
                mcp_session=self.session,
                name=remote_tool.name,
                description=remote_tool.description,
                schema=remote_tool.inputSchema
            ))
        return len(tools_response.tools)

    async def close(self):
        if self.session:
            await self.session.__aexit__(None, None, None)
        if self._client_context:
            await self._client_context.__aexit__(None, None, None)


def check_dependencies_met(requirements_path: str) -> bool:
    try:
        with open(requirements_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                pkg_name = re.split(r'[=><~]+', line)[0].strip()
                if pkg_name:
                    importlib.metadata.version(pkg_name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False
    except Exception:
        return False


class SmartSkillAdapterTool(BaseTool):
    def __init__(self, skill_folder_name: str, function_name: str, description: str, schema: dict, agent=None):
        self.name = function_name
        self.description = description
        self.parameters_schema = schema
        self.skill_folder_name = skill_folder_name
        self.function_name = function_name
        self.agent = agent

    async def _check_dependencies_safety(self, req_lines: List[str]) -> Tuple[bool, str]:
        if not self.agent or not hasattr(self.agent, 'client'):
            return True, ""
        if hasattr(self.agent, "audit_dependency_safety"):
            return await self.agent.audit_dependency_safety(
                req_lines,
                session_id="default",
                source=f"skill:{self.skill_folder_name}",
                context="requirements.txt\n" + "\n".join(req_lines)
            )
        prompt = f"""Analyze these Python package dependencies for security risks:
{chr(10).join(req_lines)}

Consider:
- Typosquatting (names similar to popular packages)
- Outdated versions with known vulnerabilities
- Unusually low download counts or suspicious maintainers
- Packages that execute code during installation

Return JSON: {{"risk": "high|medium|low", "reason": "explanation", "suggest_block": true/false}}"""
        try:
            resp = await self.agent.client.chat.completions.create(
                model=self.agent.model,
                messages=[
                    {"role": "system", "content": "You are a security assistant. Output JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                **self.agent.extra_params
            )
            result = json.loads(resp.choices[0].message.content)
            risk = result.get("risk", "low")
            if risk == "high" or result.get("suggest_block", False):
                return False, result.get("reason", "High risk dependencies detected.")
            elif risk == "medium":
                confirm = await self.agent._request_user_confirmation(
                    f"Dependencies have medium risk: {result.get('reason', '')}. Install anyway?",
                    session_id="default"
                )
                if not confirm:
                    return False, "User denied due to medium risk."
            return True, ""
        except Exception as e:
            return True, f"Safety check failed: {e}"

    async def execute(self, **kwargs) -> str:
        if self.agent and self.agent.broadcast_event:
            await self.agent.broadcast_event("smart_skill_start", {"skill": self.skill_folder_name, "function": self.function_name, "kwargs": str(kwargs)[:200]})

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skill_dir = os.path.join(base_dir, "skills", self.skill_folder_name)
        req_file = os.path.join(skill_dir, "requirements.txt")
        target_python = sys.executable
        need_sandbox = False

        if os.path.exists(req_file):
            if not check_dependencies_met(req_file):
                need_sandbox = True

        if need_sandbox:
            venv_dir = os.path.join(skill_dir, "venv")
            target_python = os.path.join(venv_dir, "Scripts", "python.exe") if os.name == 'nt' else os.path.join(venv_dir, "bin", "python")
            hash_file = os.path.join(venv_dir, "req.hash")
            
            current_hash = ""
            if os.path.exists(req_file):
                with open(req_file, 'rb') as f:
                    current_hash = hashlib.sha256(f.read()).hexdigest()
                    
            old_hash = ""
            if os.path.exists(hash_file):
                with open(hash_file, 'r', encoding='utf-8') as f:
                    old_hash = f.read().strip()

            if not os.path.exists(target_python) or current_hash != old_hash:
                with open(req_file, 'r', encoding='utf-8') as f:
                    req_lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                safe, reason = await self._check_dependencies_safety(req_lines)
                if not safe:
                    if self.agent and self.agent.broadcast_event:
                        await self.agent.broadcast_event("smart_skill_end", {"skill": self.skill_folder_name, "status": "error", "output": reason[:200]})
                    return err(f"Dependency installation blocked: {reason}", completed=True)

                print(f"\n\033[93m[*] 技能【{self.skill_folder_name}】沙盒环境初始化或依赖变更，开始自动构建，请勿关闭...\033[0m", flush=True)

                if not os.path.exists(target_python):
                    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "venv", venv_dir)
                    await proc.communicate()

                print(f"\033[96m[*] 正在专属沙盒中异步构建核心依赖包...\033[0m", flush=True)
                proc = await asyncio.create_subprocess_exec(
                    target_python,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    "-r",
                    req_file
                )
                await asyncio.wait_for(proc.communicate(), timeout=getattr(getattr(self.agent, "runtime", None), "pip_install_timeout_sec", 900))
                if proc.returncode != 0:
                    return err(f"Dependency installation failed with code {proc.returncode}", completed=True)

                if os.path.exists(req_file):
                    with open(req_file, 'r', encoding='utf-8') as f:
                        req_content = f.read().lower()
                    if "playwright" in req_content:
                        print(f"\033[95m[*] 检测到该技能需要 Playwright 爬虫，正在沙盒内独立下载 Chromium 内核 (耗时较长)...\033[0m", flush=True)
                        proc = await asyncio.create_subprocess_exec(target_python, "-m", "playwright", "install", "chromium")
                        await proc.communicate()

                with open(hash_file, 'w', encoding='utf-8') as f:
                    f.write(current_hash)

                print(f"\033[92m[*] 技能【{self.skill_folder_name}】专属沙盒构建完美完成！\n\033[0m", flush=True)

        runner_code = f"""
import sys
import json
import traceback
sys.path.insert(0, r'{os.path.abspath(skill_dir)}')

try:
    from main import {self.function_name}
    result = {self.function_name}(**{json.dumps(kwargs)})
    print(json.dumps({{"status": "success", "data": result}}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({{"status": "error", "message": str(e), "trace": traceback.format_exc()}}, ensure_ascii=False))
"""

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [target_python, "-c", runner_code],
                capture_output=True,
                text=True,
                encoding='utf-8'
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                if self.agent and self.agent.broadcast_event:
                    await self.agent.broadcast_event("smart_skill_end", {"skill": self.skill_folder_name, "status": "error", "output": result.stderr[:200]})
                return err(f"Subprocess failed: {result.stderr}")
            if self.agent and self.agent.broadcast_event:
                await self.agent.broadcast_event("smart_skill_end", {"skill": self.skill_folder_name, "status": "success", "output": output[:200]})
            return output
        except Exception as e:
            if self.agent and self.agent.broadcast_event:
                await self.agent.broadcast_event("smart_skill_end", {"skill": self.skill_folder_name, "status": "error", "message": str(e)[:200]})
            return err(f"Adapter execution failed: {str(e)}")
