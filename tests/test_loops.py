import unittest
import asyncio
import logging
from unittest.mock import AsyncMock, Mock
from funcnodes_worker.loop import (
    CustomLoop,
    LoopManager,
)
from funcnodes_core.testing import (
    teardown as fn_teardown,
    set_in_test as fn_set_in_test,
)

fn_set_in_test()


class _TestLoop(CustomLoop):
    async def loop(self):
        pass


class TestCustomLoop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.logger = logging.getLogger("TestLogger")
        self.loop = _TestLoop(delay=0.2, logger=self.logger)
        self.loop.loop = AsyncMock()

    async def test_initial_state(self):
        self.assertFalse(self.loop.running)
        self.assertFalse(self.loop.stopped)
        self.assertIsNone(self.loop.manager)

    async def test_manager_assignment(self):
        mock_manager = Mock()
        self.loop.manager = mock_manager
        self.assertEqual(self.loop.manager, mock_manager)

    async def test_manager_reassignment_fails(self):
        mock_manager = Mock()
        self.loop.manager = mock_manager
        with self.assertRaises(ValueError):
            self.loop.manager = Mock()

    async def test_stop(self):
        self.loop._running = True
        await self.loop.stop()
        self.assertFalse(self.loop.running)
        self.assertTrue(self.loop.stopped)

    async def test_pause(self):
        class CountingLoop(CustomLoop):
            counter = 0

            async def loop(self):
                self.counter += 1

        loop = CountingLoop()
        asyncio.create_task(loop.continuous_run())
        await asyncio.sleep(0.3)
        self.assertGreater(loop.counter, 1)
        loop.pause()
        fixed_counter = loop.counter
        await asyncio.sleep(0.3)
        self.assertEqual(loop.counter, fixed_counter)
        loop.resume()
        await asyncio.sleep(0.3)
        self.assertGreater(loop.counter, fixed_counter)
        loop.pause()
        fixed_counter = loop.counter
        await asyncio.sleep(0.3)
        self.assertEqual(loop.counter, fixed_counter)
        loop.resume_in(1)
        await asyncio.sleep(0.5)
        self.assertEqual(loop.counter, fixed_counter)
        await asyncio.sleep(1)
        self.assertGreater(loop.counter, fixed_counter)

    async def test_continuous_run_calls_loop(self):
        self.loop._running = True
        task = asyncio.create_task(self.loop.continuous_run())
        await asyncio.sleep(0.3)  # Let it run for a while
        self.loop._running = False  # Stop the loop
        task.cancel()
        self.loop.loop.assert_called()


class TestLoopManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker = Mock()
        self.worker.logger = logging.getLogger("TestWorkerLogger")
        self.manager = LoopManager(self.worker)
        self.custom_loop = AsyncMock(spec=CustomLoop)

    async def test_add_loop_while_stopped(self):
        self.manager.add_loop(self.custom_loop)
        self.assertIn(self.custom_loop, self.manager._loops_to_add)

    async def test_add_loop_while_running(self):
        self.manager._running = True
        task = self.manager.add_loop(self.custom_loop)
        self.assertIn(self.custom_loop, self.manager._loops)
        self.assertIsInstance(task, asyncio.Task)

    async def test_remove_loop(self):
        self.manager._loops.append(self.custom_loop)
        self.manager._loop_tasks.append(asyncio.create_task(asyncio.sleep(1)))
        self.manager.remove_loop(self.custom_loop)
        self.assertNotIn(self.custom_loop, self.manager._loops)

    async def test_run_forever_async(self):
        self.manager._running = True
        task = asyncio.create_task(self.manager.run_forever_async())
        await asyncio.sleep(0.5)
        self.manager._running = False
        await asyncio.sleep(0.1)
        task.cancel()

    async def test_stop(self):
        self.manager._running = True
        self.manager.stop()
        self.assertFalse(self.manager.running)
        self.assertEqual(len(self.manager._loops), 0)

    async def test_run_forever_threaded(self):
        self.manager._running = True
        import threading

        thread = threading.Thread(target=self.manager.run_forever)
        thread.start()
        await asyncio.sleep(1)
        self.manager.stop()
        await asyncio.sleep(0.1)
        thread.join()
        self.assertFalse(self.manager.running)


if __name__ == "__main__":
    unittest.main()
