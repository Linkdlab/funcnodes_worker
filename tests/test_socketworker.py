import asyncio
from unittest.mock import AsyncMock

import pytest

from funcnodes_worker import SocketWorker
from pytest_funcnodes import funcnodes_test


@pytest.fixture
async def worker():
    worker = SocketWorker(host="127.0.0.1", port=9382)
    worker.socket_loop._assert_connection = AsyncMock()
    worker.socket_loop.stop = AsyncMock()
    try:
        yield worker
    finally:
        worker.stop()
        await asyncio.sleep(0.4)


@funcnodes_test(no_prefix=True)
async def test_initial_state(worker):
    assert worker.socket_loop._host == "127.0.0.1"
    assert worker.socket_loop._port == 9382


@funcnodes_test(no_prefix=True)
async def test_send_message(worker):
    writer = AsyncMock()
    await worker.sendmessage("test message", writer=writer)
    writer.write.assert_called()
    writer.drain.assert_called()


@funcnodes_test(no_prefix=True)
async def test_send_message_to_clients(worker):
    writer1 = AsyncMock()
    writer2 = AsyncMock()
    worker.socket_loop.clients = [writer1, writer2]
    await worker.sendmessage("test message")
    writer1.write.assert_called()
    writer2.write.assert_called()


@funcnodes_test(no_prefix=True)
async def test_stop(worker: SocketWorker):
    asyncio.create_task(worker.run_forever_async())
    await worker.wait_for_running(timeout=20)
    await asyncio.sleep(1)
    assert worker.socket_loop.running
    worker.stop()
    await asyncio.sleep(2)
    assert not worker.socket_loop.running
