import asyncio
import time

import pytest

from funcnodes_worker import WSWorker  # noqa: E402
from funcnodes_worker._opts import aiohttp, DependencyError  # noqa: E402
from pytest_funcnodes import funcnodes_test


if aiohttp:

    @pytest.fixture
    def ws_worker():
        worker = WSWorker()
        thread = worker.run_forever_threaded()
        try:
            yield worker
        finally:
            worker.stop()
            thread.join()

    @funcnodes_test(no_prefix=True)
    async def test_ws_worker(ws_worker):
        port = ws_worker.port
        host = ws_worker.host
        await asyncio.sleep(1)
        max_time = 10
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"ws://{host}:{port}") as ws:

                async def listentask():
                    async for msg in ws:
                        print(msg)

                await ws.send_json({"type": "cmd", "cmd": "stop_worker"})
                asyncio.create_task(listentask())

                start = time.time()
                assert not ws.closed
                while not ws.closed and time.time() - start < max_time:
                    await asyncio.sleep(0.5)

                assert ws.closed

else:

    @funcnodes_test(no_prefix=True)
    async def test_placeholder():
        with pytest.raises(DependencyError):
            WSWorker()
