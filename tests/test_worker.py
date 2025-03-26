from unittest import IsolatedAsyncioTestCase, TestCase
import funcnodes_core as fn
from funcnodes_worker import Worker
from funcnodes_worker.worker import WorkerState, NodeViewState
import tempfile
import os
import sys
from pathlib import Path
import asyncio
import time
import json
from copy import deepcopy
import logging
from funcnodes_core.testing import (
    teardown as fn_teardown,
    set_in_test as fn_set_in_test,
)


class _TestWorkerClass(Worker):
    def _on_nodespaceerror(
        self,
        error: Exception,
        src: fn.NodeSpace,
    ):
        """handle nodespace errors"""

    def on_nodespaceevent(self, event, **kwargs):
        """handle nodespace events"""


@fn.NodeDecorator(node_id="test_node")
def testnode(a: int = 1) -> int:
    return a


testshelf = fn.Shelf(
    name="testshelf", description="Test shelf", subshelves=[], nodes=[testnode]
)


class TestWorkerInitCases(TestCase):
    Workerclass = _TestWorkerClass
    workerkwargs = {}

    def setUp(self):
        fn_set_in_test()
        self.tempdir = tempfile.TemporaryDirectory()
        self.workerkwargs["data_path"] = self.tempdir.name
        self.workerkwargs["uuid"] = "testuuid"
        self.worker = None

    def tearDown(self):
        if self.worker:
            self.worker.stop()
            del self.worker
        fn_teardown()

        self.tempdir.cleanup()

    def test_initialization(self):
        self.worker = self.Workerclass(**self.workerkwargs)
        self.assertIsInstance(self.worker, self.Workerclass)

    def test_with_default_nodes(self):
        self.worker = self.Workerclass(**self.workerkwargs, default_nodes=[testshelf])
        self.assertIsInstance(self.worker, self.Workerclass)

    def test_with_debug(self):
        self.worker = self.Workerclass(**self.workerkwargs, debug=True)
        self.assertIsInstance(self.worker, self.Workerclass)
        import logging

        self.assertEqual(self.worker.logger.level, logging.DEBUG)

    def test_initandrun(self):
        import threading

        wpath = fn.config.get_config_dir() / "workers"
        olfiles = (
            os.listdir(fn.config.get_config_dir() / "workers") if wpath.exists() else []
        )
        runthread = threading.Thread(
            target=self.Workerclass.init_and_run_forever,
            kwargs=self.workerkwargs,
            daemon=True,
        )
        runthread.start()
        workerdir = fn.config.get_config_dir() / "workers"

        # wait max 10 seconds for the worker to start
        for i in range(100):
            if workerdir.exists():
                break
            time.sleep(0.1)
        time.sleep(0.5)

        newfiles = os.listdir(fn.config.get_config_dir() / "workers")

        self.assertEqual(len(set(newfiles) - set(olfiles)), 1, (olfiles, newfiles))

        processfile = (set(newfiles) - set(olfiles)).pop()
        self.assertTrue(
            processfile.startswith("worker_") and processfile.endswith(".p")
        )
        stopcmd = {"cmd": "stop_worker"}

        with open(fn.config.get_config_dir() / "workers" / processfile, "r") as f:
            pid = f.read()

        self.assertTrue(pid.isdigit(), pid)

        self.assertEqual(os.getpid(), int(pid))

        with open(fn.config.get_config_dir() / "workers" / processfile, "w") as f:
            json.dump(stopcmd, f)

        # wait max 5 seconds for the worker to stop
        for i in range(150):
            if not runthread.is_alive():
                break
            time.sleep(0.1)

        log = None
        if runthread.is_alive():
            self.assertTrue("funcnodes.testuuid.log" in os.listdir(self.tempdir.name))
            with open(
                os.path.join(self.tempdir.name, "funcnodes.testuuid.log"), "r"
            ) as f:
                log = f.read()

        self.assertFalse(runthread.is_alive(), log)

        runthread.join()


class TestWorkerCase(IsolatedAsyncioTestCase):
    Workerclass = _TestWorkerClass

    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        fn_set_in_test()
        self.worker = self.Workerclass(
            data_path=Path(self.tempdir.name), default_nodes=[testshelf], debug=True
        )

    async def asyncTearDown(self):
        self.worker.stop()
        fn_teardown()
        self.tempdir.cleanup()

    def test_initialization(self):
        self.assertIsInstance(self.worker, self.Workerclass)
        self.assertTrue(hasattr(self.worker, "nodespace"))
        self.assertTrue(hasattr(self.worker, "loop_manager"))

    def test_uuid(self):
        self.assertIsInstance(self.worker.uuid(), str)

    def test_config_generation(self):
        config = fn.JSONEncoder.apply_custom_encoding(self.worker.config)

        self.maxDiff = None
        expected = {
            "uuid": self.worker.uuid(),
            "name": self.worker.name(),
            "data_path": Path(self.tempdir.name).absolute().resolve().as_posix(),
            "package_dependencies": {},
            "pid": os.getpid(),
            "python_path": sys.executable,
            "type": self.Workerclass.__name__,
            "env_path": None,
            "update_on_startup": {
                "funcnodes": True,
                "funcnodes-core": True,
                "funcnodes-worker": True,
            },
            "worker_dependencies": {},
        }
        self.assertEqual(config, expected)

    def test_exportable_config(self):
        config = self.worker.exportable_config()
        self.assertIsInstance(config, dict)
        expected = {
            "name": self.worker.name(),
            "package_dependencies": {},
            "type": self.Workerclass.__name__,
            "update_on_startup": {
                "funcnodes": True,
                "funcnodes-core": True,
                "funcnodes-worker": True,
            },
            "worker_dependencies": {},
        }
        self.assertEqual(config, expected)

    def test_write_config(self):
        config_path = self.worker._config_file
        self.worker.write_config()
        print(config_path)
        self.assertTrue(os.path.exists(config_path))

    def test_load_config(self):
        self.worker.write_config()
        config = self.worker.load_config()
        self.assertIsNotNone(config)
        self.assertEqual(config["uuid"], self.worker.uuid())

    def test_process_file_handling(self):
        self.worker._write_process_file()
        process_file = self.worker._process_file
        self.assertTrue(os.path.exists(process_file))

    def test_save_state(self):
        self.worker.save()
        state_path = self.worker.local_nodespace
        self.assertTrue(os.path.exists(state_path))

    async def test_run_cmd(self):
        cmd = {"cmd": "uuid", "kwargs": {}}
        result = await self.worker.run_cmd(cmd)
        self.assertEqual(result, self.worker.uuid())

    async def test_full_state(self):
        ser = fn.JSONEncoder.apply_custom_encoding(self.worker.full_state())
        self.assertIsInstance(ser, dict)
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
            "meta": {"id": self.worker.nodespace_id, "version": fn.__version__},
        }

        ser.pop("view", None)  # because this differes on other installations
        self.assertEqual(ser, expected)

    def test_add_node(self):
        node_id = "test_node"
        addednode = self.worker.add_node(node_id)
        self.assertIsInstance(addednode, fn.Node)
        self.assertIsInstance(addednode, testnode)

        node = self.worker.get_node(addednode.uuid)
        self.assertIsNotNone(node)
        self.assertEqual(node, addednode)
        return node

    def test_remove_node(self):
        node = self.test_add_node()
        self.worker.get_node(node.uuid)
        self.worker.remove_node(node.uuid)
        with self.assertRaises(ValueError):
            self.worker.get_node(node.uuid)

    def test_add_edge(self):
        node1 = self.test_add_node()
        node2 = self.test_add_node()

        self.worker.add_edge(node1.uuid, "out", node2.uuid, "a")
        edges = self.worker.get_edges()
        self.assertEqual(len(edges), 1)
        self.assertEqual(
            edges,
            [
                (
                    node1.uuid,
                    "out",
                    node2.uuid,
                    "a",
                )
            ],
        )

    def test_remove_edge(self):
        self.test_add_edge()
        edge = self.worker.get_edges()[0]
        self.worker.remove_edge(*edge)
        self.assertEqual(len(self.worker.get_edges()), 0)

    def test_update_node(self):
        node = self.test_add_node()
        self.worker.update_node(node.uuid, {"name": "Updated Node"})
        node = self.worker.get_node(node.uuid)
        self.assertEqual(node.name, "Updated Node")

    async def test_run(self):
        asyncio.create_task(self.worker.run_forever_async())
        await self.worker.wait_for_running(timeout=10)
        self.assertTrue(self.worker.loop_manager.running)
        self.worker.stop()
        self.assertFalse(self.worker.loop_manager.running)

    async def test_run_threaded(self):
        runthread = self.worker.run_forever_threaded()
        await self.worker.wait_for_running(timeout=10)
        self.worker.stop()
        runthread.join()
        self.assertFalse(self.worker.loop_manager.running)
        t = time.time()

    async def test_unknown_cmd(self):
        cmd = {"cmd": "unknown", "kwargs": {}}
        with self.assertRaises(Worker.UnknownCmdException):
            await self.worker.run_cmd(cmd)

    async def test_run_double(self):
        t1 = asyncio.create_task(self.worker.run_forever_async())
        await self.worker.wait_for_running(timeout=10)

        t2 = asyncio.create_task(self.worker.run_forever_async())
        with self.assertRaises(RuntimeError):
            await t2

        # t1 should still be running while t2 should be done
        self.assertFalse(t1.done())
        self.assertTrue(t2.done())

        self.worker.stop()
        async with asyncio.timeout(5):
            await t1

    async def test_load(self):
        asyncio.create_task(self.worker.run_forever_async())
        await self.worker.wait_for_running(timeout=10)
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
            dependencies={},
            external_workers={},
        )

        self.assertIsNotNone(self.worker.nodespace_loop)
        self.assertIsNotNone(self.worker.loop_manager)
        self.assertTrue(self.worker.loop_manager.running)

        self.assertIsNotNone(self.worker.nodespace_loop._manager)
        await self.worker.load(data)

        _d = deepcopy(data)
        _d["meta"]["id"] = "abc"
        # should raise an id to short erroer
        with self.assertRaises(ValueError):
            await self.worker.load(_d)

        _d = deepcopy(data)
        _d["meta"]["id"] = None
        # this should work
        await self.worker.load(_d)

        _d = deepcopy(data)
        _d["meta"]["id"] = "a" * 32
        # this should work
        await self.worker.load(_d)


class TestWorkerInteractingCase(IsolatedAsyncioTestCase):
    Workerclass = _TestWorkerClass

    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        fn_set_in_test()
        self.worker = self.Workerclass(
            data_path=Path(self.tempdir.name), default_nodes=[testshelf], debug=True
        )

        asyncio.create_task(self.worker.run_forever_async())
        async with asyncio.timeout(10):
            while self.worker.runstate != "running":
                await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        node_id = "test_node"
        self.node1 = self.worker.add_node(node_id)
        self.node2 = self.worker.add_node(node_id)
        await asyncio.sleep(0.5)
        self.worker.add_edge(self.node1.uuid, "out", self.node2.uuid, "a")
        await asyncio.sleep(0.5)  # let the nodes trigger

    def tearDown(self):
        self.worker.stop()
        fn_teardown()
        self.tempdir.cleanup()

    async def test_get_io_value(self):
        # list nodes
        nodes = self.worker.get_nodes()
        self.assertEqual(len(nodes), 2)

        v = self.worker.get_io_value(self.node1.uuid, "out")

        self.assertEqual(v, 1)

    async def test_set_io_value(self):
        self.worker.set_io_value(self.node1.uuid, "a", 2, set_default=True)
        await asyncio.sleep(0.1)  # let the nodes trigger
        v = self.worker.get_io_value(self.node1.uuid, "out")
        self.assertEqual(v, 2)

    async def test_update_node_view(self):
        self.worker.update_node_view(
            self.node1.uuid,
            NodeViewState(
                pos=(10, 10),
                size=(100, 100),
            ),
        )
        vs = self.worker.view_state()
        exp_nodes = {}
        exp_nodes[self.node1.uuid] = {
            "pos": (10, 10),
            "size": (100, 100),
        }
        exp_nodes[self.node2.uuid] = {
            "pos": (0, 0),
            "size": (200, 250),
        }

        self.assertEqual(vs["nodes"], exp_nodes)

    async def test_add_package_dependency(self):
        from funcnodes_worker.utils.modules import AVAILABLE_MODULES

        await self.worker.add_package_dependency("funcnodes-basic")
        self.assertIn("funcnodes-basic", self.worker._package_dependencies)

    async def test_upload(self):
        data = b"hello"
        self.worker.upload(data, "test.txt")

        self.assertTrue(
            os.path.exists(os.path.join(self.worker.files_path, "test.txt"))
        )
        with self.assertRaises(ValueError):
            self.worker.upload(data, "../test.txt")
