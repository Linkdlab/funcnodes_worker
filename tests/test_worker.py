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
from funcnodes_worker.remote_worker import RemoteWorker
from funcnodes_worker.worker import WorkerState, NodeViewState
from pydantic import Field


from pytest_funcnodes import funcnodes_test


class _TestWorkerClass(Worker):
    def _on_nodespaceerror(self, error: Exception, src: fn.NodeSpace):
        """handle nodespace errors"""

    def on_nodespaceevent(self, event, **kwargs):
        """handle nodespace events"""


class _CapturingRemoteWorker(RemoteWorker):
    def __init__(self, *args, **kwargs):
        """Initialize a remote worker that records outbound test messages."""

        super().__init__(*args, **kwargs)
        self.sent_messages = []
        self.sent_byte_headers = []

    async def sendmessage(self, msg: str, **kwargs):
        """Capture one JSON message that would be sent to the frontend."""

        self.sent_messages.append(json.loads(msg))

    async def send_bytes(self, data: bytes, header: dict, **sendkwargs):
        """Capture one binary message header for IO value event tests."""

        self.sent_byte_headers.append(header)


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
    assert not worker_case.nodespace.lib.has_node_id("funcnodes_core.group.input")
    assert not worker_case.nodespace.lib.has_node_id("funcnodes_core.group.output")


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
        "autostart": "never",
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
        "autostart": "never",
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
async def test_worker_case_missing_autostart_defaults_never(worker_case):
    config = worker_case.config
    config.pop("autostart", None)

    updated = worker_case.update_config(config)

    assert updated["autostart"] == "never"


@funcnodes_test
async def test_worker_case_legacy_autostart_booleans_migrate(worker_case):
    true_config = worker_case.update_config({**worker_case.config, "autostart": True})
    false_config = worker_case.update_config({**worker_case.config, "autostart": False})

    assert true_config["autostart"] == "unless-stopped"
    assert false_config["autostart"] == "never"


@funcnodes_test
async def test_worker_case_update_worker_config(worker_case):
    updated = worker_case.update_worker_config(
        name="renamed worker",
        autostart="unless-stopped",
        update_on_startup={"funcnodes": False},
    )

    assert updated["name"] == "renamed worker"
    assert updated["autostart"] == "unless-stopped"
    assert updated["update_on_startup"]["funcnodes"] is False
    assert updated["update_on_startup"]["funcnodes-core"] is True
    assert worker_case.name() == "renamed worker"

    loaded = worker_case.load_config()
    assert loaded is not None
    assert loaded["name"] == "renamed worker"
    assert loaded["autostart"] == "unless-stopped"


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
    """Full state should expose test and built-in executable group shelves."""

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
                                "node_id": "funcnodes_core.group",
                                "inputs": [],
                                "outputs": [],
                                "description": None,
                                "node_name": "Group",
                            }
                        ],
                        "subshelves": [
                            {
                                "nodes": [
                                    {
                                        "node_id": "funcnodes_core.group.input",
                                        "inputs": [],
                                        "outputs": [],
                                        "description": None,
                                        "node_name": "Group Input",
                                    },
                                    {
                                        "node_id": "funcnodes_core.group.output",
                                        "inputs": [],
                                        "outputs": [],
                                        "description": None,
                                        "node_name": "Group Output",
                                    },
                                ],
                                "subshelves": [],
                                "name": "gateways",
                                "description": "",
                            }
                        ],
                        "name": "groups",
                        "description": "",
                    },
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
async def test_worker_get_nodespace_at_path_returns_root_snapshot(worker_case):
    node = create_test_node(worker_case)

    snapshot = worker_case.get_nodespace_at_path([])

    assert snapshot["path"] == []
    assert [serialized["id"] for serialized in snapshot["nodes"]] == [node.uuid]
    assert snapshot["edges"] == []
    assert snapshot["groups"] == {}


@funcnodes_test
async def test_worker_get_nodespace_at_path_returns_group_snapshot(worker_case):
    group = fn.GroupNode(uuid="group-node", name="Group Node")
    inner = testnode(uuid="inner-node", trigger_on_create=False)
    group.inner_nodespace.add_node_instance(inner)
    worker_case.nodespace.add_node_instance(group)

    snapshot = worker_case.get_nodespace_at_path(
        [{"groupNodeId": "group-node", "label": "Group Node"}]
    )

    assert snapshot["path"] == [{"groupNodeId": "group-node", "label": "Group Node"}]
    assert [serialized["id"] for serialized in snapshot["nodes"]] == [
        group.group_input_node_uuid,
        group.group_output_node_uuid,
        "inner-node",
    ]
    nodes_by_id = {serialized["id"]: serialized for serialized in snapshot["nodes"]}
    assert nodes_by_id[group.group_input_node_uuid]["properties"][
        "frontend:pos"
    ] == [0, 0]
    assert nodes_by_id[group.group_output_node_uuid]["properties"][
        "frontend:pos"
    ] == [480, 0]


@funcnodes_test
async def test_worker_get_nodespace_at_path_rejects_non_group_node(worker_case):
    node = create_test_node(worker_case)

    with pytest.raises(ValueError, match="not a GroupNode"):
        worker_case.get_nodespace_at_path(
            [{"groupNodeId": node.uuid, "label": "Not a group"}]
        )


@funcnodes_test
async def test_worker_resolves_nested_nodespace_event_paths(worker_case):
    """Worker event path helpers should identify nested group nodespaces."""

    outer = fn.GroupNode(uuid="outer-group", name="Outer Group")
    nested = fn.GroupNode(uuid="nested-group", name="Nested Group")
    outer.inner_nodespace.add_node_instance(nested)
    worker_case.nodespace.add_node_instance(outer)

    assert worker_case._get_nodespace_path_for_event_source(
        nested.inner_nodespace
    ) == [
        {"groupNodeId": "outer-group", "label": "Outer Group"},
        {"groupNodeId": "nested-group", "label": "Nested Group"},
    ]


@funcnodes_test
async def test_remote_worker_adds_path_to_root_nodespace_events(tmp_path):
    """Remote worker events should include the path that owns the event node."""

    worker = _CapturingRemoteWorker(data_path=tmp_path, uuid="event-path-worker")
    try:
        bundle = worker.on_nodespaceevent(
            "triggerstart",
            worker.nodespace,
            node="root-node",
        )
    finally:
        worker.stop()

    assert bundle["data"]["path"] == []


@funcnodes_test
async def test_remote_worker_adds_inner_path_to_bubbled_group_events(tmp_path):
    """Bubbled group events should target the group's inner nodespace path."""

    worker = _CapturingRemoteWorker(data_path=tmp_path, uuid="inner-event-path-worker")
    group = fn.GroupNode(uuid="group-node", name="Group Node")
    worker.nodespace.add_node_instance(group)
    try:
        bundle = worker.on_nodespaceevent(
            "inner_triggerstart",
            worker.nodespace,
            node="group-node",
            inner_node="inner-node",
            inner_event="triggerstart",
        )
    finally:
        worker.stop()

    assert bundle["data"]["parent_path"] == []
    assert bundle["data"]["path"] == [
        {"groupNodeId": "group-node", "label": "Group Node"}
    ]


@funcnodes_test
async def test_worker_update_node_at_path_updates_group_internal_node(worker_case):
    """Path-aware node updates should mutate the group's inner nodespace."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    inner = testnode(uuid="inner-node")
    group.inner_nodespace.add_node_instance(inner)
    worker_case.nodespace.add_node_instance(group)

    worker_case.update_node_at_path(
        [{"groupNodeId": "group-node", "label": "Group Node"}],
        "inner-node",
        {"name": "Updated Inner"},
    )

    assert group.inner_nodespace.get_node_by_id("inner-node").name == "Updated Inner"


@funcnodes_test
async def test_worker_connect_at_path_connects_group_internal_nodes(worker_case):
    """Path-aware edge creation should connect nodes inside the group."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    source = testnode(uuid="inner-source", trigger_on_create=False)
    target = testnode(uuid="inner-target", trigger_on_create=False)
    group.inner_nodespace.add_node_instance(source)
    group.inner_nodespace.add_node_instance(target)
    worker_case.nodespace.add_node_instance(group)

    worker_case.connect_at_path(
        [{"groupNodeId": "group-node", "label": "Group Node"}],
        "inner-source",
        "out",
        "inner-target",
        "a",
    )

    assert group.inner_nodespace.serialize_edges() == [
        ("inner-source", "out", "inner-target", "a")
    ]


@funcnodes_test
async def test_worker_remove_node_at_path_removes_group_internal_node(worker_case):
    """Path-aware node removal should delete nodes from the group internals."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    inner = testnode(uuid="inner-node", trigger_on_create=False)
    group.inner_nodespace.add_node_instance(inner)
    worker_case.nodespace.add_node_instance(group)

    worker_case.remove_node_at_path(
        [{"groupNodeId": "group-node", "label": "Group Node"}],
        "inner-node",
    )

    with pytest.raises(ValueError):
        group.inner_nodespace.get_node_by_id("inner-node")


@funcnodes_test
async def test_worker_add_node_at_path_rejects_manual_group_gateways(worker_case):
    """Users should not add group gateway implementation nodes manually."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    worker_case.nodespace.add_node_instance(group)
    path = [{"groupNodeId": "group-node", "label": "Group Node"}]
    group.inner_nodespace.lib.add_node(fn.GroupInputNode, ["groups", "gateways"])
    group.inner_nodespace.lib.add_node(fn.GroupOutputNode, ["groups", "gateways"])

    with pytest.raises(ValueError, match="managed by GroupNode"):
        worker_case.add_node_at_path(path, "funcnodes_core.group.input")
    with pytest.raises(ValueError, match="managed by GroupNode"):
        worker_case.add_node_at_path(path, "funcnodes_core.group.output")


@funcnodes_test
async def test_worker_remove_node_at_path_rejects_owned_group_gateways(worker_case):
    """Users should not remove the required gateway nodes from a group."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    worker_case.nodespace.add_node_instance(group)
    path = [{"groupNodeId": "group-node", "label": "Group Node"}]

    with pytest.raises(ValueError, match="managed by GroupNode"):
        worker_case.remove_node_at_path(path, group.group_input_node_uuid)
    with pytest.raises(ValueError, match="managed by GroupNode"):
        worker_case.remove_node_at_path(path, group.group_output_node_uuid)


@funcnodes_test
async def test_worker_io_value_at_path_targets_group_internal_node(worker_case):
    """Path-aware IO commands should read and write an internal node IO."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    inner = testnode(uuid="inner-node", trigger_on_create=False)
    group.inner_nodespace.add_node_instance(inner)
    worker_case.nodespace.add_node_instance(group)
    path = [{"groupNodeId": "group-node", "label": "Group Node"}]

    worker_case.set_io_value_at_path(path, "inner-node", "a", 5, set_default=True)

    assert worker_case.get_io_value_at_path(path, "inner-node", "a") == 5


@funcnodes_test
async def test_worker_group_nodes_as_node_at_path_creates_nested_group(worker_case):
    """Executable grouping should target the resolved active nodespace path."""

    outer = fn.GroupNode(uuid="outer-group", name="Outer Group")
    first = testnode(uuid="inner-first", trigger_on_create=False)
    second = testnode(uuid="inner-second", trigger_on_create=False)
    outer.inner_nodespace.add_node_instance(first)
    outer.inner_nodespace.add_node_instance(second)
    worker_case.nodespace.add_node_instance(outer)

    created = worker_case.group_nodes_as_node_at_path(
        [{"groupNodeId": "outer-group", "label": "Outer Group"}],
        ["inner-first", "inner-second"],
        name="Nested Group",
    )

    nested_group = outer.inner_nodespace.get_node_by_id(created["id"])
    assert isinstance(nested_group, fn.GroupNode)
    assert nested_group.name == "Nested Group"
    assert {node.uuid for node in nested_group.inner_nodespace.nodes}.issuperset(
        {"inner-first", "inner-second"}
    )


@funcnodes_test
async def test_worker_ungroup_node_at_path_restores_nested_group_nodes(worker_case):
    """Executable ungrouping should target the resolved active nodespace path."""

    outer = fn.GroupNode(uuid="outer-group", name="Outer Group")
    first = testnode(uuid="inner-first", trigger_on_create=False)
    second = testnode(uuid="inner-second", trigger_on_create=False)
    outer.inner_nodespace.add_node_instance(first)
    outer.inner_nodespace.add_node_instance(second)
    nested = outer.inner_nodespace.group_nodes_as_node(
        ["inner-first", "inner-second"], group_id="nested-group"
    )
    worker_case.nodespace.add_node_instance(outer)

    restored = worker_case.ungroup_node_at_path(
        [{"groupNodeId": "outer-group", "label": "Outer Group"}],
        nested.uuid,
    )

    assert {node["id"] for node in restored} == {"inner-first", "inner-second"}
    assert outer.inner_nodespace.get_node_by_id("inner-first") is first
    assert outer.inner_nodespace.get_node_by_id("inner-second") is second


@funcnodes_test
async def test_worker_materialize_group_at_path_converts_legacy_group(worker_case):
    """Legacy UI group materialization should be explicit and path-aware."""

    outer = fn.GroupNode(uuid="outer-group", name="Outer Group")
    first = testnode(uuid="inner-first", trigger_on_create=False)
    second = testnode(uuid="inner-second", trigger_on_create=False)
    outer.inner_nodespace.add_node_instance(first)
    outer.inner_nodespace.add_node_instance(second)
    outer.inner_nodespace.groups.group_together(
        ["inner-first", "inner-second"], [], new_group_id="legacy"
    )
    worker_case.nodespace.add_node_instance(outer)

    created = worker_case.materialize_group_at_path(
        [{"groupNodeId": "outer-group", "label": "Outer Group"}],
        "legacy",
    )

    assert isinstance(outer.inner_nodespace.get_node_by_id(created["id"]), fn.GroupNode)
    assert "legacy" not in outer.inner_nodespace.groups.get_all_groups()


@funcnodes_test
async def test_worker_add_group_input_at_path_updates_public_and_gateway_io(worker_case):
    """Path-aware group input creation should update both boundary IO sides."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    worker_case.nodespace.add_node_instance(group)

    worker_case.add_group_input_at_path(
        [],
        "group-node",
        {"id": "value", "name": "Value", "type": "int", "does_trigger": False},
    )

    assert group.inputs["value"].name == "Value"
    assert group.group_input_node.outputs["value"].name == "Value"


@funcnodes_test
async def test_worker_add_group_input_at_path_auto_generates_untyped_io(worker_case):
    """Path-aware group input creation should allocate numbered name-only IDs."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    worker_case.nodespace.add_node_instance(group)

    worker_case.add_group_input_at_path([], "group-node", {"name": "Value"})

    boundary_id = next(
        io_id for io_id in group.inputs if io_id not in {"_triggerinput"}
    )
    assert boundary_id == "ip1"
    assert group.inputs[boundary_id].name == "Value"
    assert group.inputs[boundary_id].serialize()["type"] == "Any"
    assert group.group_input_node.outputs[boundary_id].name == "Value"
    assert group.group_input_node.outputs[boundary_id].serialize()["type"] == "Any"


@funcnodes_test
async def test_worker_add_group_output_at_path_updates_public_and_gateway_io(worker_case):
    """Path-aware group output creation should update both boundary IO sides."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    worker_case.nodespace.add_node_instance(group)

    worker_case.add_group_output_at_path(
        [],
        "group-node",
        {"id": "result", "name": "Result", "type": "float"},
    )

    assert group.outputs["result"].name == "Result"
    assert group.group_output_node.inputs["result"].name == "Result"


@funcnodes_test
async def test_worker_add_group_output_at_path_auto_generates_untyped_io(worker_case):
    """Path-aware group output creation should allocate numbered name-only IDs."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    worker_case.nodespace.add_node_instance(group)

    worker_case.add_group_output_at_path([], "group-node", {"name": "Result"})

    boundary_id = next(
        io_id for io_id in group.outputs if io_id not in {"_triggeroutput"}
    )
    assert boundary_id == "op1"
    assert group.outputs[boundary_id].name == "Result"
    assert group.outputs[boundary_id].serialize()["type"] == "Any"
    assert group.group_output_node.inputs[boundary_id].name == "Result"
    assert group.group_output_node.inputs[boundary_id].serialize()["type"] == "Any"


@funcnodes_test
async def test_worker_update_group_io_at_path_renames_public_and_gateway_io(worker_case):
    """Path-aware boundary rename should update public and gateway IO."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    group.add_group_input(id="value", name="Value", type=int, does_trigger=False)
    worker_case.nodespace.add_node_instance(group)

    worker_case.update_group_io_at_path(
        [],
        "group-node",
        "value",
        {"name": "Renamed Value"},
    )

    assert group.inputs["value"].name == "Renamed Value"
    assert group.group_input_node.outputs["value"].name == "Renamed Value"


@funcnodes_test
async def test_worker_remove_group_io_at_path_removes_public_gateway_and_edges(
    worker_case,
):
    """Path-aware boundary removal should remove IO and affected edges."""

    source = testnode(uuid="source-node", trigger_on_create=False)
    group = fn.GroupNode(uuid="group-node", name="Group Node")
    group.add_group_input(id="value", name="Value", type=int, does_trigger=False)
    worker_case.nodespace.add_node_instance(source)
    worker_case.nodespace.add_node_instance(group)
    worker_case.add_edge(source.uuid, "out", group.uuid, "value")

    worker_case.remove_group_io_at_path([], "group-node", "value")

    assert "value" not in group.inputs
    assert "value" not in group.group_input_node.outputs
    assert worker_case.nodespace.serialize_edges() == []


@funcnodes_test
async def test_worker_add_group_input_at_path_rejects_duplicate_without_mutation(
    worker_case,
):
    """Duplicate boundary IDs should fail without changing the group shape."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    group.add_group_input(id="value", name="Value", type=int, does_trigger=False)
    worker_case.nodespace.add_node_instance(group)
    input_ids_before = list(group.inputs)

    with pytest.raises(ValueError, match="already exists"):
        worker_case.add_group_input_at_path(
            [],
            "group-node",
            {"id": "value", "name": "Duplicate", "type": "float"},
        )

    assert list(group.inputs) == input_ids_before
    assert group.inputs["value"].name == "Value"


@funcnodes_test
async def test_worker_save_state_persists_group_internal_positions_and_edges(
    worker_case,
):
    """Executable group save state should include edited internal view data."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    source = testnode(uuid="inner-source", trigger_on_create=False)
    target = testnode(uuid="inner-target", trigger_on_create=False)
    group.inner_nodespace.add_node_instance(source)
    group.inner_nodespace.add_node_instance(target)
    source.outputs["out"].connect(target.inputs["a"])
    worker_case.nodespace.add_node_instance(group)

    worker_case.update_node_at_path(
        [{"groupNodeId": "group-node", "label": "Group Node"}],
        "inner-source",
        {"properties": {"frontend:pos": [32, 64], "frontend:size": [111, 222]}},
    )

    saved = worker_case.get_save_state()
    saved_group = next(
        node for node in saved["backend"]["nodes"] if node["id"] == "group-node"
    )
    payload = saved_group["properties"]["group"]
    saved_inner_source = next(
        node
        for node in payload["inner_nodespace"]["nodes"]
        if node["id"] == "inner-source"
    )

    assert saved_inner_source["properties"]["frontend:pos"] == [32, 64]
    assert saved_inner_source["properties"]["frontend:size"] == [111, 222]
    assert payload["inner_nodespace"]["edges"] == [
        ["inner-source", "out", "inner-target", "a"]
    ]
    assert "inner-source" not in saved.get("view", {}).get("nodes", {})


@funcnodes_test
async def test_worker_save_load_restores_group_boundary_io(tmp_path, worker_case):
    """Boundary IO edits should round-trip through worker save/load."""

    group = fn.GroupNode(uuid="group-node", name="Group Node")
    worker_case.nodespace.add_node_instance(group)
    worker_case.add_group_input_at_path(
        [],
        "group-node",
        {"id": "value", "name": "Value", "type": "int", "does_trigger": False},
    )
    worker_case.add_group_output_at_path(
        [],
        "group-node",
        {"id": "result", "name": "Result", "type": "float"},
    )
    saved = worker_case.get_save_state()

    loaded = _TestWorkerClass(
        data_path=tmp_path / "boundary-loaded",
        default_nodes=[testshelf],
        uuid="boundary-loaded-worker",
    )
    try:
        await loaded.load_data(saved)
        loaded_group = loaded.nodespace.get_node_by_id("group-node")

        assert isinstance(loaded_group, fn.GroupNode)
        assert loaded_group.inputs["value"].name == "Value"
        assert loaded_group.outputs["result"].name == "Result"
        assert loaded_group.group_input_node.outputs["value"].name == "Value"
        assert loaded_group.group_output_node.inputs["result"].name == "Result"
    finally:
        loaded.stop()


@funcnodes_test
async def test_worker_save_load_preserves_nested_group_navigation(
    tmp_path,
    worker_case,
):
    """Nested executable groups should be navigable after save/load."""

    outer = fn.GroupNode(uuid="outer-group", name="Outer Group")
    nested = fn.GroupNode(uuid="nested-group", name="Nested Group")
    inner = testnode(uuid="nested-inner", trigger_on_create=False)
    nested.inner_nodespace.add_node_instance(inner)
    outer.inner_nodespace.add_node_instance(nested)
    worker_case.nodespace.add_node_instance(outer)
    saved = worker_case.get_save_state()

    loaded = _TestWorkerClass(
        data_path=tmp_path / "nested-loaded",
        default_nodes=[testshelf],
        uuid="nested-loaded-worker",
    )
    try:
        await loaded.load_data(saved)
        snapshot = loaded.get_nodespace_at_path(
            [
                {"groupNodeId": "outer-group", "label": "Outer Group"},
                {"groupNodeId": "nested-group", "label": "Nested Group"},
            ]
        )

        assert snapshot["path"] == [
            {"groupNodeId": "outer-group", "label": "Outer Group"},
            {"groupNodeId": "nested-group", "label": "Nested Group"},
        ]
        assert "nested-inner" in [node["id"] for node in snapshot["nodes"]]
    finally:
        loaded.stop()


@funcnodes_test
async def test_worker_save_load_preserves_legacy_visual_groups(
    tmp_path,
    worker_case,
):
    """Legacy NodeSpace.groups metadata should still round-trip."""

    first = testnode(uuid="first-node", trigger_on_create=False)
    second = testnode(uuid="second-node", trigger_on_create=False)
    worker_case.nodespace.add_node_instance(first)
    worker_case.nodespace.add_node_instance(second)
    worker_case.nodespace.groups.group_together(
        ["first-node", "second-node"], [], new_group_id="legacy-group"
    )
    saved = worker_case.get_save_state()

    loaded = _TestWorkerClass(
        data_path=tmp_path / "legacy-loaded",
        default_nodes=[testshelf],
        uuid="legacy-loaded-worker",
    )
    try:
        await loaded.load_data(saved)

        assert "legacy-group" in loaded.nodespace.groups.get_all_groups()
        assert loaded.get_nodespace_at_path([])["groups"][
            "legacy-group"
        ]["node_ids"] == ["first-node", "second-node"]
    finally:
        loaded.stop()


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
