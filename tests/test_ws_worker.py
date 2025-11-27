import asyncio
import time
from unittest import IsolatedAsyncioTestCase
from pytest_funcnodes import setup as fn_setup, teardown as fn_teardown

from funcnodes_worker import (  # noqa: E402
    WSWorker,
)
from funcnodes_worker._opts import aiohttp, DependencyError  # noqa: E402


if aiohttp:

    class TestWSWorker(IsolatedAsyncioTestCase):
        def setUp(self) -> None:
            fn_setup()

        def tearDown(self) -> None:
            fn_teardown()

        async def test_ws_worker(self):
            ws_worker = WSWorker()

            ws_worker.run_forever_threaded()

            port = ws_worker.port
            host = ws_worker.host

            # make a connection to the websocket server
            MAXTIME = 10
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f"ws://{host}:{port}") as ws:

                    async def listentask():
                        async for msg in ws:
                            print(msg)

                    await ws.send_json({"type": "cmd", "cmd": "stop_worker"})
                    asyncio.create_task(listentask())

                    stime = time.time()
                    self.assertFalse(ws.closed)
                    while not ws.closed and time.time() - stime < MAXTIME:
                        await asyncio.sleep(
                            0.5
                        )  # Poll until the connection is fully closed
                    self.assertTrue(ws.closed)

                    # Wait for WebSocket to fully close


else:

    class TestPlaceholder(IsolatedAsyncioTestCase):
        def setUp(self) -> None:
            fn_setup()

        def tearDown(self) -> None:
            fn_teardown()

        async def test_placeholder(self):
            with self.assertRaises(DependencyError):
                WSWorker()
