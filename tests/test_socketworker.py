import unittest
import asyncio
from unittest.mock import AsyncMock
from funcnodes_worker import SocketWorker
from pytest_funcnodes import setup as fn_setup, teardown as fn_teardown


class TestSocketWorker(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker = SocketWorker(host="127.0.0.1", port=9382)
        self.worker.socket_loop._assert_connection = AsyncMock()
        self.worker.socket_loop.stop = AsyncMock()

    async def asyncTearDown(self):
        if self.worker:
            self.worker.stop()
            await asyncio.sleep(0.4)

    def setUp(self) -> None:
        fn_setup()

    def tearDown(self) -> None:
        fn_teardown()

    async def test_initial_state(self):
        self.assertEqual(self.worker.socket_loop._host, "127.0.0.1")
        self.assertEqual(self.worker.socket_loop._port, 9382)

    async def test_send_message(self):
        writer = AsyncMock()
        await self.worker.sendmessage("test message", writer=writer)
        writer.write.assert_called()
        writer.drain.assert_called()

    async def test_send_message_to_clients(self):
        writer1 = AsyncMock()
        writer2 = AsyncMock()
        self.worker.socket_loop.clients = [writer1, writer2]
        await self.worker.sendmessage("test message")
        writer1.write.assert_called()
        writer2.write.assert_called()

    async def test_stop(self):
        asyncio.create_task(self.worker.run_forever_async())
        await self.worker.wait_for_running(timeout=10)
        await asyncio.sleep(1)
        self.assertTrue(self.worker.socket_loop.running)
        self.worker.stop()
        self.assertFalse(self.worker.socket_loop.running)


if __name__ == "__main__":
    unittest.main()
