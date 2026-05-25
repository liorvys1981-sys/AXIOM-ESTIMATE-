"""
AXIOM Self-Healing Watchdog
Continuous autonomous monitoring for VRAM anomalies, Celery worker
health, Redis queue depth, and process lifecycle management.
Targets 99.9% uptime without human intervention.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psutil
import redis as redis_sync
import structlog

# ---------------------------------------------------------------------------
# Logging setup — structured JSON for log aggregation pipelines
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger("axiom.watchdog")

# ---------------------------------------------------------------------------
# Configuration — pulled from environment with safe defaults
# ---------------------------------------------------------------------------

REDIS_URL              = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_APP             = os.getenv("CELERY_APP", "axiom.agent_core")
CELERY_CONCURRENCY     = int(os.getenv("CELERY_CONCURRENCY", "4"))
CELERY_LOGLEVEL        = os.getenv("CELERY_LOGLEVEL", "info")

MEMORY_WARN_PCT        = float(os.getenv("MEMORY_WARN_PCT", "75.0"))
MEMORY_CRITICAL_PCT    = float(os.getenv("MEMORY_CRITICAL_PCT", "90.0"))
QUEUE_DEPTH_WARN       = int(os.getenv("QUEUE_DEPTH_WARN", "50"))
QUEUE_DEPTH_CRITICAL   = int(os.getenv("QUEUE_DEPTH_CRITICAL", "200"))
CPU_CRITICAL_PCT       = float(os.getenv("CPU_CRITICAL_PCT", "95.0"))
DISK_CRITICAL_PCT      = float(os.getenv("DISK_CRITICAL_PCT", "90.0"))

POLL_INTERVAL_SECS     = int(os.getenv("WATCHDOG_POLL_SECS", "15"))
WORKER_PING_TIMEOUT    = int(os.getenv("WORKER_PING_TIMEOUT", "5"))
MAX_RESTART_ATTEMPTS   = int(os.getenv("MAX_RESTART_ATTEMPTS", "5"))
RESTART_BACKOFF_BASE   = float(os.getenv("RESTART_BACKOFF_BASE", "2.0"))   # exponential
STALE_WORKER_THRESHOLD = int(os.getenv("STALE_WORKER_THRESHOLD_SECS", "300"))

WATCHED_QUEUES         = os.getenv("CELERY_QUEUES", "celery,estimate,claims,total_loss,lien,audit,cpo,gpu_resell").split(",")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SystemSnapshot:
    timestamp:         datetime
    ram_used_pct:      float
    ram_used_bytes:    int
    ram_total_bytes:   int
    swap_used_pct:     float
    cpu_pct:           float
    disk_used_pct:     float
    disk_free_bytes:   int
    queue_depths:      dict[str, int] = field(default_factory=dict)
    worker_alive:      bool           = False
    worker_count:      int            = 0
    active_task_count: int            = 0


@dataclass
class WatchdogState:
    restart_attempts:    int            = 0
    last_restart_at:     float          = 0.0   # epoch
    consecutive_healthy: int            = 0
    restart_history:     list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

def get_redis_client() -> redis_sync.Redis:
    return redis_sync.from_url(REDIS_URL, socket_connect_timeout=3, socket_timeout=3)


def get_queue_depths(r: redis_sync.Redis) -> dict[str, int]:
    """
    Return the number of pending messages in each Celery queue.
    Celery uses standard Redis lists for the default broker transport.
    """
    depths: dict[str, int] = {}
    for queue in WATCHED_QUEUES:
        try:
            depths[queue] = r.llen(queue) or 0
        except Exception:
            depths[queue] = -1   # -1 signals "unable to read"
    return depths


# ---------------------------------------------------------------------------
# System metrics collection
# ---------------------------------------------------------------------------

def collect_snapshot() -> SystemSnapshot:
    """Collect a point-in-time system metrics snapshot."""
    ram    = psutil.virtual_memory()
    swap   = psutil.swap_memory()
    cpu    = psutil.cpu_percent(interval=1)
    disk   = psutil.disk_usage("/")

    try:
        r = get_redis_client()
        queue_depths = get_queue_depths(r)
        r.close()
    except Exception as exc:
        log.warning("redis_unreachable", error=str(exc))
        queue_depths = {q: -1 for q in WATCHED_QUEUES}

    return SystemSnapshot(
        timestamp       = datetime.now(timezone.utc),
        ram_used_pct    = ram.percent,
        ram_used_bytes  = ram.used,
        ram_total_bytes = ram.total,
        swap_used_pct   = swap.percent,
        cpu_pct         = cpu,
        disk_used_pct   = disk.percent,
        disk_free_bytes = disk.free,
        queue_depths    = queue_depths,
    )


# ---------------------------------------------------------------------------
# Celery worker inspection
# ---------------------------------------------------------------------------

def ping_celery_workers(timeout: int = WORKER_PING_TIMEOUT) -> tuple[bool, int, int]:
    """
    Ping the Celery worker cluster via CLI.
    Returns (alive: bool, worker_count: int, active_tasks: int).
    """
    try:
        result = subprocess.run(
            ["celery", "-A", CELERY_APP, "inspect", "ping",
             "--timeout", str(timeout), "--json"],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        if result.returncode != 0 or "OK" not in result.stdout:
            return False, 0, 0

        # Count worker responses — each responding worker has an "OK" entry
        worker_count = result.stdout.count('"ok"')
        return True, worker_count, 0

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as exc:
        log.warning("celery_ping_failed", error=str(exc))
        return False, 0, 0


def get_active_task_count() -> int:
    """Query how many tasks are currently being processed."""
    try:
        result = subprocess.run(
            ["celery", "-A", CELERY_APP, "inspect", "active", "--json"],
            capture_output=True,
            text=True,
            timeout=WORKER_PING_TIMEOUT + 2,
        )
        if result.returncode != 0:
            return 0
        # Each task appears as a JSON object in the output
        return result.stdout.count('"id"')
    except Exception:
        return 0


def find_stale_celery_processes() -> list[psutil.Process]:
    """
    Detect Celery worker processes that have been running longer than
    STALE_WORKER_THRESHOLD seconds without completing (zombie workers).
    """
    stale: list[psutil.Process] = []
    now = time.time()
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time", "status"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "celery" not in cmdline:
                continue
            if proc.info.get("status") == psutil.STATUS_ZOMBIE:
                stale.append(proc)
                continue
            age = now - proc.info.get("create_time", now)
            if age > STALE_WORKER_THRESHOLD:
                stale.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return stale


# ---------------------------------------------------------------------------
# Recovery actions
# ---------------------------------------------------------------------------

def flush_ram_caches() -> None:
    """
    Attempt to free OS page cache and reclaimable memory.
    Runs as a best-effort — silently skips if insufficient privileges.
    """
    try:
        with open("/proc/sys/vm/drop_caches", "w") as f:
            f.write("1\n")   # 1 = page cache only (safe at runtime)
        log.info("ram_cache_flushed")
    except (PermissionError, FileNotFoundError):
        log.debug("ram_cache_flush_skipped", reason="insufficient_privilege_or_non_linux")


def kill_stale_workers(stale: list[psutil.Process]) -> int:
    """
    Send SIGTERM to stale worker processes. Returns count killed.
    """
    killed = 0
    for proc in stale:
        try:
            proc.send_signal(signal.SIGTERM)
            log.warning("stale_worker_terminated", pid=proc.pid)
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.warning("worker_kill_failed", pid=proc.pid, error=str(exc))
    return killed


def spawn_celery_worker(state: WatchdogState) -> bool:
    """
    Spawn a new Celery worker process. Implements exponential back-off
    to avoid restart storms. Returns True if a new process was launched.
    """
    if state.restart_attempts >= MAX_RESTART_ATTEMPTS:
        log.error(
            "max_restarts_reached",
            attempts=state.restart_attempts,
            limit=MAX_RESTART_ATTEMPTS,
        )
        return False

    # Exponential back-off: 2^n seconds (capped at 60 s)
    backoff = min(RESTART_BACKOFF_BASE ** state.restart_attempts, 60.0)
    elapsed = time.time() - state.last_restart_at
    if elapsed < backoff:
        log.info(
            "restart_backoff_active",
            wait_remaining_secs=round(backoff - elapsed, 1),
        )
        return False

    cmd = [
        "celery", "-A", CELERY_APP, "worker",
        "--loglevel", CELERY_LOGLEVEL,
        f"--concurrency={CELERY_CONCURRENCY}",
        "--without-gossip",
        "--without-mingle",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,   # detach from watchdog's process group
        )
        state.restart_attempts += 1
        state.last_restart_at   = time.time()
        state.restart_history.append({
            "attempt":   state.restart_attempts,
            "pid":       proc.pid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        log.info(
            "celery_worker_spawned",
            pid=proc.pid,
            attempt=state.restart_attempts,
        )
        return True
    except Exception as exc:
        log.error("worker_spawn_failed", error=str(exc), exc_info=True)
        return False


def purge_overflow_queue(queue_name: str) -> int:
    """
    Purge a Celery queue that has exceeded QUEUE_DEPTH_CRITICAL.
    Returns the number of messages purged.
    """
    try:
        result = subprocess.run(
            ["celery", "-A", CELERY_APP, "amqp", "queue.purge", queue_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            log.warning("queue_purged_overflow", queue=queue_name)
            return 1
        return 0
    except Exception as exc:
        log.error("queue_purge_failed", queue=queue_name, error=str(exc))
        return 0


# ---------------------------------------------------------------------------
# Evaluation logic — decide what action (if any) to take from a snapshot
# ---------------------------------------------------------------------------

def evaluate_snapshot(
    snap: SystemSnapshot,
    state: WatchdogState,
) -> list[str]:
    """
    Inspect a SystemSnapshot and return a list of action keys to execute.
    This is pure logic — no side effects — making it fully unit-testable.
    """
    actions: list[str] = []

    # — RAM —
    if snap.ram_used_pct >= MEMORY_CRITICAL_PCT:
        actions.append("flush_ram")
        log.warning(
            "memory_critical",
            used_pct=snap.ram_used_pct,
            threshold=MEMORY_CRITICAL_PCT,
            free_mb=round((snap.ram_total_bytes - snap.ram_used_bytes) / 1024 ** 2, 1),
        )
    elif snap.ram_used_pct >= MEMORY_WARN_PCT:
        log.warning("memory_warning", used_pct=snap.ram_used_pct, threshold=MEMORY_WARN_PCT)

    # — CPU —
    if snap.cpu_pct >= CPU_CRITICAL_PCT:
        log.error("cpu_saturation", cpu_pct=snap.cpu_pct, threshold=CPU_CRITICAL_PCT)

    # — Disk —
    if snap.disk_used_pct >= DISK_CRITICAL_PCT:
        log.error(
            "disk_critical",
            used_pct=snap.disk_used_pct,
            free_gb=round(snap.disk_free_bytes / 1024 ** 3, 2),
        )

    # — Celery workers —
    if not snap.worker_alive:
        actions.append("spawn_worker")
        log.error("celery_workers_offline")

    # — Queue depth —
    for queue, depth in snap.queue_depths.items():
        if depth == -1:
            log.warning("queue_read_failed", queue=queue)
        elif depth >= QUEUE_DEPTH_CRITICAL:
            actions.append(f"purge_queue:{queue}")
            log.error("queue_overflow", queue=queue, depth=depth, threshold=QUEUE_DEPTH_CRITICAL)
        elif depth >= QUEUE_DEPTH_WARN:
            log.warning("queue_deep", queue=queue, depth=depth, threshold=QUEUE_DEPTH_WARN)
            if not snap.worker_alive:
                actions.append("spawn_worker")

    return actions


def execute_actions(actions: list[str], state: WatchdogState) -> None:
    """Execute the action list produced by evaluate_snapshot."""
    seen_spawn = False
    for action in actions:
        if action == "flush_ram":
            flush_ram_caches()

        elif action == "spawn_worker" and not seen_spawn:
            # Kill any zombies first so we don't double up
            stale = find_stale_celery_processes()
            if stale:
                killed = kill_stale_workers(stale)
                log.info("stale_workers_reaped", count=killed)
                time.sleep(1)   # allow OS to clean up PIDs
            spawned = spawn_celery_worker(state)
            if spawned:
                seen_spawn = True
                # After a successful restart, give the worker time to come up
                # before the next health poll evaluates it
                time.sleep(3)

        elif action.startswith("purge_queue:"):
            queue_name = action.split(":", 1)[1]
            purge_overflow_queue(queue_name)


# ---------------------------------------------------------------------------
# Main watchdog loop
# ---------------------------------------------------------------------------

def run_watchdog() -> None:
    """
    Blocking watchdog loop. Runs indefinitely; designed to be the
    PID 1 entrypoint of the watchdog container or a supervised process.
    Handles SIGTERM and SIGINT for graceful shutdown.
    """
    state = WatchdogState()
    _shutdown = False

    def _handle_shutdown(signum: int, _frame: Any) -> None:
        nonlocal _shutdown
        log.info("watchdog_shutdown_signal", signal=signum)
        _shutdown = True

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT,  _handle_shutdown)

    log.info(
        "watchdog_started",
        poll_interval=POLL_INTERVAL_SECS,
        memory_warn_pct=MEMORY_WARN_PCT,
        memory_critical_pct=MEMORY_CRITICAL_PCT,
        queue_depth_warn=QUEUE_DEPTH_WARN,
        queue_depth_critical=QUEUE_DEPTH_CRITICAL,
    )

    while not _shutdown:
        cycle_start = time.monotonic()

        try:
            snap = collect_snapshot()

            # Enrich snapshot with Celery live data
            alive, wcount, active = ping_celery_workers()
            snap.worker_alive      = alive
            snap.worker_count      = wcount
            snap.active_task_count = active

            # Log periodic health summary (every poll)
            log.info(
                "health_check",
                ram_pct=snap.ram_used_pct,
                cpu_pct=snap.cpu_pct,
                disk_pct=snap.disk_used_pct,
                workers_alive=alive,
                worker_count=wcount,
                active_tasks=active,
                queues=snap.queue_depths,
            )

            actions = evaluate_snapshot(snap, state)

            if actions:
                execute_actions(actions, state)
            else:
                state.consecutive_healthy += 1
                # After 10 clean polls in a row, reset restart counter
                if state.consecutive_healthy >= 10 and state.restart_attempts > 0:
                    log.info(
                        "restart_counter_reset",
                        after_consecutive_healthy=state.consecutive_healthy,
                    )
                    state.restart_attempts    = 0
                    state.consecutive_healthy = 0

        except Exception as exc:
            log.error("watchdog_cycle_exception", error=str(exc), exc_info=True)

        # Sleep for the remainder of the poll interval (drift-compensated)
        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, POLL_INTERVAL_SECS - elapsed)
        time.sleep(sleep_for)

    log.info("watchdog_stopped")


# ---------------------------------------------------------------------------
# Async wrapper (for embedding inside FastAPI lifespan if needed)
# ---------------------------------------------------------------------------

async def run_watchdog_async() -> None:
    """Run the watchdog in a thread-pool executor so it doesn't block the event loop."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_watchdog)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_watchdog()
