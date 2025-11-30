from __future__ import annotations
from abc import ABC, abstractmethod
import asyncio
import threading
from typing import List, Optional
import logging
import warnings
from funcnodes_core import NodeSpace
import time
import weakref

MIN_DELAY = 0.1
MIN_DEF = 0.1


class CustomLoop(ABC):
    def __init__(self, delay=0.1, logger: logging.Logger | None = None) -> None:
        if delay < MIN_DELAY:
            delay = MIN_DELAY
        self._delay = delay
        if logger is None:
            logger = logging.getLogger(f"{self.__class__.__name__}")
        self._logger = logger
        self._running = False
        self._stopped = False
        self._paused = False
        self._manager: Optional[LoopManager] = None
        self._stop_event = asyncio.Event()

    @property
    def manager(self) -> Optional[LoopManager]:
        return self._manager

    @manager.setter
    def manager(self, manager: Optional[LoopManager]):
        if manager is not None and self._manager is not None:
            raise ValueError("Loop already has a manager")

        if manager is None and self._manager is not None:
            self._manager.remove_loop(self)

        if manager is self._manager:
            return

        self._manager = manager

    @abstractmethod
    async def loop(self):
        """This method is called in a loop every <self.delay> seconds ."""

    async def _loop(self):
        return await self.loop()

    async def stop(self):
        self._running = False
        self.manager = None
        await asyncio.sleep(min(self._delay, MIN_DEF) * 1.25)
        self._stopped = True
        # in case parent class has a cleanup method
        if hasattr(super(), "stop"):
            super().stop()

    @property
    def running(self):
        return self._running

    @property
    def stopped(self):
        return self._stopped and not self._running

    async def continuous_run(self):
        last_run = 0
        self._running = True
        while self.running:
            try:
                if (time.time() - self._delay > last_run) and not self._paused:
                    await self._loop()
                    last_run = time.time()
            except Exception as exc:  # pylint: disable=broad-except
                self._logger.exception(exc)

            await asyncio.sleep(min(self._delay, MIN_DEF))

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def resume_in(self, delay: float):
        async def _resume():
            await asyncio.sleep(delay)
            self.resume()

        if self.manager:
            self.manager.async_call(_resume())
        else:
            asyncio.create_task(_resume())


class LoopManager:
    def __init__(self, worker) -> None:
        self._loops: List[CustomLoop] = []
        self._loop: asyncio.AbstractEventLoop = None  # type: ignore
        self._worker = weakref.ref(worker)
        self.reset_loop()
        self._loop_tasks: List[asyncio.Task] = []
        self._running = False
        self._loops_to_add = []
        self._async_tasks = []

    def reset_loop(self):
        try:
            # Try to get the running loop first (Python 3.7+)
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop, try to get the current event loop
            # Suppress deprecation warning for get_event_loop() in Python 3.13+
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=DeprecationWarning)
                try:
                    self._loop = asyncio.get_event_loop()
                except RuntimeError as e:
                    error_msg = str(e)
                    if (
                        "There is no current event loop" in error_msg
                        or "There is no running event loop" in error_msg
                    ):
                        self._loop = asyncio.new_event_loop()
                    # asyncio.set_event_loop(self._loop)
                    else:
                        raise

    def add_loop(self, loop: CustomLoop):
        if self._running:
            self._loops.append(loop)
            loop.manager = self

            async def looprunner():
                await loop.continuous_run()
                self.remove_loop(loop)

            t = self.async_call(looprunner())
            self._loop_tasks.append(t)
            return t
        else:
            self._loops_to_add.append(loop)

    def remove_loop(self, loop: CustomLoop):
        # check if self._loop is running as the current loop
        is_running = (
            self._loop.is_running()
        )  # and asyncio.get_event_loop() == self._loop

        if loop.running and not self._loop.is_closed():
            loop._running = False  # set this to false prevent recursion
            try:
                if not is_running:
                    self._loop.run_until_complete(loop.stop())
                else:
                    _ = self.async_call(loop.stop())
            except Exception as e:
                worker = self._worker()
                if worker is not None:
                    worker.logger.exception(e)
                raise e

        if loop in self._loops:
            idx = self._loops.index(loop)
            task = self._loop_tasks.pop(idx)
            self._loops.pop(idx)
            self._cancel_and_await_task(task, is_running)

    def _cancel_and_await_task(self, task: asyncio.Task, is_running: bool):
        """Cancel a task and ensure the cancellation is awaited to release references."""

        async def _wait_cancel(t: asyncio.Task):
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover - defensive
                worker = self._worker()
                if worker is not None:
                    worker.logger.exception(exc)

        if task.done():
            try:
                _ = task.exception()
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover - defensive
                worker = self._worker()
                if worker is not None:
                    worker.logger.exception(exc)
            return

        task.cancel()

        waiter = _wait_cancel(task)
        if not is_running and not self._loop.is_closed():
            try:
                self._loop.run_until_complete(waiter)
            except Exception as exc:  # pragma: no cover - defensive
                worker = self._worker()
                if worker is not None:
                    worker.logger.exception(exc)
        else:
            scheduled = self.async_call(waiter)
            if scheduled is None:
                # no loop to schedule on; close coroutine to avoid "never awaited"
                waiter.close()

    def async_call(self, croutine: asyncio.Coroutine):
        # Check if the loop is closed or not running

        self._async_tasks: List[asyncio.Task] = [
            t for t in self._async_tasks if not t.done() and not t.cancelled()
        ]

        if self._loop.is_closed():
            # Try to get the running loop instead
            try:
                running_loop = asyncio.get_running_loop()
                task = running_loop.create_task(croutine)
                self._async_tasks.append(task)
                return task
            except RuntimeError:
                # No running loop available, skip creating the task
                worker = self._worker()
                if worker is not None:
                    worker.logger.warning(
                        "Cannot create task: event loop is closed and no running loop available"
                    )
                # close coroutine to avoid runtime warning about never awaited
                croutine.close()
                return None

        # Check if the loop is running
        if not self._loop.is_running():
            # Try to get the running loop instead
            try:
                running_loop = asyncio.get_running_loop()
                task = running_loop.create_task(croutine)
                self._async_tasks.append(task)
                return task
            except RuntimeError:
                # No running loop available, but our loop exists, try to use it
                # cannot schedule; close coroutine to avoid warnings
                croutine.close()
                return None

        task = self._loop.create_task(croutine)
        self._async_tasks.append(task)
        return task

    def __del__(self):
        self.stop()

    def stop(self):
        self._running = False
        for loop in list(self._loops):
            self.remove_loop(loop)

        self._async_tasks: List[asyncio.Task] = [
            t for t in self._async_tasks if not t.done() and not t.cancelled()
        ]

        is_running = self._loop.is_running()

        # Give pending async_call tasks a brief chance to finish when loop is running
        grace_handled = False
        if is_running and self._async_tasks:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            same_thread = running_loop is self._loop
            if not same_thread:
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        asyncio.wait(list(self._async_tasks), timeout=1),
                        self._loop,
                    )
                    fut.result(timeout=2)
                    grace_handled = True
                except Exception as exc:  # pragma: no cover - defensive
                    worker = self._worker()
                    if worker is not None:
                        worker.logger.exception(exc)
            else:

                async def _grace_wait(tasks: list[asyncio.Task]):
                    try:
                        await asyncio.wait(tasks, timeout=1)
                    except Exception as exc:  # pragma: no cover - defensive
                        worker = self._worker()
                        if worker is not None:
                            worker.logger.exception(exc)
                    finally:
                        for task in list(tasks):
                            self._cancel_and_await_task(task, True)

                self._loop.create_task(_grace_wait(list(self._async_tasks)))
                grace_handled = True

        # Cancel and await all loop tasks
        for task in list(self._loop_tasks):
            self._cancel_and_await_task(task, is_running)

        # Cancel and await async_call tasks
        if not grace_handled:
            for task in list(self._async_tasks):
                self._cancel_and_await_task(task, is_running)

    @property
    def running(self) -> bool:
        """Returns True if the loop manager is running."""
        return self._running

    def _prerun(self):
        worker = self._worker()
        if worker is not None:
            worker.logger.info("Setup loop manager to run")
        self._running = True
        loops2add = list(self._loops_to_add)
        self._loops_to_add = []
        for loop in loops2add:
            self.add_loop(loop)
        if worker is not None:
            worker.logger.info("Starting loop manager")

    def run_forever(self, reset_loop: bool = False):
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if reset_loop:
            self.reset_loop()
        asyncio.set_event_loop(self._loop)
        self._prerun()

        async def _rf():
            while self.running:
                await asyncio.sleep(1)

        try:
            self._loop.run_until_complete(_rf())
        except KeyboardInterrupt:
            print("Interrupt received, shutting down.")
            worker = self._worker()
            if worker is not None:
                worker.stop()
        except Exception as e:
            worker = self._worker()
            if worker is not None:
                worker.logger.exception(e)
            raise e
        finally:
            self.stop()
            if running_loop is not None:
                asyncio.set_event_loop(running_loop)

    def run_forever_threaded(self):
        thread = threading.Thread(target=self.run_forever, kwargs={"reset_loop": True})
        thread.start()
        return thread

    async def run_forever_async(self):
        self._prerun()

        while self.running:
            await asyncio.sleep(1)


class NodeSpaceLoop(CustomLoop):
    def __init__(self, nodespace: NodeSpace, delay=0.005) -> None:
        super().__init__(delay)
        self._nodespace = nodespace

    async def loop(self):
        await self._nodespace.await_done()
