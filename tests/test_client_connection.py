import asyncio
import logging

import pytest

from funcnodes_worker.websocket import ClientConnection


pytestmark = pytest.mark.asyncio


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


@pytest.fixture
def logger():
    return logging.getLogger("test_client_connection")


async def test_close_cancels_send_task(logger):
    ws = DummyWebSocket(delay=0.1)
    client = ClientConnection(ws, logger)

    # Enqueue data so the send loop is actively processing.
    await client.enqueue("ping")
    await asyncio.sleep(0.01)

    await client.close()

    assert client.send_task.done()
    assert client.queue.empty()


async def test_enqueue_after_close_is_noop(logger):
    ws = DummyWebSocket()
    client = ClientConnection(ws, logger)

    await client.close()
    await client.enqueue("ignored")

    assert client.send_task.done()
    assert ws.sent == []
