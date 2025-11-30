from typing import Optional
from weakref import ref
import funcnodes_core as fn
from funcnodes_core import instance_nodefunction, flatten_shelf  # noqa: E402
from funcnodes_worker import CustomLoop  # noqa: E402
from funcnodes_worker import FuncNodesExternalWorker, RemoteWorker  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402
import time  # noqa: E402
import asyncio  # noqa: E402
import logging  # noqa: E402
import tempfile  # noqa: E402
import json  # noqa: E402
import gc  # noqa: E402
import pytest
from pytest_funcnodes import funcnodes_test

try:
    import objgraph  # noqa: E402
except ImportError:
    objgraph = None


class RaiseErrorLogger(logging.Logger):
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


class ExternalWorkerSelfStop(FuncNodesExternalWorker):
    NODECLASSID = "testexternalworker_ExternalWorkerSelfStop"

    async def loop(self):
        print("loopstart")
        await asyncio.sleep(1)
        print("Stopping")
        await self.stop()
        print("loopend")


class ExternalWorker1(FuncNodesExternalWorker):
    NODECLASSID = "testexternalworker_ExternalWorker1"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.triggercount = 0

    async def loop(self):
        pass

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


@fn.NodeDecorator(node_id="workertestnode")
async def workertestnode(a: int) -> int:
    return a + 1


class ExternalWorkerWithNodeShelves(FuncNodesExternalWorker):
    NODECLASSID = "testexternalworker_ExternalWorkerWithNodeShelves"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._nodeshelf = fn.Shelf(
            name="test",
            description="test",
            nodes=[
                workertestnode,
            ],
        )

    def get_nodeshelf(self) -> Optional[fn.Shelf]:
        return self._nodeshelf


# @pytest.fixture(autouse=True)
# def funcnodes_setup_teardown():
#     fn_setup()
#     register_node(workertestnode)
#     yield
#     fn_teardown()


@pytest.fixture
async def running_remote_worker():
    tempdir = tempfile.TemporaryDirectory(prefix="funcnodes")
    retmoteworker = _TestWorker(data_path=tempdir.name)
    loop = asyncio.get_event_loop()
    runtask = loop.create_task(retmoteworker.run_forever_async())
    start = time.time()
    while not retmoteworker.loop_manager.running and time.time() - start < 50:
        if runtask.done():
            if runtask.exception():
                tempdir.cleanup()
                raise runtask.exception()
        await asyncio.sleep(1)
    if not retmoteworker.loop_manager.running:
        runtask.cancel()
        tempdir.cleanup()
        raise Exception("Worker not running")
    try:
        yield retmoteworker
    finally:
        retmoteworker.stop()
        async with asyncio.timeout(5):
            await runtask
        tempdir.cleanup()


@funcnodes_test(no_prefix=True)
def test_external_worker_missing_nodeclassid():
    with pytest.raises(ValueError):

        class ExternalWorker2(FuncNodesExternalWorker):
            IS_ABSTRACT = False

            async def loop(self):
                pass


@funcnodes_test(no_prefix=True)
async def test_external_worker_sync_loop():
    class ExternalWorker1(FuncNodesExternalWorker):
        NODECLASSID = "testexternalworker"

        def loop(self):
            pass

    assert ExternalWorker1.running_instances() == [], (
        ExternalWorker1.running_instances()
    )
    worker = ExternalWorker1(workerid="test")
    worker._logger = RaiseErrorLogger("raiserror")
    await asyncio.sleep(0.5)

    with pytest.raises(TypeError) as e:
        await worker.continuous_run()

    assert "object NoneType can't be used in 'await' expression" == str(e.value)
    assert worker.running
    await worker.stop()
    assert not worker.running
    assert ExternalWorker1.running_instances() == [], (
        ExternalWorker1.running_instances()
    )


@funcnodes_test(no_prefix=True)
async def test_external_worker_loop():
    class ExternalWorker1(FuncNodesExternalWorker):
        NODECLASSID = "testexternalworker"

        async def loop(self):
            await self.stop()

    assert ExternalWorker1.running_instances() == [], (
        ExternalWorker1.running_instances()
    )
    worker = ExternalWorker1(workerid="test")
    worker._logger = RaiseErrorLogger("raiserror")
    await worker.continuous_run()


@funcnodes_test(no_prefix=True)
async def test_external_worker_serialization():
    class ExternalWorker1(FuncNodesExternalWorker):
        NODECLASSID = "testexternalworker"

        async def loop(self):
            await self.stop()

        @instance_nodefunction()
        def test(self, a: int) -> int:
            return 1 + a

    worker = ExternalWorker1(workerid="test")
    ser = json.loads(json.dumps(worker, cls=fn.JSONEncoder))
    assert ser == {
        "name": "ExternalWorker1(test)",
        "nodeclassid": "testexternalworker",
        "running": False,
        "uuid": "test",
        "config": {},
    }


@funcnodes_test(no_prefix=True)
async def test_external_worker_nodes(running_remote_worker: _TestWorker):
    running_remote_worker.add_local_worker(
        ExternalWorker1, "test_external_worker_nodes"
    )
    nodeid = "testexternalworker_ExternalWorker1.test_external_worker_nodes.test"
    nodeclass = running_remote_worker.nodespace.lib.get_node_by_id(nodeid)
    assert nodeclass.node_name == "Test"
    node = running_remote_worker.add_node(nodeid, name="TestNode")
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
    assert node.serialize() == expected_node_ser


@funcnodes_test(no_prefix=True)
async def test_base_run(running_remote_worker: _TestWorker):
    for _ in range(5):
        await asyncio.sleep(0.3)
        t = time.time()
        assert t - running_remote_worker.timerloop.last_run <= 0.25


@funcnodes_test(no_prefix=True)
async def test_external_worker_run(running_remote_worker: _TestWorker):
    def get_ws_nodes():
        nodes = []
        for shelf in running_remote_worker.nodespace.lib.shelves:
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

        assert len(nodes) == target, nodes

        del nodes
        gc.collect()

    await asyncio.sleep(0.5)
    t = time.time()
    assert t - running_remote_worker.timerloop.last_run <= 0.4
    print("adding worker")
    check_nodes_length(0)

    w: ExternalWorker1 = running_remote_worker.add_local_worker(
        ExternalWorker1, "test_external_worker_run"
    )

    check_nodes_length(2)

    assert (
        "testexternalworker_ExternalWorker1" in FuncNodesExternalWorker.RUNNING_WORKERS
    )
    assert (
        "test_external_worker_run"
        in FuncNodesExternalWorker.RUNNING_WORKERS["testexternalworker_ExternalWorker1"]
    )

    nodetest = running_remote_worker.add_node(
        "testexternalworker_ExternalWorker1.test_external_worker_run.test",
    )

    node_getcount = running_remote_worker.add_node(
        "testexternalworker_ExternalWorker1.test_external_worker_run.get_count",
    )

    assert "out" in node_getcount.outputs
    assert node_getcount.outputs["out"].value is fn.NoValue
    assert w.triggercount == 0

    fn.FUNCNODES_LOGGER.debug("triggering node_getcount 1")
    await node_getcount

    assert node_getcount.outputs["out"].value == 0
    assert w.triggercount == 0

    fn.FUNCNODES_LOGGER.debug("triggering nodetest 1")
    nodetest.inputs["a"].value = 1
    await fn.run_until_complete(nodetest)

    assert w.triggercount == 1
    assert nodetest.outputs["out"].value == 2
    fn.FUNCNODES_LOGGER.debug("triggering node_getcount 2")
    await node_getcount

    assert "out" in node_getcount.outputs
    assert node_getcount.outputs["out"].value == 1

    assert not (
        nodetest.status()["requests_trigger"] or nodetest.status()["in_trigger"]
    )

    w.increment_trigger()
    assert nodetest.status()["requests_trigger"] or nodetest.status()["in_trigger"]
    await asyncio.sleep(0.1)
    print("waiting")
    t = time.time()
    while (
        nodetest.status()["requests_trigger"] or nodetest.status()["in_trigger"]
    ) and time.time() - t < 10:
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

    t = time.time()
    assert t - running_remote_worker.timerloop.last_run <= 1.0
    gc.collect()
    if "testexternalworker_ExternalWorker1" in FuncNodesExternalWorker.RUNNING_WORKERS:
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
                    max_depth=10,
                    filename="backrefs_before.dot",
                    highlight=lambda x: isinstance(x, ExternalWorker1),
                    shortnames=False,
                    extra_node_attrs=lambda x: {"longname": str(x)},
                )

        assert (
            "test_external_worker_run"
            not in FuncNodesExternalWorker.RUNNING_WORKERS[
                "testexternalworker_ExternalWorker1"
            ]
        ), {
            k: {vk: vv for vk, vv in v.items()}
            for k, v in FuncNodesExternalWorker.RUNNING_WORKERS.items()
        }

    check_nodes_length(0)

    await asyncio.sleep(0.5)
    t = time.time()
    assert t - running_remote_worker.timerloop.last_run <= 0.3


@funcnodes_test(no_prefix=True)
async def test_external_worker_nodes_shelf(running_remote_worker: _TestWorker):
    worker = running_remote_worker.add_local_worker(
        ExternalWorkerWithNodeShelves, "test_external_worker_nodes"
    )

    assert isinstance(worker, ExternalWorkerWithNodeShelves)

    assert worker.get_nodeshelf() is not None
    assert worker.get_nodeshelf().name == "test"
    assert worker.get_nodeshelf().nodes == [workertestnode]

    assert isinstance(worker.nodeshelf, ref)
    assert worker.nodeshelf() is not None
    assert worker.nodeshelf().name == "test"
    assert worker.nodeshelf().nodes == [workertestnode]

    nodeclass = running_remote_worker.nodespace.lib.get_node_by_id("workertestnode")
    assert nodeclass.node_name == "workertestnode"


@funcnodes_test(no_prefix=True)
async def test_external_worker_nodes_multiple_updates(
    running_remote_worker: _TestWorker,
):
    worker = running_remote_worker.add_local_worker(
        ExternalWorkerWithNodeShelves, "test_external_worker_nodes_multiple"
    )

    for _ in range(2):
        print("registered nodes", list(fn.node.REGISTERED_NODES.keys()))
        worker.emit("nodes_update")
        assert worker.get_nodeshelf() is not None
        assert worker.get_nodeshelf().name == "test"
        assert worker.get_nodeshelf().nodes == [workertestnode]

        assert isinstance(worker.nodeshelf, ref)
        assert worker.nodeshelf() is not None
        assert worker.nodeshelf().name == "test"
        assert worker.nodeshelf().nodes == [workertestnode]

        print(
            json.dumps(running_remote_worker.nodespace.lib.full_serialize(), indent=4)
        )
        print("registered nodes", list(fn.node.REGISTERED_NODES.keys()))

        nodeclass = running_remote_worker.nodespace.lib.get_node_by_id("workertestnode")
        assert nodeclass.node_name == "workertestnode"
