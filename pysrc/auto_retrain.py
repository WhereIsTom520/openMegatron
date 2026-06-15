"""Auto-retraining loop — online continuous learning for the reward model.

Monitors the trajectory store for new data. When enough new trajectories
accumulate, triggers retraining, evaluates the new model against the current
best, and auto-deploys if it's better. This closes the Phase 1→2→3 loop:
  agent interaction → trajectory → retrain → better model → agent uses it

Usage:
    # Manual trigger
    python -m pysrc.auto_retrain retrain --db .trajectory/trajectories.db

    # Daemon mode (runs in background, checks every N minutes)
    python -m pysrc.auto_retrain daemon --db .trajectory/trajectories.db --interval 30

    # Install into agent for automatic inline retraining
    from auto_retrain import install_auto_retrain
    install_auto_retrain(agent)
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from model_registry import ModelRegistry
from reward_model import SklearnRewardScorer, TorchRewardScorer, create_scorer
from reward_trainer import RewardTrainer
from trajectory_store import TrajectoryStore

logger = logging.getLogger(__name__)

# Default: retrain when 50+ new trajectories since last model
DEFAULT_RETRAIN_THRESHOLD = 50
# Default: check interval in seconds for daemon mode
DEFAULT_CHECK_INTERVAL = 600  # 10 minutes


class AutoRetrainLoop:
    """Monitors trajectory data and retrains the reward model when needed.

    Design:
      1. Tracks the trajectory count at last training time
      2. When (current_count - last_count) >= threshold, triggers retrain
      3. Trains new model, evaluates against current active
      4. Only deploys if new model has better F1
      5. Updates registry + swaps model in agent (if installed)
    """

    def __init__(
        self,
        trajectory_db: str = ".trajectory/trajectories.db",
        registry_db: str = ".trajectory/model_registry.db",
        model_dir: str = ".trajectory/models",
        backend: str = "sklearn",
        retrain_threshold: int = DEFAULT_RETRAIN_THRESHOLD,
    ):
        self._trajectory_db = trajectory_db
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._backend = backend
        self._retrain_threshold = retrain_threshold
        self._last_train_count = 0
        self._agent_ref: Any = None  # Optional reference to agent for hot-swap
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="openmegatron-auto-retrain",
        )
        self._retrain_future: Optional[concurrent.futures.Future] = None

        self._registry = ModelRegistry(db_path=registry_db)
        self._restore_state()

    def _restore_state(self) -> None:
        """Restore last_train_count from the active model's metadata."""
        active = self._registry.get_active()
        if active:
            self._last_train_count = active["metadata"].get("train_count", 0)
            logger.info(
                "AutoRetrain: restored state — last trained at %d trajectories, active model=%s",
                self._last_train_count, active["id"],
            )

    def bind_agent(self, agent: Any) -> None:
        """Bind an agent reference for hot-swapping the reward model."""
        self._agent_ref = agent

    def should_retrain(self) -> tuple[bool, int, int]:
        """Check if enough new data has accumulated.

        Returns:
            (should_retrain, current_total, new_since_last)
        """
        store = TrajectoryStore(db_path=self._trajectory_db)
        current_total = store.count()
        store.close()

        new_count = current_total - self._last_train_count
        return new_count >= self._retrain_threshold, current_total, new_count

    def trigger_retrain_async(self) -> bool:
        """Submit a retrain cycle to the background worker if one is not running.

        Returns True when a new background job was submitted, False when an
        existing job is already in flight.
        """
        if self._retrain_future is not None and not self._retrain_future.done():
            return False

        self._retrain_future = self._executor.submit(self.retrain)

        def _log_result(future: concurrent.futures.Future) -> None:
            try:
                result = future.result()
                logger.info("AutoRetrain: background cycle finished: %s", result.get("status"))
            except Exception:
                logger.exception("AutoRetrain: background cycle failed")

        self._retrain_future.add_done_callback(_log_result)
        return True

    def retrain(self) -> dict:
        """Run a full retrain cycle: train, evaluate, deploy if better.

        Returns:
            Dict with full cycle results for logging/reporting.
        """
        store = TrajectoryStore(db_path=self._trajectory_db)
        current_total = store.count()

        if current_total < 10:
            store.close()
            return {
                "status": "skipped",
                "reason": "insufficient_data",
                "n_samples": current_total,
                "min_required": 10,
            }

        logger.info("AutoRetrain: starting retrain cycle with %d trajectories", current_total)

        # ── Train new model ──
        scorer = create_scorer(self._backend)
        trainer = RewardTrainer(store, scorer)
        train_metrics = trainer.train()

        if "error" in train_metrics:
            store.close()
            return {"status": "error", **train_metrics}

        # ── Evaluate on full dataset ──
        eval_metrics = scorer.evaluate(store)
        store.close()

        # ── Save model ──
        version = self._registry.count() + 1
        ext = ".pt" if self._backend == "torch" else ".pkl"
        model_path = str(self._model_dir / f"reward_v{version}_{int(time.time())}{ext}")
        scorer.save(model_path)

        # ── Compare with current active ──
        active = self._registry.get_active()
        deploy = True
        reason = "first_model"

        if active and os.path.exists(active["file_path"]):
            # Run regression guard before deploying
            try:
                from regression_guard import RegressionGuard
                store2 = TrajectoryStore(db_path=self._trajectory_db)
                guard = RegressionGuard()
                validation = guard.validate(model_path, active["file_path"], store2)
                store2.close()
                if not validation["passed"]:
                    deploy = False
                    reason = f"regression_blocked: {validation['summary']}"
                else:
                    current_f1 = active["f1"]
                    new_f1 = eval_metrics["f1"]
                    if new_f1 <= current_f1:
                        deploy = False
                        reason = f"no_improvement (new_f1={new_f1:.4f} <= current_f1={current_f1:.4f})"
                    else:
                        reason = f"improved (new_f1={new_f1:.4f} > current_f1={current_f1:.4f})"
            except Exception as exc:
                logger.warning("Regression guard skipped: %s", exc)
                current_f1 = active["f1"]
                new_f1 = eval_metrics["f1"]
                if new_f1 <= current_f1:
                    deploy = False
                    reason = f"no_improvement (new_f1={new_f1:.4f} <= current_f1={current_f1:.4f})"
                else:
                    reason = f"improved (new_f1={new_f1:.4f} > current_f1={current_f1:.4f})"

        # ── Register ──
        mid = self._registry.register(
            file_path=model_path,
            backend=self._backend,
            accuracy=eval_metrics["accuracy"],
            f1=eval_metrics["f1"],
            n_samples=current_total,
            metadata={
                "train_count": current_total,
                "train_metrics": train_metrics,
                "eval_metrics": eval_metrics,
                "deploy_reason": reason,
            },
            activate=deploy,
            status="trained" if deploy else "candidate",
        )

        # ── Hot-swap in agent if bound ──
        if deploy and self._agent_ref is not None:
            try:
                from reward_integration import install_reward_model
                install_reward_model(self._agent_ref, model_path, backend=self._backend)
                logger.info("AutoRetrain: hot-swapped reward model in agent → %s", mid)
            except Exception as exc:
                logger.warning("AutoRetrain: hot-swap failed: %s", exc)

        self._last_train_count = current_total

        result = {
            "status": "deployed" if deploy else "trained_not_deployed",
            "model_id": mid,
            "model_path": model_path,
            "n_samples": current_total,
            "new_since_last": current_total - (active["metadata"].get("train_count", 0) if active else 0),
            "accuracy": eval_metrics["accuracy"],
            "f1": eval_metrics["f1"],
            "previous_f1": active["f1"] if active else None,
            "deploy_reason": reason,
            "train_metrics": train_metrics,
        }

        logger.info(
            "AutoRetrain: cycle complete — %s (f1=%.4f, reason=%s)",
            result["status"], eval_metrics["f1"], reason,
        )
        return result

    def get_status(self) -> dict:
        """Get current loop status for monitoring."""
        should, total, new = self.should_retrain()
        active = self._registry.get_active()
        return {
            "total_trajectories": total,
            "new_since_last_train": new,
            "retrain_threshold": self._retrain_threshold,
            "should_retrain": should,
            "active_model": active["id"] if active else None,
            "active_model_f1": active["f1"] if active else None,
            "total_models": self._registry.count(),
        }

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._registry.close()


# ── Async daemon ─────────────────────────────────────────────────────────────

async def run_daemon(
    trajectory_db: str = ".trajectory/trajectories.db",
    registry_db: str = ".trajectory/model_registry.db",
    model_dir: str = ".trajectory/models",
    backend: str = "sklearn",
    retrain_threshold: int = DEFAULT_RETRAIN_THRESHOLD,
    check_interval: int = DEFAULT_CHECK_INTERVAL,
) -> None:
    """Run the auto-retrain loop as a background daemon.

    Checks for new data every check_interval seconds. When enough new
    trajectories accumulate, triggers retraining automatically.
    """
    loop = AutoRetrainLoop(
        trajectory_db=trajectory_db,
        registry_db=registry_db,
        model_dir=model_dir,
        backend=backend,
        retrain_threshold=retrain_threshold,
    )

    logger.info(
        "AutoRetrain daemon started — threshold=%d, interval=%ds",
        retrain_threshold, check_interval,
    )

    try:
        while True:
            status = loop.get_status()
            logger.debug(
                "AutoRetrain check: total=%d new=%d should_retrain=%s",
                status["total_trajectories"],
                status["new_since_last_train"],
                status["should_retrain"],
            )

            if status["should_retrain"]:
                result = loop.retrain()
                print(f"[AutoRetrain] {json.dumps(result, ensure_ascii=False)}")

            await asyncio.sleep(check_interval)
    except asyncio.CancelledError:
        logger.info("AutoRetrain daemon cancelled")
    finally:
        loop.close()


# ── Agent integration ────────────────────────────────────────────────────────

def install_auto_retrain(
    agent: Any,
    trajectory_db: str = ".trajectory/trajectories.db",
    registry_db: str = ".trajectory/model_registry.db",
    model_dir: str = ".trajectory/models",
    backend: str = "sklearn",
    retrain_threshold: int = DEFAULT_RETRAIN_THRESHOLD,
) -> AutoRetrainLoop:
    """Install auto-retrain into an agent.

    Binds the agent for hot-swapping and hooks into the trajectory collector
    to check if retraining is needed after each interaction.

    Args:
        agent: YuanGeAgent instance.
        trajectory_db: Path to trajectory SQLite DB.
        registry_db: Path to model registry SQLite DB.
        model_dir: Directory for saved model files.
        backend: "sklearn" or "torch".
        retrain_threshold: Number of new trajectories before retraining.

    Returns:
        AutoRetrainLoop instance.
    """
    loop = AutoRetrainLoop(
        trajectory_db=trajectory_db,
        registry_db=registry_db,
        model_dir=model_dir,
        backend=backend,
        retrain_threshold=retrain_threshold,
    )
    loop.bind_agent(agent)

    # Hook: after each trajectory collection, check if we should retrain
    original_collect = None
    if hasattr(agent, "_trajectory_collector") and agent._trajectory_collector:
        original_collect = agent._trajectory_collector.collect

    async def _collect_and_maybe_retrain(trace: dict, source: str = "openmegatron") -> Optional[str]:
        tid = None
        if original_collect:
            try:
                tid = await original_collect(trace, source)
            except Exception:
                pass

        # Check if we've crossed the threshold (fire-and-forget, non-blocking)
        try:
            should, total, new = loop.should_retrain()
            if should:
                logger.info("AutoRetrain: threshold reached (%d new), triggering retrain", new)
                loop.trigger_retrain_async()
        except Exception:
            logger.debug("AutoRetrain check skipped", exc_info=True)

        return tid

    if hasattr(agent, "_trajectory_collector") and agent._trajectory_collector:
        agent._trajectory_collector.collect = _collect_and_maybe_retrain

    # Store reference
    agent._auto_retrain = loop

    logger.info(
        "AutoRetrain installed — threshold=%d trajectories, backend=%s",
        retrain_threshold, backend,
    )
    return loop


# ── CLI ──────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Auto-retrain loop — online continuous learning for reward model",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # retrain (manual trigger)
    retrain_cmd = sub.add_parser("retrain", help="Manually trigger a retrain cycle")
    retrain_cmd.add_argument("--db", default=".trajectory/trajectories.db")
    retrain_cmd.add_argument("--registry", default=".trajectory/model_registry.db")
    retrain_cmd.add_argument("--model-dir", default=".trajectory/models")
    retrain_cmd.add_argument("--backend", default="sklearn", choices=["sklearn", "torch"])

    # status
    status_cmd = sub.add_parser("status", help="Show auto-retrain status")
    status_cmd.add_argument("--db", default=".trajectory/trajectories.db")
    status_cmd.add_argument("--registry", default=".trajectory/model_registry.db")

    # daemon
    daemon_cmd = sub.add_parser("daemon", help="Run auto-retrain daemon")
    daemon_cmd.add_argument("--db", default=".trajectory/trajectories.db")
    daemon_cmd.add_argument("--registry", default=".trajectory/model_registry.db")
    daemon_cmd.add_argument("--model-dir", default=".trajectory/models")
    daemon_cmd.add_argument("--backend", default="sklearn", choices=["sklearn", "torch"])
    daemon_cmd.add_argument("--threshold", type=int, default=DEFAULT_RETRAIN_THRESHOLD)
    daemon_cmd.add_argument("--interval", type=int, default=DEFAULT_CHECK_INTERVAL,
                            help="Check interval in seconds (default: 600 = 10min)")

    # models
    models_cmd = sub.add_parser("models", help="List registered model versions")
    models_cmd.add_argument("--registry", default=".trajectory/model_registry.db")

    return parser


def main(argv: list[str] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "models":
        registry = ModelRegistry(db_path=args.registry)
        models = registry.list_all()
        for m in models:
            active = "★" if m["is_active"] else " "
            print(f"{active} {m['id']} | f1={m['f1']:.4f} | acc={m['accuracy']:.4f} | n={m['n_samples']} | {m['status']} | {m['created_at']}")
        print(f"\n{len(models)} model(s) total")
        registry.close()
        return

    if args.command == "status":
        loop = AutoRetrainLoop(
            trajectory_db=args.db,
            registry_db=args.registry,
        )
        status = loop.get_status()
        print(json.dumps(status, indent=2, ensure_ascii=False))
        loop.close()
        return

    if args.command == "retrain":
        loop = AutoRetrainLoop(
            trajectory_db=args.db,
            registry_db=args.registry,
            model_dir=args.model_dir,
            backend=args.backend,
        )
        result = loop.retrain()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        loop.close()
        return

    if args.command == "daemon":
        logging.basicConfig(level=logging.INFO)
        try:
            asyncio.run(run_daemon(
                trajectory_db=args.db,
                registry_db=args.registry,
                model_dir=args.model_dir,
                backend=args.backend,
                retrain_threshold=args.threshold,
                check_interval=args.interval,
            ))
        except KeyboardInterrupt:
            print("\nAutoRetrain daemon stopped.")
        return


if __name__ == "__main__":
    main()
