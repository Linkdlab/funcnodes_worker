import asyncio
import logging
from contextlib import suppress
import time
from unittest.mock import AsyncMock, Mock

import pytest

from funcnodes_worker.loop import (
    CustomLoop,
    LoopManager,
)

from pytest_funcnodes import funcnodes_test

pytestmark = pytest.mark.asyncio


class _TestLoop(CustomLoop):
    async def loop(self):
        pass


@pytest.fixture
def logger():
    return logging.getLogger("TestLogger")


@pytest.fixture
def test_loop(logger):
    loop = _TestLoop(delay=0.2, logger=logger)
    loop.loop = AsyncMock()
    return loop


@funcnodes_test(no_prefix=True)
async def test_initial_state(test_loop):
    assert not test_loop.running
    assert not test_loop.stopped
    assert test_loop.manager is None


@funcnodes_test(no_prefix=True)
async def test_manager_assignment(test_loop):
    mock_manager = Mock()
    test_loop.manager = mock_manager
    assert test_loop.manager == mock_manager


@funcnodes_test(no_prefix=True)
async def test_manager_reassignment_fails(test_loop):
    mock_manager = Mock()
    test_loop.manager = mock_manager
    with pytest.raises(ValueError):
        test_loop.manager = Mock()


@funcnodes_test(no_prefix=True)
async def test_stop_loop(test_loop):
    test_loop._running = True
    await test_loop.stop()
    assert not test_loop.running
    assert test_loop.stopped


@funcnodes_test(no_prefix=True)
async def test_pause():
    class CountingLoop(CustomLoop):
        counter = 0

        async def loop(self):
            self.counter += 1

    loop = CountingLoop()
    asyncio.create_task(loop.continuous_run())
    await asyncio.sleep(0.3)
    assert loop.counter > 1
    loop.pause()
    fixed_counter = loop.counter
    await asyncio.sleep(0.3)
    assert loop.counter == fixed_counter
    loop.resume()
    await asyncio.sleep(0.3)
    assert loop.counter > fixed_counter
    loop.pause()
    fixed_counter = loop.counter
    await asyncio.sleep(0.3)
    assert loop.counter == fixed_counter
    loop.resume_in(1)
    await asyncio.sleep(0.5)
    assert loop.counter == fixed_counter
    await asyncio.sleep(1)
    assert loop.counter > fixed_counter


@funcnodes_test(no_prefix=True)
async def test_continuous_run_calls_loop(test_loop):
    test_loop._running = True
    task = asyncio.create_task(test_loop.continuous_run())
    await asyncio.sleep(0.3)
    test_loop._running = False
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    test_loop.loop.assert_called()


@pytest.fixture
def loop_manager():
    worker = Mock()
    worker.logger = logging.getLogger("TestWorkerLogger")
    return LoopManager(worker)


@pytest.fixture
def custom_loop():
    return AsyncMock(spec=CustomLoop)


@funcnodes_test(no_prefix=True)
async def test_add_loop_while_stopped(
    loop_manager: LoopManager, custom_loop: CustomLoop
):
    loop_manager.add_loop(custom_loop)
    assert custom_loop in loop_manager._loops_to_add


@funcnodes_test(no_prefix=True)
async def test_add_loop_while_running(
    loop_manager: LoopManager, custom_loop: CustomLoop
):
    loop_manager._running = True
    task = loop_manager.add_loop(custom_loop)
    assert custom_loop in loop_manager._loops
    assert isinstance(task, asyncio.Task)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@funcnodes_test(no_prefix=True)
async def test_remove_loop(loop_manager: LoopManager, custom_loop: CustomLoop):
    loop_manager._loops.append(custom_loop)
    loop_manager._loop_tasks.append(asyncio.create_task(asyncio.sleep(1)))
    loop_manager.remove_loop(custom_loop)
    assert custom_loop not in loop_manager._loops


@funcnodes_test(no_prefix=True)
async def test_run_forever_async(loop_manager: LoopManager):
    loop_manager._running = True
    task = asyncio.create_task(loop_manager.run_forever_async())
    await asyncio.sleep(0.5)
    loop_manager._running = False
    await asyncio.sleep(0.1)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@funcnodes_test(no_prefix=True)
async def test_stop_loop_manager(loop_manager: LoopManager):
    loop_manager._running = True
    loop_manager.stop()
    assert not loop_manager.running
    assert len(loop_manager._loops) == 0


@funcnodes_test(no_prefix=True)
async def test_run_forever_threaded(loop_manager: LoopManager):
    thread = loop_manager.run_forever_threaded()
    start = time.time()
    while not loop_manager.running and time.time() - start < 10:
        await asyncio.sleep(0.1)
    loop_manager.stop()
    await asyncio.sleep(0.1)
    thread.join()
    assert not loop_manager.running
