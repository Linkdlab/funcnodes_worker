import asyncio
import io
import json
import logging
import os
from pathlib import Path
import time
import zipfile
from copy import deepcopy
from typing import ClassVar, Type, Union

import pytest
import funcnodes_core as fn

from funcnodes_worker import Worker, FuncNodesExternalWorker, ExternalWorkerConfig
from funcnodes_worker.worker import WorkerState, NodeViewState
from pydantic import Field


from pytest_funcnodes import funcnodes_test


class _TestWorkerClass(Worker):
    def _on_nodespaceerror(self, error: Exception, src: fn.NodeSpace):
        """handle nodespace errors"""

    def on_nodespaceevent(self, event, **kwargs):
        """handle nodespace events"""


@fn.NodeDecorator(node_id="test_node")
def testnode(a: int = 1) -> int:
    return a


testshelf = fn.Shelf(
    name="testshelf", description="Test shelf", subshelves=[], nodes=[testnode]
)


@pytest.fixture
def worker_class():
    return _TestWorkerClass


@pytest.fixture
def worker_kwargs(request: pytest.FixtureRequest):
    return {"uuid": request.node.name}


@pytest.fixture
async def worker_case(
    worker_class: Type[_TestWorkerClass],
    tmp_path: Union[Path, str],
    request: pytest.FixtureRequest,
):
    worker = worker_class(
        data_path=tmp_path,
        default_nodes=[testshelf],
        debug=True,
        uuid=f"TestWorkerCase_{request.node.name}",
    )
    worker.write_config()
    try:
        yield worker
    finally:
        worker.stop()
        await asyncio.sleep(0.4)


@pytest.fixture
async def interacting_worker(running_test_worker: _TestWorkerClass):
    worker = running_test_worker
    node1 = worker.add_node("test_node")
    node2 = worker.add_node("test_node")
    await asyncio.sleep(0.5)
    worker.add_edge(node1.uuid, "out", node2.uuid, "a")
    await asyncio.sleep(0.5)
    return worker, node1, node2


@pytest.fixture
def worker_instance(
    worker_class: Type[_TestWorkerClass],
    # tmp_path:Union[Path, str],
    funcnodes_test_setup_teardown,
    # get test name
    request: pytest.FixtureRequest,
):
    worker = worker_class(
        # data_path=tmp_path,
        default_nodes=[testshelf],
        uuid=request.node.name,
    )
    assert len(list(worker.data_path.parent.iterdir())) == 1, (
        f"data_path {worker.data_path.parent} is not empty, but {list(worker.data_path.parent.iterdir())}"
    )
    return worker


@pytest.fixture
async def running_test_worker(worker_instance: _TestWorkerClass):
    thread = worker_instance.run_forever_threaded()
    await worker_instance.wait_for_running(timeout=10)
    try:
        yield worker_instance
    finally:
        worker_instance.stop()
        thread.join()


@pytest.fixture(scope="function", autouse=True)
def register_ndoe():
    fn.node.register_node(testnode)


def create_test_node(worker):
    node = worker.add_node("test_node")
    assert isinstance(node, fn.Node)
    assert isinstance(node, testnode)
    retrieved = worker.get_node(node.uuid)
    assert retrieved is node
    return node


@funcnodes_test
def test_worker_initialization(worker_class, worker_kwargs):
    worker = worker_class(**worker_kwargs)
    assert isinstance(worker, worker_class)


@funcnodes_test(no_prefix=True)
def test_with_default_nodes(worker_class, worker_kwargs):
    worker = worker_class(**worker_kwargs, default_nodes=[testshelf])
    try:
        assert isinstance(worker, worker_class)
    finally:
        worker.stop()


@funcnodes_test(no_prefix=True)
def test_with_debug(worker_class: Type[_TestWorkerClass], worker_kwargs):
    worker = worker_class(**worker_kwargs, debug=True)
    try:
        assert isinstance(worker, worker_class)
        assert worker.logger.level == logging.DEBUG
    finally:
        worker.stop()


@funcnodes_test(disable_file_handler=False)
def test_worker_logger(worker_instance: _TestWorkerClass):
    worker = worker_instance
    assert worker.logger.level == logging.DEBUG
    assert worker.logger.name == "funcnodes." + worker.uuid()
    assert len(worker.logger.handlers) == 2, worker.logger.handlers
    # At least one stream-like handler
    file_handlers = [
        h for h in worker.logger.handlers if isinstance(h, logging.FileHandler)
    ]
    stream_handlers = [
        h
        for h in worker.logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)  # exclude FileHandler + subclasses
    ]
    # One file-like handler (FileHandler / RotatingFileHandler / etc.)
    assert len(file_handlers) == 1, file_handlers

    # One pure stream handler (console)
    assert len(stream_handlers) == 1, stream_handlers


@funcnodes_test(disable_file_handler=False)
def test_initandrun(running_test_worker: _TestWorkerClass):
    workersdir = fn.config.get_config_dir() / "workers"
    worker = running_test_worker
    workerdir = workersdir / f"worker_{worker.uuid()}"
    worker_p_file = workersdir / f"worker_{worker.uuid()}.p"

    for _ in range(200):
        if worker_p_file.exists():
            break
        time.sleep(0.1)
    time.sleep(2)

    newfiles = os.listdir(fn.config.get_config_dir() / "workers")
    # newfiles = set(newfiles) - set(existing_files)

    assert workerdir.is_dir()
    assert worker_p_file.exists()

    assert f"worker_{worker.uuid()}.p" in newfiles, (
        f"worker_{worker.uuid()}.p not found in {fn.config.get_config_dir() / 'workers'}"
    )
    assert f"worker_{worker.uuid()}.runstate" in newfiles, (
        f"worker_{worker.uuid()}.runstate not found in {fn.config.get_config_dir() / 'workers'}"
    )
    assert f"worker_{worker.uuid()}" in newfiles, (
        f"worker_{worker.uuid()} not found in {fn.config.get_config_dir() / 'workers'}"
    )

    with open(worker_p_file, "r") as file_handle:
        pid = file_handle.read()

    assert pid.isdigit(), pid
    assert os.getpid() == int(pid)

    with open(worker_p_file, "w") as file_handle:
        json.dump({"cmd": "stop_worker"}, file_handle)

    for _ in range(150):
        if not worker_p_file.exists():
            break
        time.sleep(0.1)
    time.sleep(0.5)
    log_contents = None
    if worker_p_file.exists():
        assert f"funcnodes.{worker.uuid()}.log" in os.listdir(workerdir), (
            f"funcnodes.{worker.uuid()}.log not found in {workerdir}"
        )
        with open(workerdir / f"funcnodes.{worker.uuid()}.log", "r") as logfile:
            log_contents = logfile.read()

    assert not worker_p_file.exists(), log_contents


@funcnodes_test
async def test_worker_case_initialization(worker_case, worker_class):
    assert isinstance(worker_case, worker_class)
    assert hasattr(worker_case, "nodespace")
    assert hasattr(worker_case, "loop_manager")
    assert worker_case.nodespace.lib.has_node_id("test_node")


@funcnodes_test
async def test_worker_case_uuid(worker_case):
    assert isinstance(worker_case.uuid(), str)


@funcnodes_test
async def test_worker_case_config_generation(worker_case):
    config = fn.JSONEncoder.apply_custom_encoding(worker_case.config)
    expected = {
        "uuid": worker_case.uuid(),
        "name": worker_case.name(),
        "data_path": worker_case.data_path.absolute().resolve().as_posix(),
        "package_dependencies": {},
        "pid": os.getpid(),
        "type": worker_case.__class__.__name__,
        "env_path": None,
        "update_on_startup": {
            "funcnodes": True,
            "funcnodes-core": True,
            "funcnodes-worker": True,
        },
        "worker_dependencies": {},
    }
    assert config == expected


@funcnodes_test
async def test_worker_case_exportable_config(worker_case):
    config = worker_case.exportable_config()
    expected = {
        "name": worker_case.name(),
        "package_dependencies": {},
        "type": worker_case.__class__.__name__,
        "update_on_startup": {
            "funcnodes": True,
            "funcnodes-core": True,
            "funcnodes-worker": True,
        },
        "worker_dependencies": {},
    }
    assert config == expected


@funcnodes_test
async def test_worker_case_write_config(worker_case):
    config_path = worker_case._config_file
    worker_case.write_config()
    assert os.path.exists(config_path)


@funcnodes_test
async def test_worker_case_load_config(worker_case):
    worker_case.write_config()
    config = worker_case.load_config()
    assert config is not None
    assert config["uuid"] == worker_case.uuid()


@funcnodes_test
async def test_worker_case_process_file_handling(worker_case):
    worker_case._write_process_file()
    process_file = worker_case._process_file
    assert os.path.exists(process_file)


@funcnodes_test
async def test_worker_case_save_state(worker_case):
    worker_case.save()
    assert os.path.exists(worker_case.local_nodespace)


@funcnodes_test
async def test_worker_run_cmd(worker_case):
    cmd = {"cmd": "uuid", "kwargs": {}}
    result = await worker_case.run_cmd(cmd)
    assert result == worker_case.uuid()


@funcnodes_test
async def test_worker_full_state(worker_case):
    ser = fn.JSONEncoder.apply_custom_encoding(worker_case.full_state())
    expected = {
        "backend": {
            "nodes": [],
            "prop": {},
            "lib": {
                "shelves": [
                    {
                        "nodes": [
                            {
                                "node_id": "test_node",
                                "inputs": [
                                    {
                                        "type": "int",
                                        "description": None,
                                        "uuid": "a",
                                    }
                                ],
                                "outputs": [
                                    {
                                        "type": "int",
                                        "description": None,
                                        "uuid": "out",
                                    }
                                ],
                                "description": "",
                                "node_name": "testnode",
                            }
                        ],
                        "subshelves": [],
                        "name": "testshelf",
                        "description": "Test shelf",
                    }
                ]
            },
            "edges": [],
        },
        "worker": {},
        "worker_dependencies": [],
        "progress_state": {
            "message": "",
            "status": "",
            "progress": 0,
            "blocking": False,
        },
        "meta": {"id": worker_case.nodespace_id, "version": fn.__version__},
    }

    ser.pop("view", None)
    assert ser == expected


@funcnodes_test
async def test_worker_add_node(worker_case):
    node = create_test_node(worker_case)
    assert isinstance(node, fn.Node)


@funcnodes_test
async def test_worker_remove_node(worker_case):
    node = create_test_node(worker_case)
    worker_case.remove_node(node.uuid)
    with pytest.raises(ValueError):
        worker_case.get_node(node.uuid)


@funcnodes_test
async def test_worker_add_edge(worker_case):
    node1 = create_test_node(worker_case)
    node2 = create_test_node(worker_case)
    worker_case.add_edge(node1.uuid, "out", node2.uuid, "a")
    edges = worker_case.get_edges()
    assert len(edges) == 1
    assert edges == [(node1.uuid, "out", node2.uuid, "a")]


@funcnodes_test
async def test_worker_remove_edge(worker_case):
    node1 = create_test_node(worker_case)
    node2 = create_test_node(worker_case)
    worker_case.add_edge(node1.uuid, "out", node2.uuid, "a")
    edge = worker_case.get_edges()[0]
    worker_case.remove_edge(*edge)
    assert len(worker_case.get_edges()) == 0


@funcnodes_test
async def test_worker_update_node(worker_case):
    node = create_test_node(worker_case)
    worker_case.update_node(node.uuid, {"name": "Updated Node"})
    updated = worker_case.get_node(node.uuid)
    assert updated.name == "Updated Node"


@funcnodes_test
async def test_worker_run(worker_case):
    task = asyncio.create_task(worker_case.run_forever_async())
    await worker_case.wait_for_running(timeout=10)
    assert worker_case.loop_manager.running
    worker_case.stop()
    assert not worker_case.loop_manager.running
    async with asyncio.timeout(5):
        await task


@funcnodes_test
async def test_worker_run_threaded(worker_case):
    runthread = worker_case.run_forever_threaded()
    await worker_case.wait_for_running(timeout=10)
    worker_case.stop()
    runthread.join()
    assert not worker_case.loop_manager.running


@funcnodes_test
async def test_worker_unknown_cmd(worker_case):
    cmd = {"cmd": "unknown", "kwargs": {}}
    with pytest.raises(Worker.UnknownCmdException):
        await worker_case.run_cmd(cmd)


@funcnodes_test
async def test_worker_run_double(worker_case):
    first_task = asyncio.create_task(worker_case.run_forever_async())
    await worker_case.wait_for_running(timeout=10)
    assert worker_case._process_file.exists()

    second_task = asyncio.create_task(worker_case.run_forever_async())
    with pytest.raises(RuntimeError):
        async with asyncio.timeout(10):
            await second_task

    assert not first_task.done()
    assert second_task.done()

    worker_case.stop()
    async with asyncio.timeout(5):
        await first_task


@funcnodes_test
async def test_worker_load(worker_case):
    run_task = asyncio.create_task(worker_case.run_forever_async())
    await worker_case.wait_for_running(timeout=10)
    data = WorkerState(
        backend={
            "nodes": [],
            "prop": {},
            "lib": {
                "shelves": [
                    {
                        "nodes": [
                            {
                                "node_id": "test_node",
                                "inputs": [
                                    {
                                        "type": "int",
                                        "description": None,
                                        "uuid": "a",
                                    }
                                ],
                                "outputs": [
                                    {
                                        "type": "int",
                                        "description": None,
                                        "uuid": "out",
                                    }
                                ],
                                "description": "",
                                "node_name": "testnode",
                            }
                        ],
                        "subshelves": [],
                        "name": "testshelf",
                        "description": "Test shelf",
                    }
                ]
            },
            "edges": [],
        },
        view={},
        meta={},
        external_workers={},
    )

    assert worker_case.nodespace_loop is not None
    assert worker_case.loop_manager is not None
    assert worker_case.loop_manager.running
    assert worker_case.nodespace_loop._manager is not None

    await worker_case.load(data)

    mutated = deepcopy(data)
    mutated["meta"]["id"] = "abc"
    with pytest.raises(ValueError):
        await worker_case.load(mutated)

    mutated = deepcopy(data)
    mutated["meta"]["id"] = None
    await worker_case.load(mutated)

    mutated = deepcopy(data)
    mutated["meta"]["id"] = "a" * 32
    await worker_case.load(mutated)

    worker_case.stop()
    async with asyncio.timeout(5):
        await run_task


@funcnodes_test
async def test_get_io_value(interacting_worker):
    worker, node1, node2 = interacting_worker
    nodes = worker.get_nodes()
    assert len(nodes) == 2
    value = worker.get_io_value(node1.uuid, "out")
    assert value == 1


@funcnodes_test
async def test_set_io_value(interacting_worker):
    worker, node1, _ = interacting_worker
    worker.set_io_value(node1.uuid, "a", 2, set_default=True)
    await asyncio.sleep(0.1)
    value = worker.get_io_value(node1.uuid, "out")
    assert value == 2


@funcnodes_test
async def test_update_node_view(interacting_worker):
    worker, node1, node2 = interacting_worker
    worker.update_node_view(
        node1.uuid,
        NodeViewState(
            pos=(10, 10),
            size=(100, 100),
        ),
    )
    view_state = worker.view_state()
    expected_nodes = {
        node1.uuid: {"pos": (10, 10), "size": (100, 100)},
        node2.uuid: {"pos": (0, 0), "size": (200, 250)},
    }
    assert view_state["nodes"] == expected_nodes


@funcnodes_test
async def test_add_package_dependency(interacting_worker):
    worker, _, _ = interacting_worker
    await worker.add_package_dependency("funcnodes-basic")
    assert "funcnodes-basic" in worker._package_dependencies


@funcnodes_test
async def test_upload(interacting_worker):
    worker, _, _ = interacting_worker
    data = b"hello"
    worker.upload(data, "test.txt")
    assert os.path.exists(os.path.join(worker.files_path, "test.txt"))
    with pytest.raises(ValueError):
        worker.upload(data, "../test.txt")


class _CountingShelfConfig(ExternalWorkerConfig):
    marker: int = 0


class CountingShelfWorker(FuncNodesExternalWorker):
    NODECLASSID = "test_counting_shelf_worker"
    config_cls = _CountingShelfConfig

    def __init__(self, *args, **kwargs) -> None:
        self.shelf_calls = 0
        self.last_marker = 0
        super().__init__(*args, **kwargs)
        self.last_marker = self.config.marker

    async def loop(self):
        await asyncio.sleep(0.01)

    def post_config_update(self):
        self.last_marker = self.config.marker
        self.emit("nodes_update")

    def get_nodeshelf(self):
        self.shelf_calls += 1
        return None


class _SecretiveConfig(ExternalWorkerConfig):
    EXPORT_EXCLUDE_FIELDS: ClassVar[set[str]] = {"class_hidden"}

    class_hidden: str = "secret-from-class"
    field_hidden: str = Field(
        default="secret-from-field", json_schema_extra={"export": False}
    )
    visible: str = "visible"


class SecretiveWorker(FuncNodesExternalWorker):
    NODECLASSID = "test_secretive_worker"
    config_cls = _SecretiveConfig

    def get_nodeshelf(self):
        return None


@funcnodes_test
async def test_export_worker_excludes_external_worker_sensitive_fields(
    running_test_worker: _TestWorkerClass,
):
    external_worker = running_test_worker
    worker_instance = external_worker.add_local_worker(
        SecretiveWorker, "secretive-worker"
    )
    external_worker.update_external_worker(
        worker_instance.uuid,
        SecretiveWorker.NODECLASSID,
        config={
            "class_hidden": "top-secret",
            "field_hidden": "token-123",
            "visible": "fine",
        },
    )
    await asyncio.sleep(0.2)

    full_state = external_worker.get_save_state()
    assert len(full_state["external_workers"][SecretiveWorker.NODECLASSID]) == 1, (
        full_state
    )
    saved_config = full_state["external_workers"][SecretiveWorker.NODECLASSID][0][
        "config"
    ]
    assert saved_config["class_hidden"] == "top-secret"
    assert saved_config["field_hidden"] == "token-123"
    assert saved_config["visible"] == "fine"

    exported = external_worker.export_worker()
    with zipfile.ZipFile(io.BytesIO(exported), "r") as zf:
        exported_state = json.loads(zf.read("state").decode("utf-8"))

    exported_config = exported_state["external_workers"][SecretiveWorker.NODECLASSID][
        0
    ]["config"]
    assert "class_hidden" not in exported_config
    assert "field_hidden" not in exported_config
    assert exported_config["visible"] == "fine"


@funcnodes_test
async def test_update_external_worker_refreshes_shelf_without_event(
    worker_instance: _TestWorkerClass,
):
    ex_worker_instance = worker_instance.add_local_worker(
        CountingShelfWorker, "counting-shelf-worker"
    )
    assert ex_worker_instance.shelf_calls == 1

    worker_instance.update_external_worker(
        ex_worker_instance.uuid,
        CountingShelfWorker.NODECLASSID,
        config={"marker": 1},
    )

    await asyncio.sleep(0.2)

    assert ex_worker_instance.shelf_calls == 2
