"""Cancellation-safe helpers for blocking Parquet file operations."""

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar


T = TypeVar("T")


async def _wait_for_task(task: asyncio.Task[T]) -> T:
    """Wait for a task to finish despite repeated caller cancellation."""

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break

    return task.result()


async def run_blocking_safely(
    function: Callable[..., T],
    *args: Any,
    cancel_cleanup: Callable[[T], Any] | None = None,
) -> T:
    """Run blocking work in a thread without racing cancellation cleanup.

    If the caller is cancelled, wait for the worker thread to finish before
    propagating cancellation. When supplied, ``cancel_cleanup`` is executed
    with the worker result before cancellation is propagated.
    """

    task = asyncio.create_task(
        asyncio.to_thread(function, *args)
    )

    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            result = await _wait_for_task(task)
        except BaseException:
            # Preserve the caller's cancellation rather than replacing it
            # with an exception raised by the worker.
            pass
        else:
            if cancel_cleanup is not None:
                cleanup_task = asyncio.create_task(
                    asyncio.to_thread(cancel_cleanup, result)
                )

                try:
                    await _wait_for_task(cleanup_task)
                except BaseException:
                    # Cancellation remains the primary outcome. Cleanup
                    # has nevertheless been awaited to avoid a race.
                    pass

        raise