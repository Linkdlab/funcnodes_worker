from unittest import IsolatedAsyncioTestCase
import funcnodes_core as fn

from funcnodes_core.testing import (
    teardown as fn_teardown,
    set_in_test as fn_set_in_test,
)

fn_set_in_test()


from funcnodes_worker import (
    FuncNodesExternalWorker,
    RemoteWorker,
)
from unittest.mock import MagicMock


from funcnodes_core import (
    instance_nodefunction,
    flatten_shelf,
)
from funcnodes_worker import CustomLoop
import time
import asyncio
import logging

import tempfile
import json
import gc

try:
    import objgraph
except ImportError:
    objgraph = None

fn.FUNCNODES_LOGGER.setLevel(logging.DEBUG)


class ExternalWorker_Test(FuncNodesExternalWorker):
    pass


class RaiseErrorLogger:
    def exception(self, e: Exception):
        raise e


class TimerLoop(CustomLoop):
    def __init__(self, worker) -> None:
        super().__init__(delay=0.1)
        self._worker = worker
        self.last_run = 0

    async def loop(self):
        self.last_run = time.time()

    #  print("timer", self.last_run)


class _TestWorker(RemoteWorker):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.timerloop = TimerLoop(self)
        self.loop_manager.add_loop(self.timerloop)

    async def sendmessage(self, *args, **kwargs):
        return MagicMock()

    async def send_bytes(self, *args, **kwargs):
        return MagicMock()


class TestExternalWorker(IsolatedAsyncioTestCase):
    def test_external_worker_missing_loop(self):
        class ExternalWorker1(FuncNodesExternalWorker):
            pass

        with self.assertRaises(TypeError):
            ExternalWorker1()

    def test_external_worker_missing_nodeclassid(self):
        with self.assertRaises(ValueError):

            class ExternalWorker2(FuncNodesExternalWorker):
                IS_ABSTRACT = False

                async def loop(self):
                    pass

    async def test_external_worker_sync_loop(self):
        class ExternalWorker1(FuncNodesExternalWorker):
            NODECLASSID = "testexternalworker"

            def loop(self):
                self.stop()

        worker = ExternalWorker1(workerid="test")
        worker._logger = RaiseErrorLogger()

        with self.assertRaises(TypeError) as e:
            await worker.continuous_run()

        self.assertEqual(
            "object NoneType can't be used in 'await' expression", str(e.exception)
        )

    async def test_external_worker_loop(self):
        class ExternalWorker1(FuncNodesExternalWorker):
            NODECLASSID = "testexternalworker"

            async def loop(self):
                await self.stop()

        self.assertEqual(ExternalWorker1.running_instances(), [])
        worker = ExternalWorker1(workerid="test")
        worker._logger = RaiseErrorLogger()
        await worker.continuous_run()

    async def test_external_worker_serialization(self):
        class ExternalWorker1(FuncNodesExternalWorker):
            NODECLASSID = "testexternalworker"

            async def loop(self):
                await self.stop()

            @instance_nodefunction()
            def test(self, a: int) -> int:
                return 1 + a

        worker = ExternalWorker1(workerid="test")
        ser = json.loads(json.dumps(worker, cls=fn.JSONEncoder))
        self.assertEqual(
            ser,
            {
                "name": "ExternalWorker1(test)",
                "nodeclassid": "testexternalworker",
                "running": False,
                "uuid": "test",
            },
        )


class ExternalWorker1(FuncNodesExternalWorker):
    NODECLASSID = "testexternalworker_ExternalWorker1"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.triggercount = 0

    async def loop(self):
        print("loopstart")
        await asyncio.sleep(1)
        print("Stopping")
        await self.stop()
        print("loopend")

    @instance_nodefunction()
    def test(self, a: int) -> int:
        self.triggercount += 1
        return 1 + a

    @test.triggers
    def increment_trigger(self):
        print("incrementing")

    @instance_nodefunction()
    def get_count(self) -> int:
        return self.triggercount


class TestExternalWorkerWithWorker(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="funcnodes")
        self.retmoteworker = _TestWorker(data_path=self.tempdir.name)
        self._loop = asyncio.get_event_loop()
        self.runtask = self._loop.create_task(self.retmoteworker.run_forever_async())
        t = time.time()
        while not self.retmoteworker.loop_manager.running and time.time() - t < 10:
            if self.runtask.done():
                if self.runtask.exception():
                    raise self.runtask.exception()
            await asyncio.sleep(1)
        if not self.retmoteworker.loop_manager.running:
            raise Exception("Worker not running")

    async def asyncTearDown(self):
        self.retmoteworker.stop()

        async with asyncio.timeout(5):
            await self.runtask

    def tearDown(self) -> None:
        if not self.runtask.done():
            self.runtask.cancel()

        fn_teardown()
        self.tempdir.cleanup()
        return super().tearDown()

    async def test_external_worker_nodes(self):
        self.retmoteworker.add_local_worker(
            ExternalWorker1, "test_external_worker_nodes"
        )
        nodeid = "testexternalworker_ExternalWorker1.test_external_worker_nodes.test"
        nodeclass = self.retmoteworker.nodespace.lib.get_node_by_id(nodeid)
        self.assertEqual(nodeclass.node_name, "Test")
        node = self.retmoteworker.add_node(nodeid, name="TestNode")
        self.maxDiff = None
        expected_node_ser = {
            "name": "TestNode",
            "id": node.uuid,
            "node_id": nodeid,
            "node_name": "Test",
            "io": {
                "a": {"is_input": True, "value": fn.NoValue, "emit_value_set": True},
                "out": {"is_input": False, "value": fn.NoValue, "emit_value_set": True},
            },
        }
        self.assertEqual(node.serialize(), expected_node_ser)

    async def test_base_run(self):
        for _ in range(5):
            await asyncio.sleep(0.3)
            t = time.time()
            self.assertLessEqual(t - self.retmoteworker.timerloop.last_run, 0.25)

    async def test_external_worker_run(self):
        def get_ws_nodes():
            nodes = []
            for shelf in self.retmoteworker.nodespace.lib.shelves:
                nodes.extend(flatten_shelf(shelf)[0])
            return nodes

        def check_nodes_length(target=0):
            nodes = get_ws_nodes()

            if target == 0 and len(nodes) > 0 and objgraph:
                objgraph.show_backrefs(
                    nodes,
                    max_depth=15,
                    filename="backrefs_nodes.dot",
                    highlight=lambda x: isinstance(x, fn.Node),
                    shortnames=False,
                )

            self.assertEqual(len(nodes), target, nodes)

            del nodes
            gc.collect()

        await asyncio.sleep(0.5)
        t = time.time()
        self.assertLessEqual(
            t - self.retmoteworker.timerloop.last_run,
            0.4,
            (t, self.retmoteworker.timerloop.last_run),
        )
        print("adding worker")
        check_nodes_length(0)

        w: ExternalWorker1 = self.retmoteworker.add_local_worker(
            ExternalWorker1, "test_external_worker_run"
        )

        check_nodes_length(2)

        self.assertIn(
            "testexternalworker_ExternalWorker1",
            FuncNodesExternalWorker.RUNNING_WORKERS,
        )
        self.assertIn(
            "test_external_worker_run",
            FuncNodesExternalWorker.RUNNING_WORKERS[
                "testexternalworker_ExternalWorker1"
            ],
        )

        nodetest = self.retmoteworker.add_node(
            "testexternalworker_ExternalWorker1.test_external_worker_run.test",
        )

        node_getcount = self.retmoteworker.add_node(
            "testexternalworker_ExternalWorker1.test_external_worker_run.get_count",
        )

        self.assertEqual(node_getcount.outputs["out"].value, fn.NoValue)
        self.assertEqual(w.triggercount, 0)

        fn.FUNCNODES_LOGGER.debug("triggering node_getcount 1")
        await node_getcount

        self.assertEqual(node_getcount.outputs["out"].value, 0)
        self.assertEqual(w.triggercount, 0)

        self.assertEqual(w.triggercount, 0)
        fn.FUNCNODES_LOGGER.debug("triggering nodetest 1")
        nodetest.inputs["a"].value = 1
        await fn.run_until_complete(nodetest)

        self.assertEqual(w.triggercount, 1)
        self.assertEqual(nodetest.outputs["out"].value, 2)
        fn.FUNCNODES_LOGGER.debug("triggering node_getcount 2")
        await node_getcount

        self.assertEqual(node_getcount.outputs["out"].value, 1)

        self.assertEqual(
            nodetest.status()["requests_trigger"] or nodetest.status()["in_trigger"],
            False,
        )

        w.increment_trigger()
        self.assertEqual(
            nodetest.status()["requests_trigger"] or nodetest.status()["in_trigger"],
            True,
        )

        print("waiting")
        t = time.time()
        while w.running and time.time() - t < 10:
            await asyncio.sleep(0.1)
        t = time.time()
        while not w.stopped and time.time() - t < 10:
            print(w._stopped, w._running)
            await asyncio.sleep(0.6)
            await w.stop()
        del w
        del node_getcount
        del nodetest
        await asyncio.sleep(5)

        # await asyncio.sleep(6)
        t = time.time()
        self.assertLessEqual(t - self.retmoteworker.timerloop.last_run, 1.0)
        gc.collect()
        if (
            "testexternalworker_ExternalWorker1"
            in FuncNodesExternalWorker.RUNNING_WORKERS
        ):
            if (
                "test_external_worker_run"
                in FuncNodesExternalWorker.RUNNING_WORKERS[
                    "testexternalworker_ExternalWorker1"
                ]
            ):
                if objgraph:
                    objgraph.show_backrefs(
                        [
                            FuncNodesExternalWorker.RUNNING_WORKERS[
                                "testexternalworker_ExternalWorker1"
                            ]["test_external_worker_run"]
                        ],
                        max_depth=15,
                        filename="backrefs_before.dot",
                        highlight=lambda x: isinstance(x, ExternalWorker1),
                        shortnames=False,
                    )

            self.assertNotIn(
                "test_external_worker_run",
                FuncNodesExternalWorker.RUNNING_WORKERS[
                    "testexternalworker_ExternalWorker1"
                ],
            )

        check_nodes_length(0)

        await asyncio.sleep(0.5)
        t = time.time()
        self.assertLessEqual(t - self.retmoteworker.timerloop.last_run, 0.3)
