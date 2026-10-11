"""Run independent CAD jobs in killable child processes with item timeouts."""

from __future__ import annotations

import multiprocessing
import queue
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Sequence


@dataclass
class TimedProcessResult:
    task_id: str
    status: str
    value: Any = None
    error: str | None = None
    elapsed_seconds: float = 0.0


def _child_entry(result_queue, target: Callable[..., Any], args: Sequence[Any]) -> None:
    try:
        result_queue.put(("COMPLETED", target(*args), None))
    except BaseException as exc:  # child failures must be returned to the audit log
        result_queue.put((
            "FAILED",
            None,
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        ))


def run_one_timed_process(
    task_id: str,
    target: Callable[..., Any],
    args: Sequence[Any],
    timeout_seconds: float,
) -> TimedProcessResult:
    """Run one item in a process that can be terminated after the deadline."""
    started = time.monotonic()
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(target=_child_entry, args=(result_queue, target, args))
    process.start()
    try:
        status, value, error = result_queue.get(timeout=max(0.1, float(timeout_seconds)))
    except queue.Empty:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=2.0)
        return TimedProcessResult(
            task_id=task_id,
            status="TIMEOUT",
            error=f"Exceeded item timeout of {float(timeout_seconds):.1f} seconds",
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
    finally:
        result_queue.close()
    process.join(timeout=5.0)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2.0)
    return TimedProcessResult(
        task_id=task_id,
        status=status,
        value=value,
        error=error,
        elapsed_seconds=round(time.monotonic() - started, 3),
    )


def iter_timed_process_results(
    target: Callable[..., Any],
    tasks: Iterable[tuple[str, Sequence[Any]]],
    timeout_seconds: float,
    workers: int,
) -> Iterator[TimedProcessResult]:
    """Yield completed results while enforcing an independent timeout per item."""
    task_list = list(tasks)
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
        futures = {
            executor.submit(
                run_one_timed_process,
                task_id,
                target,
                args,
                timeout_seconds,
            ): task_id
            for task_id, args in task_list
        }
        for future in as_completed(futures):
            task_id = futures[future]
            try:
                yield future.result()
            except BaseException as exc:
                yield TimedProcessResult(
                    task_id=task_id,
                    status="FAILED",
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                )
