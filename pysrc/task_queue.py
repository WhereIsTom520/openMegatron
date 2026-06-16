"""task_queue.py - 异步任务队列系统

类似 External Text Agent 的任务队列机制，提供：
1. 任务持久化（PostgreSQL）
2. 优先级调度
3. 状态管理（pending → running → completed/failed/cancelled）
4. 自动重试
5. 并发控制
6. 任务依赖
7. Web 可视化
"""

import asyncio
import json as _json
import hashlib
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ───────── 状态定义 ─────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"
    BLOCKED = "blocked"  # 依赖未满足


class TaskPriority(int, Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


# ───────── 数据类 ─────────

@dataclass
class Task:
    """单个任务的数据结构。"""
    id: str
    queue: str = "default"
    type: str = "generic"           # 任务类型标签
    payload: dict = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    max_retries: int = 3
    retry_count: int = 0
    depends_on: list[str] = field(default_factory=list)  # 依赖的任务 ID 列表
    scheduled_at: Optional[datetime] = None  # 定时执行
    timeout: Optional[float] = None          # 超时秒数
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    session_id: Optional[str] = None
    owner_id: str = "system"
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "queue": self.queue,
            "type": self.type,
            "payload": self.payload,
            "status": self.status.value,
            "priority": self.priority.value,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "depends_on": self.depends_on,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "timeout": self.timeout,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "session_id": self.session_id,
            "owner_id": self.owner_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            id=d["id"],
            queue=d.get("queue", "default"),
            type=d.get("type", "generic"),
            payload=d.get("payload", {}),
            status=TaskStatus(d.get("status", "pending")),
            priority=TaskPriority(d.get("priority", 1)),
            max_retries=d.get("max_retries", 3),
            retry_count=d.get("retry_count", 0),
            depends_on=d.get("depends_on", []),
            scheduled_at=datetime.fromisoformat(d["scheduled_at"]) if d.get("scheduled_at") else None,
            timeout=d.get("timeout"),
            result=d.get("result"),
            error=d.get("error"),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(timezone.utc),
            started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None,
            completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
            session_id=d.get("session_id"),
            owner_id=d.get("owner_id", "system"),
            metadata=d.get("metadata", {}),
        )


# ───────── 任务处理器注册 ─────────

class TaskHandler:
    """任务处理器基类。子类实现 async handle(task) -> dict。"""

    async def handle(self, task: Task) -> dict:
        raise NotImplementedError


_handler_registry: dict[str, type[TaskHandler]] = {}


def register_handler(task_type: str):
    """装饰器：注册任务类型对应的处理器。"""
    def decorator(cls):
        _handler_registry[task_type] = cls
        return cls
    return decorator


def get_handler(task_type: str) -> Optional[TaskHandler]:
    cls = _handler_registry.get(task_type)
    if cls:
        return cls()
    return None


# ───────── 队列核心引擎 ─────────

class TaskQueue:
    """异步任务队列引擎。

    用法:
        queue = TaskQueue(pg_pool)
        await queue.start()  # 启动后台 worker
        task_id = await queue.enqueue("my_type", {"key": "value"}, priority=TaskPriority.HIGH)
        result = await queue.wait_for(task_id)  # 等待完成
        await queue.stop()
    """

    def __init__(
        self,
        pg_pool=None,
        max_concurrency: int = 4,
        poll_interval: float = 0.5,
    ):
        self.pg_pool = pg_pool
        self.max_concurrency = max_concurrency
        self.poll_interval = poll_interval
        self._running = False
        self._workers: dict[str, asyncio.Task] = {}   # queue_name -> worker task
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._pending_callbacks: dict[str, list[asyncio.Future]] = defaultdict(list)
        self._active_count: int = 0

    async def start(self):
        """启动队列（创建信号量，启动 worker 协程）。"""
        if self._running:
            return
        self._running = True
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._workers["default"] = asyncio.create_task(self._worker_loop("default"))
        logger.info(f"TaskQueue started (max_concurrency={self.max_concurrency})")

    async def stop(self, wait: bool = True):
        """停止队列。"""
        self._running = False
        for name, worker in self._workers.items():
            worker.cancel()
        if wait:
            await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._workers.clear()
        logger.info("TaskQueue stopped")

    # ── 任务入队 ──

    async def enqueue(
        self,
        task_type: str,
        payload: dict,
        *,
        queue: str = "default",
        priority: TaskPriority = TaskPriority.NORMAL,
        max_retries: int = 3,
        depends_on: list[str] = None,
        scheduled_at: datetime = None,
        timeout: float = None,
        session_id: str = None,
        owner_id: str = "system",
        metadata: dict = None,
    ) -> str:
        """将任务加入队列，返回任务 ID。"""
        task_id = _task_id(task_type, payload)

        task = Task(
            id=task_id,
            queue=queue,
            type=task_type,
            payload=payload,
            priority=priority,
            max_retries=max_retries,
            depends_on=depends_on or [],
            scheduled_at=scheduled_at,
            timeout=timeout,
            session_id=session_id,
            owner_id=owner_id,
            metadata=metadata or {},
        )

        if self.pg_pool:
            await self._pg_upsert(task)
        else:
            logger.warning("No pg_pool set — task not persisted")

        logger.debug(f"Enqueued {task_type} task: {task_id} (priority={priority.name})")
        return task_id

    async def enqueue_batch(self, tasks: list[dict]) -> list[str]:
        """批量入队。tasks: list of kwargs for enqueue()."""
        return [await self.enqueue(**t) for t in tasks]

    # ── 任务等待 ──

    async def wait_for(self, task_id: str, timeout: float = None) -> dict:
        """等待任务完成，返回结果。"""
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_callbacks[task_id].append(future)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            return {"status": "timeout", "task_id": task_id}
        finally:
            if task_id in self._pending_callbacks:
                self._pending_callbacks[task_id] = [
                    f for f in self._pending_callbacks[task_id] if not f.done()
                ]

    # ── 查询 ──

    async def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务状态。"""
        if self.pg_pool:
            return await self._pg_fetch(task_id)
        return None

    async def list_tasks(
        self,
        status: TaskStatus = None,
        queue: str = None,
        task_type: str = None,
        session_id: str = None,
        limit: int = 50,
    ) -> list[Task]:
        """列出任务。"""
        if self.pg_pool:
            return await self._pg_list(status, queue, task_type, session_id, limit)
        return []

    async def get_queue_stats(self) -> dict:
        """获取队列统计。"""
        if self.pg_pool:
            return await self._pg_stats()
        return {"total": 0, "by_status": {}, "by_type": {}}

    # ── 任务控制 ──

    async def cancel(self, task_id: str):
        """取消任务。"""
        if self.pg_pool:
            await self._pg_update_status(task_id, TaskStatus.CANCELLED)

    async def retry(self, task_id: str):
        """重试失败任务。"""
        task = await self.get_task(task_id)
        if task and task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            if self.pg_pool:
                await self._pg_update_status(task_id, TaskStatus.PENDING, retry_count=0, error=None)

    # ── Worker 循环 ──

    async def _worker_loop(self, queue_name: str):
        """后台 worker：不断轮询待办任务并执行。"""
        while self._running:
            try:
                task = await self._dequeue(queue_name)
                if task:
                    self._active_count += 1
                    asyncio.create_task(self._execute_task(task))
                else:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker loop error ({queue_name}): {e}")
                await asyncio.sleep(self.poll_interval)

    async def _dequeue(self, queue_name: str) -> Optional[Task]:
        """从队列中取出下一个待办任务。"""
        if not self.pg_pool:
            return None

        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT data FROM task_queue
                   WHERE status = 'pending'
                     AND queue = $1
                     AND (scheduled_at IS NULL OR scheduled_at <= NOW())
                     AND NOT EXISTS (
                       SELECT 1 FROM task_queue AS dep
                       WHERE dep.id = ANY(task_queue.depends_on)
                         AND dep.status != 'completed'
                     )
                   ORDER BY priority DESC, created_at ASC
                   LIMIT 1
                   FOR UPDATE SKIP LOCKED""",
                queue_name,
            )
            if row:
                data = row["data"]
                await conn.execute(
                    "UPDATE task_queue SET status = 'running', started_at = NOW() WHERE id = $1",
                    data["id"],
                )
                return Task.from_dict(data)
        return None

    async def _execute_task(self, task: Task):
        """执行单个任务（带重试逻辑）。"""
        async with self._semaphore:
            handler = get_handler(task.type)
            if not handler:
                logger.warning(f"No handler for task type: {task.type}")
                await self._fail(task, f"No handler registered for type '{task.type}'")
                self._active_count -= 1
                return

            last_error = None
            for attempt in range(task.max_retries + 1):
                if attempt > 0:
                    logger.info(f"Retrying task {task.id} (attempt {attempt}/{task.max_retries})")
                    if self.pg_pool:
                        await self._pg_update_status(task.id, TaskStatus.RETRYING,
                                                     retry_count=attempt)

                try:
                    if task.timeout:
                        result = await asyncio.wait_for(handler.handle(task), timeout=task.timeout)
                    else:
                        result = await handler.handle(task)

                    await self._complete(task, result)
                    self._active_count -= 1
                    return

                except asyncio.TimeoutError:
                    last_error = f"Timeout after {task.timeout}s"
                    logger.warning(f"Task {task.id} timeout (attempt {attempt}/{task.max_retries})")
                except Exception as e:
                    last_error = str(e)
                    logger.error(f"Task {task.id} failed (attempt {attempt}/{task.max_retries}): {e}")

                if attempt < task.max_retries:
                    await asyncio.sleep(2 ** attempt)  # exponential backoff

            # All retries exhausted
            await self._fail(task, last_error or "Unknown error")
            self._active_count -= 1

    async def _complete(self, task: Task, result: dict):
        """标记任务为完成。"""
        task.status = TaskStatus.COMPLETED
        task.result = result
        task.completed_at = datetime.now(timezone.utc)
        if self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE task_queue
                       SET status = 'completed', result = $1::jsonb, completed_at = NOW()
                       WHERE id = $2""",
                    _json.dumps(result), task.id,
                )
        # 通知等待者
        self._notify_waiters(task.id, {"status": "completed", "result": result})

    async def _fail(self, task: Task, error: str):
        """标记任务为失败。"""
        task.status = TaskStatus.FAILED
        task.error = error
        task.completed_at = datetime.now(timezone.utc)
        if self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE task_queue
                       SET status = 'failed', error = $1, completed_at = NOW()
                       WHERE id = $2""",
                    error, task.id,
                )
        self._notify_waiters(task.id, {"status": "failed", "error": error})

    def _notify_waiters(self, task_id: str, result: dict):
        """通知等待该任务完成的所有 caller。"""
        futures = self._pending_callbacks.pop(task_id, [])
        for f in futures:
            if not f.done():
                f.set_result(result)

    # ── PostgreSQL 持久化 ──

    async def _pg_upsert(self, task: Task):
        async with self.pg_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO task_queue (id, queue, type, data, status, priority, max_retries,
                                           depends_on, scheduled_at, timeout, session_id, owner_id, created_at)
                   VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13)
                   ON CONFLICT (id) DO UPDATE SET
                       status = EXCLUDED.status,
                       priority = EXCLUDED.priority,
                       data = EXCLUDED.data""",
                task.id, task.queue, task.type, _json.dumps(task.to_dict()),
                task.status.value, task.priority.value, task.max_retries,
                _json.dumps(task.depends_on), task.scheduled_at, task.timeout,
                task.session_id, task.owner_id, task.created_at,
            )

    async def _pg_fetch(self, task_id: str) -> Optional[Task]:
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT data FROM task_queue WHERE id = $1", task_id)
            if row:
                return Task.from_dict(row["data"])
        return None

    async def _pg_list(self, status=None, queue=None, task_type=None,
                       session_id=None, limit=50) -> list[Task]:
        conditions = []
        params = []
        idx = 0
        if status:
            idx += 1
            conditions.append(f"status = ${idx}")
            params.append(status.value if isinstance(status, TaskStatus) else status)
        if queue:
            idx += 1
            conditions.append(f"queue = ${idx}")
            params.append(queue)
        if task_type:
            idx += 1
            conditions.append(f"type = ${idx}")
            params.append(task_type)
        if session_id:
            idx += 1
            conditions.append(f"session_id = ${idx}")
            params.append(session_id)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT data FROM task_queue {where} ORDER BY priority DESC, created_at DESC LIMIT ${idx + 1}"
        params.append(limit)

        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [Task.from_dict(r["data"]) for r in rows]

    async def _pg_update_status(self, task_id: str, status: TaskStatus,
                                 retry_count: int = None, error: str = None):
        sets = ["status = $1"]
        params = [status.value]
        if retry_count is not None:
            sets.append("retry_count = $2")
            params.append(retry_count)
        if error is not None:
            sets.append("error = $3")
            params.append(error)
        params.append(task_id)
        sql = f"UPDATE task_queue SET {', '.join(sets)} WHERE id = ${len(params)}"
        async with self.pg_pool.acquire() as conn:
            await conn.execute(sql, *params)

    async def _pg_stats(self) -> dict:
        async with self.pg_pool.acquire() as conn:
            by_status = await conn.fetch(
                "SELECT status, COUNT(*) as cnt FROM task_queue GROUP BY status"
            )
            by_type = await conn.fetch(
                "SELECT type, COUNT(*) as cnt FROM task_queue GROUP BY type"
            )
            total_row = await conn.fetchval("SELECT COUNT(*) FROM task_queue")
        return {
            "total": total_row or 0,
            "by_status": {r["status"]: r["cnt"] for r in by_status},
            "by_type": {r["type"]: r["cnt"] for r in by_type},
        }


# ───────── 内置处理器示例 ─────────

@register_handler("echo")
class EchoHandler(TaskHandler):
    """测试用：原样返回 payload。"""
    async def handle(self, task: Task) -> dict:
        await asyncio.sleep(0.1)
        return {"echo": task.payload, "handled_at": datetime.now(timezone.utc).isoformat()}


# ───────── 工具函数 ─────────

def _task_id(task_type: str, payload: dict) -> str:
    raw = _json.dumps({"type": task_type, "payload": payload}, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    ts = int(time.time() * 1000)
    return f"task_{h}_{ts}"


# ───────── HTML 可视化（基于 vis.js 的队列监控面板） ─────────

def render_queue_dashboard(tasks: list[dict], stats: dict) -> str:
    '''Generate task queue monitoring dashboard HTML.'''
    status_colors = {
        "pending": "#f0ad4e", "running": "#5bc0de", "completed": "#5cb85c",
        "failed": "#d9534f", "cancelled": "#aaa", "retrying": "#f0ad4e", "blocked": "#999",
    }
    import json as _j
    tasks_json = _j.dumps([
        {
            "id": t["id"][:16],
            "label": t["type"][:20],
            "title": (
                "<b>" + str(t.get("type", "")) + "</b><br/>"
                + "Status: " + str(t.get("status", "")) + "<br/>"
                + "Priority: " + str(t.get("priority", "")) + "<br/>"
                + "Retries: " + str(t.get("retry_count", 0)) + "/" + str(t.get("max_retries", 3)) + "<br/>"
                + "Created: " + str(t.get("created_at", ""))[:19] + "<br/>"
                + "Payload: " + _j.dumps(t.get("payload", {}))[:200]
            ),
            "group": t["status"],
            "color": {background: status_colors.get(t["status"], "#999"), border: "#666"},
            "value": t["priority"] + 1,
        }
        for t in tasks[:200]
    ])
    by_status = _j.dumps(stats.get("by_status", {}))
    by_type = _j.dumps(stats.get("by_type", {}))
    total = stats.get("total", 0)
    pending = stats.get("by_status", {}).get("pending", 0)
    running = stats.get("by_status", {}).get("running", 0)
    completed = stats.get("by_status", {}).get("completed", 0)
    failed = stats.get("by_status", {}).get("failed", 0)

    # Build HTML template with placeholders to avoid f-string conflicts
    html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Task Queue Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.6/vis-network.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.6/dist/vis-network.min.css" />
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #f5f5f5; }
  #toolbar { position:fixed; top:0; left:0; right:0; z-index:100; background:#fff; border-bottom:1px solid #ddd; padding:10px 20px; display:flex; align-items:center; gap:16px; flex-wrap:wrap; box-shadow:0 1px 3px rgba(0,0,0,.08); }
  #toolbar h1 { margin:0; font-size:16px; color:#333; }
  #stats { font-size:12px; color:#666; display:flex; gap:12px; flex-wrap:wrap; }
  .stat-badge { display:inline-block; padding:2px 10px; border-radius:10px; font-size:12px; color:#fff; }
  #mynetwork { position:fixed; top:52px; left:0; right:420px; bottom:0; }
  #sidebar { position:fixed; top:52px; right:0; width:420px; bottom:0; background:#fff; border-left:1px solid #ddd; overflow-y:auto; padding:16px; font-size:13px; }
  #sidebar h3 { font-size:14px; margin:12px 0 6px; color:#333; border-bottom:1px solid #eee; padding-bottom:4px; }
  #sidebar ul { list-style:none; padding:0; }
  #sidebar li { padding:4px 0; border-bottom:1px solid #f0f0f0; font-size:12px; }
  .vis-network { outline:none; }
</style>
</head>
<body>
<div id="toolbar">
  <h1>&#x1F9F0; Task Queue</h1>
  <div id="stats">
    <span>Total: <b id="totalCount">TOTAL_PLACEHOLDER</b></span>
    <span class="stat-badge" style="background:#f0ad4e">P: PENDING_PLACEHOLDER</span>
    <span class="stat-badge" style="background:#5bc0de">R: RUNNING_PLACEHOLDER</span>
    <span class="stat-badge" style="background:#5cb85c">C: COMPLETED_PLACEHOLDER</span>
    <span class="stat-badge" style="background:#d9534f">F: FAILED_PLACEHOLDER</span>
  </div>
</div>
<div id="mynetwork"></div>
<div id="sidebar">
  <h3>&#x1F4CA; Status Distribution</h3>
  <div id="statusChart"></div>
  <h3>&#x1F4CB; Type Distribution</h3>
  <div id="typeList"></div>
  <h3>&#x2139;&#xFE0F; Click node for details</h3>
  <div id="detailPanel"></div>
</div>
<script>
const tasksData = new vis.DataSet(JSON_TASKS_PLACEHOLDER);
const edgesData = new vis.DataSet([]);
const options = {
  physics: { enabled: false },
  groups: {
    pending: { shape: 'dot', size: 15 },
    running: { shape: 'star', size: 20 },
    completed: { shape: 'dot', size: 12 },
    failed: { shape: 'square', size: 18 },
    cancelled: { shape: 'dot', size: 10 },
    retrying: { shape: 'star', size: 18 },
    blocked: { shape: 'diamond', size: 15 },
  },
  layout: { randomSeed: 42, improvedLayout: true },
  interaction: { hover: true, tooltipDelay: 100, navigationButtons: true, keyboard: true },
};
const container = document.getElementById('mynetwork');
const network = new vis.Network(container, { nodes: tasksData, edges: edgesData }, options);

network.on('click', function(params) {
  if (params.nodes.length > 0) {
    const node = tasksData.get(params.nodes[0]);
    document.getElementById('detailPanel').innerHTML = node.title || 'No details';
  }
});

const byStatus = BY_STATUS_PLACEHOLDER;
const byType = BY_TYPE_PLACEHOLDER;
const statusColors = STATUS_COLORS_PLACEHOLDER;
let chartHtml = Object.entries(byStatus).map(function(e) {
  var k = e[0], v = e[1];
  return '<div style="display:flex;justify-content:space-between;padding:2px 0;">'
    + '<span style="color:" + (statusColors[k] || '#999') + "'>&#x25CF; ' + k + '</span>'
    + '<span>' + v + '</span></div>';
}).join('');
document.getElementById('statusChart').innerHTML = chartHtml || '<p>No data</p>';

document.getElementById('typeList').innerHTML = Object.entries(byType).map(function(e) {
  return '<li>' + e[0] + ': ' + e[1] + '</li>';
}).join('') || '<p>No data</p>';
</script>
</body>
</html>'''

    # Replace placeholders with actual values
    html = html.replace('TOTAL_PLACEHOLDER', str(total))
    html = html.replace('PENDING_PLACEHOLDER', str(pending))
    html = html.replace('RUNNING_PLACEHOLDER', str(running))
    html = html.replace('COMPLETED_PLACEHOLDER', str(completed))
    html = html.replace('FAILED_PLACEHOLDER', str(failed))
    html = html.replace('JSON_TASKS_PLACEHOLDER', str(tasks_json))
    html = html.replace('BY_STATUS_PLACEHOLDER', str(by_status))
    html = html.replace('BY_TYPE_PLACEHOLDER', str(by_type))
    import json as _jj
    html = html.replace('STATUS_COLORS_PLACEHOLDER', _jj.dumps(status_colors))
    return html

def main():
    import asyncio
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
