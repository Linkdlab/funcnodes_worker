import asyncio
import logging
from unittest import IsolatedAsyncioTestCase

from funcnodes_worker.websocket import ClientConnection


class DummyWebSocket:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.sent = []
        self.closed = False

    async def send_str(self, msg: str):
        await asyncio.sleep(self.delay)
        self.sent.append(("str", msg))

    async def send_bytes(self, data: bytes):
        await asyncio.sleep(self.delay)
        self.sent.append(("bytes", data))

    async def close(self, *_, **__):
        self.closed = True


class TestClientConnection(IsolatedAsyncioTestCase):
    async def test_close_cancels_send_task(self):
        ws = DummyWebSocket(delay=0.1)
        client = ClientConnection(ws, logging.getLogger("test_close"))

        # Enqueue data so the send loop is actively processing.
        await client.enqueue("ping")
        await asyncio.sleep(0.01)

        await client.close()

        self.assertTrue(client.send_task.done())
        self.assertTrue(client.queue.empty())

    async def test_enqueue_after_close_is_noop(self):
        ws = DummyWebSocket()
        client = ClientConnection(ws, logging.getLogger("test_enqueue"))

        await client.close()
        await client.enqueue("ignored")

        self.assertTrue(client.send_task.done())
        self.assertFalse(ws.sent)
