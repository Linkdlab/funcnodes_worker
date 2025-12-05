from __future__ import annotations
from abc import ABC, abstractmethod
import gc
from functools import wraps
from typing import (
    List,
    Type,
    Tuple,
    TypedDict,
    Any,
    Literal,
    Optional,
    Dict,
    Union,
    cast,
)
import os
import time
import json
import asyncio
import sys
import importlib
import importlib.util
import inspect
from uuid import uuid4
import warnings
from slugify import slugify

try:
    import psutil
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    psutil = None  # pragma: no cover

import funcnodes_core as fn
from funcnodes_worker.loop import LoopManager, NodeSpaceLoop, CustomLoop
from funcnodes_worker.external_worker import (
    FuncNodesExternalWorker,
    FuncNodesExternalWorkerJson,
)
from funcnodes_core import (
    NodeSpace,
    Shelf,
    NodeSpaceJSON,
    Node,
    NodeJSON,
    JSONEncoder,
    ByteEncoder,
    JSONDecoder,
    NodeClassNotFoundError,
    FullLibJSON,
)
from funcnodes_core.io import (
    ValueOptions,
)
from funcnodes_core.utils import saving
from funcnodes_core.lib import find_shelf, ShelfDict
from exposedfunctionality import exposed_method, get_exposed_methods
from typing_extensions import deprecated
from funcnodes_core.grouping_logic import NodeGroup

import threading
from weakref import WeakSet
import io
import zipfile
import base64
from pathlib import Path

from funcnodes_worker.utils.messages import worker_event_message
from ._opts import (
    USE_VENV,
    venvmngr,
    FUNCNODES_REACT,
    subprocess_monitor,
    USE_SUBPROCESS_MONITOR,
)
from .utils.modules import (
    AVAILABLE_REPOS,
    reload_base,
    install_repo,
    try_import_module,
    install_package,
    version_string_to_Specifier,
)
from funcnodes_core.utils.files import write_json_secure


from funcnodes_core.nodespace import FullNodeJSON


class MetaInfo(TypedDict):
    id: str
    version: str


class NodeViewState(TypedDict):
    pos: Optional[Tuple[int, int]]
    size: Optional[Tuple[int, int]]


class ViewState(TypedDict):
    nodes: Optional[dict[str, NodeViewState]]
    renderoptions: fn.config.RenderOptions


class WorkerState(TypedDict):
    backend: NodeSpaceJSON
    view: ViewState
    meta: MetaInfo
    external_workers: Dict[str, List[FuncNodesExternalWorkerJson]]


class ProgressState(TypedDict):
    message: str
    status: str
    progress: float
    blocking: bool


class FullState(TypedDict):
    backend: NodeSpace
    view: ViewState
    worker: dict[str, list[str]]
    progress_state: ProgressState
    meta: MetaInfo
    worker_dependencies: List[WorkerDict]


class MEvent(TypedDict):
    type: Literal["mevent"]
    event: str
    data: Any


class CmdMessage(TypedDict):
    type: Literal["cmd"]
    cmd: str
    kwargs: dict
    id: str | None


class ResultMessage(TypedDict):
    type: Literal["result"]
    id: str | None
    result: Any


class ProgressStateMessage(ProgressState, TypedDict):
    type: Literal["progress"]


class ErrorMessage(TypedDict):
    type: Literal["error"]
    error: str
    tb: List[str]
    id: str | None


FrontEndKeys = Literal["react"]


class ExtendedFullNodeJSON(FullNodeJSON):
    """
    ExtendedFullNodeJSON is the interface for the serialization of a Node with additional data
    """

    frontend: Optional[NodeViewState]


JSONMessage = Union[CmdMessage, ResultMessage, ErrorMessage, ProgressStateMessage]

EXTERNALWORKERLIB = "_external_worker"


def get_workers_dir() -> Path:
    return fn.config.get_config_dir() / "workers"


def get_worker_dir(worker_id: str) -> Path:
    return get_workers_dir() / f"worker_{worker_id}"


class LocalWorkerLookupLoop(CustomLoop):
    class WorkerNotFoundError(Exception):
        pass

    def __init__(self, client: Worker, path: Optional[Path] = None, delay=5) -> None:
        super().__init__(delay)

        self._path = Path(path).absolute() if path is not None else None
        self._client: Worker = client
        self.worker_classes: List[Type[FuncNodesExternalWorker]] = []
        self._parsed_files = []

    @property
    def path(self):
        if self._path is None:
            p = self._client.local_scripts
        else:
            p = self._path

        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)

        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

        return p

    @path.setter
    def path(self, path: Path):
        self._path = Path(path).absolute()

    async def loop(self):
        # get all .py files in path (deep)
        tasks = []
        # for id, instancedict in FuncNodesExternalWorker.RUNNING_WORKERS.items():
        #     for instance in instancedict.values():
        #         if instance.stopped:
        #             tasks.append(self.stop_local_worker_by_id(id, instance.uuid))
        # print(instance.name, "references:", gc.get_referrers(instance))

        await asyncio.gather(*tasks)

        for root in Path(self.path).rglob("*.py"):
            if root.name not in self._parsed_files:
                module_name = root.stem  # Get filename without .py extension
                spec = importlib.util.spec_from_file_location(module_name, root)

                if spec is None or spec.loader is None:
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                for name, obj in inspect.getmembers(module):
                    if (
                        inspect.isclass(obj)
                        and issubclass(obj, FuncNodesExternalWorker)
                        and obj != FuncNodesExternalWorker
                    ):
                        if obj not in self.worker_classes:
                            self.worker_classes.append(obj)

                self._parsed_files.append(root.name)

        # import gc
        # import objgraph

        # gc.collect()
        # for k, v in FuncNodesExternalWorker.RUNNING_WORKERS.items():
        #    for id, n in v.items():
        #        print("#" * 10)
        #                print(k, id, n)
        #                print(gc.get_referrers(n), len(gc.get_referrers(n)))
        #        objgraph.show_backrefs([n], filename=f"{n}.png", max_depth=5,too_many=)

        # print("-" * 10)
        # TODO: memory leak somewhere, the instance is never removed

    def _worker_instance_stopping_callback(
        self, src: FuncNodesExternalWorker, **kwargs
    ):
        try:
            self._client.logger.debug(
                f"Worker stopping callback for {src.NODECLASSID}.{src.uuid}"
            )
            asyncio.get_event_loop().create_task(
                self.stop_local_worker_by_id(src.NODECLASSID, src.uuid)
            )
        except Exception as e:
            self._client.logger.exception(e)

    def start_local_worker(
        self,
        worker_class: Type[FuncNodesExternalWorker],
        worker_id: str,
        name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        if worker_class not in self.worker_classes:
            self.worker_classes.append(worker_class)

        self._client.logger.info(
            f"starting local worker {worker_class.NODECLASSID} {worker_id}"
        )
        worker_instance: FuncNodesExternalWorker = worker_class(
            workerid=worker_id,
            data_path=self._client.data_path / "external_workers" / worker_id,
            name=name,
            config=config,
        )

        worker_instance.on(
            "stopping",
            self._worker_instance_stopping_callback,
        )
        self._client.loop_manager.add_loop(worker_instance)

        def _inner_update_worker_shelf(*args, **kwargs):
            self._update_worker_shelf(worker_instance)

        worker_instance.on("nodes_update", _inner_update_worker_shelf)

        self._update_worker_shelf(worker_instance)

        self._client.request_save()

        return worker_instance

    def _update_worker_shelf(self, worker_instance: FuncNodesExternalWorker):
        shelf_path = [EXTERNALWORKERLIB, worker_instance.uuid]
        try:
            self._client.nodespace.lib.remove_shelf_path(shelf_path)
        except ValueError:
            pass

        worker_nodeshelf_ref = worker_instance.nodeshelf
        worker_nodeshelf_obj = (
            worker_nodeshelf_ref() if worker_nodeshelf_ref is not None else None
        )
        if worker_nodeshelf_obj is not None:
            try:
                self._client.nodespace.lib.remove_shelf_path(
                    [*shelf_path, worker_nodeshelf_obj.name]
                )
            except ValueError:
                pass
        # perform a gc collect to remove any references to the old nodeshelf
        gc.collect()

        self._client.nodespace.lib.add_nodes(
            worker_instance.get_all_nodeclasses(),
            shelf_path,
        )
        # Reuse worker_nodeshelf_obj instead of accessing the property again
        # to avoid calling get_nodeshelf() twice
        if worker_nodeshelf_obj is not None:
            self._client.nodespace.lib.add_subshelf_weak(
                worker_nodeshelf_ref, shelf_path
            )

    def start_local_worker_by_id(self, worker_id: str):
        for worker_class in self.worker_classes:
            if worker_class.NODECLASSID == worker_id:
                return self.start_local_worker(worker_class, uuid4().hex)

        raise LocalWorkerLookupLoop.WorkerNotFoundError(
            "No worker with id " + worker_id
        )

    async def stop_local_worker_by_id(self, worker_id: str, instance_id: str):
        if worker_id in FuncNodesExternalWorker.RUNNING_WORKERS:
            if instance_id in FuncNodesExternalWorker.RUNNING_WORKERS[worker_id]:
                worker_instance = FuncNodesExternalWorker.RUNNING_WORKERS[worker_id][
                    instance_id
                ]

                self._client.logger.debug(
                    f"stopped worker by id {worker_id} instance {instance_id}"
                )
                self._client.nodespace.lib.remove_nodeclasses(
                    worker_instance.get_all_nodeclasses()
                )

                self._client.nodespace.lib.remove_shelf_path(
                    [EXTERNALWORKERLIB, worker_instance.uuid]
                )

                # Remove the event listener BEFORE calling stop to prevent circular reference
                worker_instance.off("stopping", self._worker_instance_stopping_callback)
                worker_instance.cleanup()
                timeout_duration = 5
                self._client.logger.info(
                    f"Stopping worker {worker_id} instance {instance_id}"
                )
                try:
                    await asyncio.wait_for(
                        worker_instance.stop(), timeout=timeout_duration
                    )
                except asyncio.TimeoutError:
                    self._client.logger.warning(
                        "Timeout: worker_instance.stop() did not complete within "
                        f"{timeout_duration} seconds for worker {worker_id} instance {instance_id}"
                    )

                self._client.logger.info(
                    f"Stopped worker {worker_id} instance {instance_id}"
                )

                self._client.loop_manager.remove_loop(worker_instance)
                del worker_instance

                await self._client.worker_event("external_worker_update")
                self._client.request_save()
                return True
            else:
                raise LocalWorkerLookupLoop.WorkerNotFoundError(
                    "No worker with instance id " + instance_id
                )

        raise LocalWorkerLookupLoop.WorkerNotFoundError(
            "No worker with id " + worker_id + " and instance id " + instance_id
        )

    async def stop_local_workers_by_id(self, worker_id: str) -> bool:
        if worker_id in FuncNodesExternalWorker.RUNNING_WORKERS:
            tasks = []
            for instance_id in list(
                FuncNodesExternalWorker.RUNNING_WORKERS[worker_id].keys()
            ):
                tasks.append(self.stop_local_worker_by_id(worker_id, instance_id))

            # Run all tasks in parallel
            await asyncio.gather(*tasks)
            #
            return True
        return False

    async def get_local_worker_by_id(self, class_id: str, worker_id: str):
        if class_id in FuncNodesExternalWorker.RUNNING_WORKERS:
            if worker_id in FuncNodesExternalWorker.RUNNING_WORKERS[class_id]:
                return FuncNodesExternalWorker.RUNNING_WORKERS[class_id][worker_id]
        return None


class SaveLoop(CustomLoop):
    def __init__(self, client: Worker, delay=5) -> None:
        super().__init__(delay)
        self._client: Worker = client
        self.save_requested = False

    def request_save(self):
        self.save_requested = True

    async def loop(self):
        self._client._write_process_file()
        # self._client.write_config()
        if self.save_requested:
            self._client.save()
        self.save_requested = False


def requests_save(func):
    if asyncio.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(self: Worker, *args, **kwargs):
            res = func(self, *args, **kwargs)
            self.request_save()
            return res

        return async_wrapper
    else:

        @wraps(func)
        def wrapper(self: Worker, *args, **kwargs):
            res = func(self, *args, **kwargs)
            self.request_save()
            return res

        return wrapper


class HeartbeatLoop(CustomLoop):
    def __init__(self, client: Worker, required_heatbeat=None, delay=5) -> None:
        if required_heatbeat is not None:
            required_heatbeat = float(required_heatbeat)
            delay = min(delay, required_heatbeat / 10)

        super().__init__(delay)
        self._client: Worker = client
        self.required_heatbeat = required_heatbeat
        self._last_heartbeat = time.time()

    def heartbeat(self):
        self._last_heartbeat = time.time()

    async def loop(self):
        if self.required_heatbeat is not None:
            if time.time() - self._last_heartbeat > self.required_heatbeat:
                asyncio.create_task(self._client.stop_worker())


class ExternalWorkerSerClass(TypedDict):
    module: str
    class_name: str
    name: str
    _classref: Type[FuncNodesExternalWorker]
    instances: WeakSet[FuncNodesExternalWorker]


class WorkerDict(TypedDict):
    module: str
    worker_classes: List[ExternalWorkerSerClass]


class BasePackageDependency(TypedDict):
    package: str


class PipPackageDependency(BasePackageDependency):
    version: Optional[str]


class LocalPackageDependency(BasePackageDependency):
    path: str


PackageDependency = Union[PipPackageDependency, LocalPackageDependency]


def module_to_worker(mod) -> List[Type[FuncNodesExternalWorker]]:
    """
    Parses a single module for FuncNodesExternalWorker.
    """  #

    fn.FUNCNODES_LOGGER.debug(f"parsing module {mod}")
    classes: List[Type[FuncNodesExternalWorker]] = []
    for sn in ["FUNCNODES_WORKER_CLASSES"]:  # typo in the original code
        if hasattr(mod, sn):
            worker_classes = getattr(mod, sn)
            if isinstance(worker_classes, (list, tuple)):
                for worker_class in worker_classes:
                    if issubclass(worker_class, FuncNodesExternalWorker):
                        classes.append(worker_class)
            elif issubclass(worker_classes, FuncNodesExternalWorker):
                classes.append(worker_classes)

            else:
                raise ValueError(
                    f"FUNCNODES_WORKER_CLASSES in {mod} "
                    "is not a list of FuncNodesExternalWorker classes "
                    "or a FuncNodesExternalWorker class"
                )

    return classes


class PossibleUpdates(TypedDict, total=False):
    funcnodes: bool


class WorkerJson(TypedDict):
    type: str
    uuid: str
    name: str | None
    data_path: Optional[str]
    env_path: Optional[str]
    pid: Optional[int]

    # shelves_dependencies: Dict[str, ShelfDict]
    worker_dependencies: Dict[str, WorkerDict]
    package_dependencies: Dict[str, PackageDependency]
    update_on_startup: PossibleUpdates


def worker_json_get_data_path(workerJson: WorkerJson) -> Path:
    dp = workerJson.get("data_path")
    if dp is None:
        return get_worker_dir(workerJson["uuid"])
    else:
        return Path(dp)


runstateLiteral = Literal["undefined", "starting", "running", "stopping", "stopped"]
runsStateDetails = Tuple[runstateLiteral, str]
runsstatePackage = Union[runstateLiteral, runsStateDetails]


class Worker(ABC):
    class UnknownCmdException(ValueError):
        """Unknown command exception for Commands to the Worker."""

    def __init__(
        self,
        data_path: str | None = None,
        default_nodes: List[Shelf] | None = None,
        nodespace_delay=0.005,
        local_worker_lookup_delay=5,
        save_delay=5,
        required_heatbeat=None,
        uuid: str | None = None,
        name: str | None = None,
        debug: bool = False,
        **kwargs,  # catch all additional arguments for future compatibility
    ) -> None:
        if default_nodes is None:
            default_nodes = []

        self._debug = debug
        self._runstate: runstateLiteral = "undefined"
        self._package_dependencies: Dict[str, PackageDependency] = {}
        # self._shelves_dependencies: Dict[str, ShelfDict] = {}
        self._worker_dependencies: Dict[str, WorkerDict] = {}
        self.loop_manager = LoopManager(self)
        self.nodespace = NodeSpace()

        self.nodespace_loop = NodeSpaceLoop(self.nodespace, delay=nodespace_delay)
        self.loop_manager.add_loop(self.nodespace_loop)

        self.local_worker_lookup_loop = LocalWorkerLookupLoop(
            client=self,
            delay=local_worker_lookup_delay,
        )
        self.loop_manager.add_loop(self.local_worker_lookup_loop)

        self.saveloop = SaveLoop(self, delay=save_delay)
        self.loop_manager.add_loop(self.saveloop)

        self.heartbeatloop = HeartbeatLoop(self, required_heatbeat=required_heatbeat)
        self.loop_manager.add_loop(self.heartbeatloop)

        self.nodespace.on("*", self._on_nodespaceevent)
        self.nodespace.lib.on("*", self._on_libevent)
        self.nodespace.on_error(self._on_nodespaceerror)

        for shelf in default_nodes:
            self.nodespace.lib.add_shelf(shelf)

        self._nodespace_id: str = uuid4().hex
        self.viewdata: ViewState = {
            "renderoptions": fn.config.FUNCNODES_RENDER_OPTIONS,
        }
        self._uuid = (
            (slugify(name)[:16] + "_" + uuid4().hex[:16])
            if name
            else uuid4().hex
            if not uuid
            else uuid
        )
        self._name = name or None
        self._WORKERS_DIR: Path = get_workers_dir()
        self._WORKER_DIR: Path = get_worker_dir(self.uuid())

        self._data_path: Path = Path(data_path if data_path else self._WORKER_DIR)
        self.data_path = self._data_path
        self.logger = fn.get_logger(self.uuid(), propagate=False)
        if debug:
            self.logger.setLevel("DEBUG")
        self.logger.info("Init Worker %s", self.__class__.__name__)

        # self.logger.addHandler(
        #     RotatingFileHandler(
        #         self.data_path / "worker.log",
        #         maxBytes=100000,
        #         backupCount=5,
        #     )
        # )

        self._exposed_methods = get_exposed_methods(self)
        self._progress_state: ProgressState = {
            "message": "",
            "status": "",
            "progress": 0,
            "blocking": False,
        }
        self._save_disabled = False

    @property
    def venvmanager(self):
        envpath = self.config["env_path"]
        if envpath and USE_VENV:
            try:
                return venvmngr.UVVenvManager.get_virtual_env(envpath)
            except Exception:
                return None
        return None

    @property
    def _process_file(self) -> Path:
        return self._WORKERS_DIR / f"worker_{self.uuid()}.p"

    @property
    def _runstate_file(self) -> Path:
        return self._WORKERS_DIR / f"worker_{self.uuid()}.runstate"

    @property
    def _config_file(self) -> Path:
        return self._WORKERS_DIR / f"worker_{self.uuid()}.json"

    def _check_process_file(self, hard: bool = False):
        pf = self._process_file
        if pf.exists():
            if hard:
                raise RuntimeError("Worker already running")
            with open(pf, "r") as f:
                d = f.read()
            if d != "":
                try:
                    cmd = json.loads(d)
                    if not isinstance(
                        cmd, int
                    ):  # highly probable that data is an int (pid)
                        self.loop_manager.async_call(self.run_cmd(cmd))
                    else:
                        if psutil.pid_exists(cmd) and cmd != os.getpid():
                            if self._runstate != "stopped":
                                self.stop(save=False)
                            raise RuntimeError("Worker already running")
                except RuntimeError as e:
                    raise e
                except Exception:
                    pass

    def _write_process_file(self):
        pf = self._process_file
        if not pf.parent.exists():
            pf.parent.mkdir(parents=True, exist_ok=True)  # pragma: no cover
        self._check_process_file()
        with open(pf, "w+") as f:
            f.write(str(os.getpid()))

    # region config

    @property
    def config(self) -> WorkerJson:
        return self.load_or_generate_config()

    def exportable_config(self) -> dict:
        """creates a copy of the config without the process specific data"""
        exportable = dict(**self.config)
        exportable.pop("pid", None)
        exportable.pop("python_path", None)
        exportable.pop("env_path", None)
        exportable.pop("data_path", None)
        exportable.pop("uuid", None)
        return exportable

    def load_config(self) -> WorkerJson | None:
        """loads the config from the config file"""
        self.logger.debug("Loading config")
        cfile = self._config_file
        oldc = None
        if cfile.exists():
            with open(
                cfile,
                "r",
                encoding="utf-8",
            ) as f:
                oldc = json.load(f)
        if oldc:
            if "name" in oldc:
                self._name = oldc["name"]
        return oldc

    def load_or_generate_config(self) -> WorkerJson:
        """loads the config from the config file or generates a new one if it does not exist"""
        c = self.load_config()
        if c is None:
            c = self.generate_config()
        return c

    def generate_config(self) -> WorkerJson:
        """generates a new config"""
        self.logger.debug("Generate config")
        uuid = self.uuid()
        name = self.name()
        data_path = self.data_path
        env_path = None

        worker_dependencies: Dict[str, WorkerDict] = {}
        return self.update_config(
            WorkerJson(
                type=self.__class__.__name__,
                uuid=uuid,
                name=name,
                data_path=str(data_path),
                env_path=env_path,
                # shelves_dependencies=self._shelves_dependencies.copy(),
                worker_dependencies=worker_dependencies,
                package_dependencies=self._package_dependencies.copy(),
                pid=os.getpid(),
                update_on_startup={},
            )
        )

    def update_config(self, conf: WorkerJson) -> WorkerJson:
        """Updates a configuration dictionary for the Worker."""
        self.logger.debug("Updating config")
        conf["uuid"] = self.uuid()
        conf["name"] = self.name()
        conf["data_path"] = self.data_path
        conf["pid"] = os.getpid()

        if "update_on_startup" not in conf:
            conf["update_on_startup"] = {}  # pragma: no cover

        if "funcnodes" not in conf["update_on_startup"]:
            conf["update_on_startup"]["funcnodes"] = True  # pragma: no cover
        if "funcnodes-core" not in conf["update_on_startup"]:
            conf["update_on_startup"]["funcnodes-core"] = True  # pragma: no cover
        if "funcnodes-worker" not in conf["update_on_startup"]:
            conf["update_on_startup"]["funcnodes-worker"] = True  # pragma: no cover

        # conf["shelves_dependencies"] = self._shelves_dependencies.copy()
        conf["package_dependencies"] = self._package_dependencies.copy()

        worker_dependencies = conf.get("worker_dependencies", {})
        if isinstance(worker_dependencies, list):  # pragma: no cover
            warnings.warn(
                "worker_dependencies should be a dict, not a list",
                DeprecationWarning,
            )
            worker_dependencies = {
                w["module"]: w for w in cast(List[WorkerDict], worker_dependencies)
            }

        def w_in_without_classes(w: WorkerDict):
            cs = w.copy()
            cs["worker_classes"] = []
            csj = json.dumps(cs, sort_keys=True, cls=JSONEncoder)
            for w2 in worker_dependencies.values():
                w2 = w2.copy()
                w2["worker_classes"] = []
                if csj == json.dumps(w2, sort_keys=True, cls=JSONEncoder):
                    return True
            return False

        for k, v in self._worker_dependencies.items():
            if not w_in_without_classes(v):  # pragma: no cover
                worker_dependencies[k] = v
        conf["worker_dependencies"] = worker_dependencies

        return conf

    def write_config(self, opt_conf: Optional[WorkerJson] = None) -> WorkerJson:
        """
        Writes the configuration to the config file.
        If opt_conf is not None, it will write the opt_conf to the config file
        otherwise it will write the current config to the config file.
        """
        self.logger.debug("Write config")
        if opt_conf is None:
            c = self.update_config(self.config)
        else:
            c = opt_conf
        c["uuid"] = self.uuid()
        c["pid"] = os.getpid()

        # if the data_path is the default data_path, set it to None
        if c["data_path"] == self._WORKER_DIR:
            c["data_path"] = None
        cfile = self._config_file
        if not cfile.parent.exists():
            cfile.parent.mkdir(parents=True, exist_ok=True)

        write_json_secure(data=c, filepath=cfile, cls=JSONEncoder, indent=2)
        return c

    async def ini_config(self):
        """initializes the worker from the config file"""
        self.logger.debug("Init config")
        try:
            self._check_process_file(hard=True)
        except Exception:
            self.logger.debug("Found process file, wait and try again")
            await asyncio.sleep(
                1
            )  # wait for at least 1 second to make sure the process file is written
            self._check_process_file()

        self._write_process_file()
        c = self.load_or_generate_config()

        await self.update_from_config(dict(c))

    async def update_from_config(self, config: dict):
        """updates the worker from a config dict"""
        self.logger.debug("Update from config")
        await reload_base(with_repos=True)
        if "package_dependencies" in config:
            for name, dep in config["package_dependencies"].items():
                try:
                    await self.add_package_dependency(
                        name, dep, save=False, sync=False, do_reload_base=False
                    )
                except Exception as e:
                    self.logger.exception(e)

        # if "worker_dependencies" in c:
        #     for dep in list(c["worker_dependencies"]):
        #         try:
        #             self.add_worker_package(dep, save=False)
        #         except Exception as e:
        #             self.logger.exception(e)

        # TODO: remove in future version
        def _shelves_dependencies_to_package(shelfdep: ShelfDict) -> PackageDependency:
            d = BasePackageDependency(
                package=(
                    shelfdep["package"]
                    if "package" in shelfdep
                    else shelfdep["module"].replace("_", "-")
                )
            )

            if "version" in shelfdep:
                d = PipPackageDependency(
                    package=d["package"], version=shelfdep["version"]
                )

            if "path" in shelfdep:
                d = LocalPackageDependency(package=d["package"], path=shelfdep["path"])
            else:
                d = PipPackageDependency(
                    package=d["package"], version=shelfdep.get("version")
                )

            return d

        if "shelves_dependencies" in config:
            if isinstance(config["shelves_dependencies"], dict):
                for k, v in config["shelves_dependencies"].items():
                    try:
                        pkg = _shelves_dependencies_to_package(v)
                        await self.add_package_dependency(
                            pkg["package"], pkg, save=False, sync=False
                        )
                    except Exception as e:
                        self.logger.exception(e)
            elif isinstance(config["shelves_dependencies"], list):
                for dep in config["shelves_dependencies"]:
                    try:
                        pkg = _shelves_dependencies_to_package(dep)
                        await self.add_package_dependency(
                            pkg["package"], pkg, save=False, sync=False
                        )
                    except Exception as e:
                        self.logger.exception(e)
        await self.worker_event("fullsync")
        await asyncio.sleep(1)

    @exposed_method()
    def export_worker(self, with_files: bool = True) -> bytes:
        """packs all the required data for the worker to be exported into a custom zip file format"""

        self.save()

        zip_buffer = io.BytesIO()
        config = self.exportable_config()

        with zipfile.ZipFile(zip_buffer, "w") as zip_file:
            zip_file.writestr(
                "config", json.dumps(config, cls=JSONEncoder, indent=2).encode("utf-8")
            )
            zip_file.writestr(
                "state",
                json.dumps(
                    self.get_save_state(export=True), cls=JSONEncoder, indent=2
                ).encode("utf-8"),
            )
            if self.venvmanager:
                tomlpath = self.data_path / "pyproject.toml"
                if tomlpath.exists():
                    zip_file.write(tomlpath, "pyproject.toml")

            if with_files:
                # add all files in the files directory
                basefiles = Path("files")
                for file in self.files_path.rglob("*"):
                    if file.is_file():
                        relative_path = file.relative_to(self.files_path)
                        zip_file.write(file, basefiles / relative_path)

        zip_bytes = zip_buffer.getvalue()
        zip_buffer.close()

        return zip_bytes

    @exposed_method()
    async def update(
        self,
        config: Union[str, dict, None] = None,
        state: Union[str, dict, None] = None,
    ):
        """updates the worker from a config and state dict"""
        if isinstance(config, str):
            dictconfig = json.loads(config)
        else:
            dictconfig = config
        if not isinstance(dictconfig, dict):
            raise ValueError("config must be a dict or a json string")

        if isinstance(state, str):
            dictstate = json.loads(state)
        else:
            dictstate = state
        if not isinstance(dictstate, dict):
            raise ValueError("state must be a dict or a json string")

        if config is not None:
            await self.update_from_config(dictconfig)
        if state is not None:
            await self.load_data(dictstate)

    @exposed_method()
    async def update_from_export(self, data: Union[str, bytes]):
        """updates the worker from an exported zip file"""
        if isinstance(data, str):
            # data is base64 encoded zip data
            data = base64.b64decode(data)

        with zipfile.ZipFile(io.BytesIO(data), "r") as zip_file:
            config = json.loads(zip_file.read("config").decode("utf-8"))
            state = json.loads(zip_file.read("state").decode("utf-8"))
            if "pyproject.toml" in zip_file.namelist() and self.venvmanager:
                with zip_file.open("pyproject.toml") as f:
                    tomllines = f.readlines()

                toml = b""
                for line in list(tomllines):
                    if b"requires-python =" in line:
                        # if the toml was created with a higher python version,
                        # we need to update the requires-python to the minimum
                        line = b'requires-python = ">=3.11"'
                    toml += line + b"\n"

                with open(self.data_path / "pyproject.toml", "wb") as f:
                    f.write(toml)

            # extract files
            for file in zip_file.namelist():
                if file.startswith("files/"):
                    zip_file.extract(file, self.data_path)

        await self.update(config=config, state=state)

    # endregion config
    # region properties
    @property
    def data_path(self) -> Path:
        return self._data_path.absolute()

    @data_path.setter
    def data_path(self, data_path: Path):
        data_path = data_path.resolve()
        if not data_path.exists():
            data_path.mkdir(parents=True, exist_ok=True)
        self._data_path = data_path

    @property
    def files_path(self) -> Path:
        fp = self.data_path / "files"
        if not fp.exists():
            fp.mkdir(parents=True, exist_ok=True)

        return fp

    @property
    def local_nodespace(self) -> Path:
        return self.data_path / "nodespace.json"

    @property
    def local_scripts(self) -> Path:
        return self.data_path / "local_scripts"

    @property
    def nodespace_id(self) -> str:
        return self._nodespace_id

    # endregion properties

    # region local worker
    def add_local_worker(
        self,
        worker_class: Type[FuncNodesExternalWorker],
        nid: str,
        name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        w = self.local_worker_lookup_loop.start_local_worker(
            worker_class, nid, name=name, config=config
        )
        self.loop_manager.async_call(self.worker_event("external_worker_update"))
        return w

    @exposed_method()
    def add_external_worker(self, module: str, cls_module: str, cls_name: str):
        if module in self._worker_dependencies:
            wdep = self._worker_dependencies[module]
            for wcls in wdep["worker_classes"]:
                if wcls["class_name"] == cls_name and wcls["module"] == cls_module:
                    return self.add_local_worker(wcls["_classref"], uuid4().hex)

        raise ValueError(f"Worker {cls_name}({cls_module}) not found in {module}")

    @exposed_method()
    def get_worker_dependencies(self) -> List[WorkerDict]:
        for k, v in self._worker_dependencies.items():
            for cls in v["worker_classes"]:
                cls["instances"] = WeakSet(cls["_classref"].running_instances())

        return list(self._worker_dependencies.values())

    @exposed_method()
    def update_external_worker(
        self,
        worker_id: str,
        class_id: str,
        name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        worker_instance = FuncNodesExternalWorker.RUNNING_WORKERS.get(class_id, {}).get(
            worker_id
        )
        if worker_instance is None:
            raise ValueError(f"Worker {worker_id} not found")
        if name is not None:
            worker_instance.name = name

        if config is not None:
            worker_instance.update_config(config)
            # Note: _update_worker_shelf will be called automatically via the
            # "nodes_update" event handler registered in start_local_worker,
            # so we don't need to call it directly here.
        self.loop_manager.async_call(self.worker_event("external_worker_update"))

    @exposed_method()
    async def remove_external_worker(self, worker_id: str, class_id: str):
        res = await self.local_worker_lookup_loop.stop_local_worker_by_id(
            class_id, worker_id
        )

        return res

    @exposed_method()
    async def get_external_worker_config(
        self, worker_id: str, class_id: str
    ) -> Dict[str, Dict[str, Any]]:
        worker_instance = await self.local_worker_lookup_loop.get_local_worker_by_id(
            class_id, worker_id
        )
        if worker_instance is None:
            raise ValueError(f"Worker {worker_id} ({class_id}) not found")
        return {
            "jsonSchema": worker_instance.config.model_json_schema(),
            "uiSchema": None,
            "formData": worker_instance.config.model_dump(mode="json"),
        }

    # endregion local worker
    # region states
    @exposed_method()
    def uuid(self) -> str:
        """returns the uuid of the worker"""
        return self._uuid

    def slug_name(self) -> str:
        return slugify(self.name())

    @exposed_method()
    def name(self) -> str:
        """returns the name of the worker or the uuid if no name is set"""
        return self._name or self._uuid

    @exposed_method()
    def view_state(self) -> ViewState:
        """returns the view state of the worker"""

        self.viewdata["renderoptions"] = fn.config.FUNCNODES_RENDER_OPTIONS
        viewdata = self.viewdata.copy()

        available_nodeids = []
        viewdata["nodes"] = {}
        for node in self.nodespace.nodes:
            available_nodeids.append(node.uuid)
            viewdata["nodes"][node.uuid] = NodeViewState(
                pos=node.get_property("frontend:pos", (0, 0)),
                size=node.get_property("frontend:size", (200, 250)),
            )

        return viewdata

    @exposed_method()
    def heartbeat(self):
        self.heartbeatloop.heartbeat()

    @exposed_method()
    def get_meta(self) -> MetaInfo:
        return {
            "id": self.nodespace_id,
            "version": fn.__version__,
        }

    @exposed_method()
    def upload(self, data: Union[bytes, str], filename: Path) -> Path:
        full_path = (self.files_path / filename).resolve()

        # make sure full_path is a subpath of files_path
        if not full_path.is_relative_to(self.files_path):
            raise ValueError("filename must be a relative subpath of the files_path")

        # if data is a string, decode it
        if isinstance(data, str):
            data = base64.b64decode(data)

        # Ensure the directory exists
        full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(full_path, "wb") as f:
            f.write(data)
        self.nodespace.set_secret_property("files_dir", self.files_path.as_posix())

        return filename

    @exposed_method()
    def get_save_state(self, export: bool = False) -> WorkerState:
        ws = self.view_state()
        ws.pop("nodes", None)
        data: WorkerState = {
            "backend": saving.serialize_nodespace_for_saving(self.nodespace),
            "view": ws,
            "meta": self.get_meta(),
            "external_workers": {
                workerclass.NODECLASSID: [
                    w_instance.serialize(export=export)
                    for w_instance in workerclass.running_instances()
                ]
                for workerclass in self.local_worker_lookup_loop.worker_classes
            },
        }
        return data

    @exposed_method()
    def full_state(self) -> FullState:
        data = FullState(
            backend=self.nodespace,
            view=self.view_state(),
            worker={
                w.NODECLASSID: [i.uuid for i in w.running_instances()]
                for w in self.local_worker_lookup_loop.worker_classes
            },
            worker_dependencies=self.get_worker_dependencies(),
            progress_state=self._progress_state,
            meta=self.get_meta(),
        )

        return data

    @exposed_method()
    def get_library(self) -> FullLibJSON:
        return self.nodespace.lib.full_serialize()

    @exposed_method()
    def get_nodes(self, with_frontend: bool = False) -> List[ExtendedFullNodeJSON]:
        nodes_viewdata = self.viewdata.get("nodes", {})
        for node in self.nodespace.nodes:
            if node.uuid in nodes_viewdata:
                self.update_node_view(node, nodes_viewdata[node.uuid])

        nodes = [
            ExtendedFullNodeJSON(**nodedata, frontend=None)
            for nodedata in self.nodespace.full_nodes_serialize()
        ]

        if with_frontend:
            # this will be deprecated in the future
            for node in nodes:
                node["frontend"] = NodeViewState(
                    pos=node.get("properties", {}).get("frontend:pos", (0, 0)),
                    size=node.get("properties", {}).get("frontend:size", (200, 250)),
                )

        return nodes

    @exposed_method()
    def get_edges(self) -> List[Tuple[str, str, str, str]]:
        return self.nodespace.serialize_edges()

    @exposed_method()
    async def stop_worker(self):
        self.logger.info("Stopping worker")
        await self.set_progress_state(
            message="Stopping worker", status="info", progress=0.0, blocking=False
        )

        self.stop()
        await self.set_progress_state(
            message="Stopping worker", status="info", progress=1, blocking=False
        )
        return True

    @exposed_method()
    async def get_plugin_keys(self, type: FrontEndKeys) -> List[str]:
        await self._check_frontend(type, install_missing=True)
        if type == "react":
            _, module = FUNCNODES_REACT()
            return list(module.FUNCNODES_REACT_PLUGIN.keys())

        raise ValueError(f"Plugin type {type} not found")

    @exposed_method()
    async def get_plugin(self, key: str, type: FrontEndKeys) -> Any:
        await self._check_frontend(type, install_missing=True)
        if type == "react":
            _, module = FUNCNODES_REACT()
            return module.get_react_plugin_content(key)

        raise ValueError(f"Plugin type {type} not found")

    async def _check_frontend(
        self, fontendkey: FrontEndKeys, install_missing: bool = True
    ) -> bool:
        if fontendkey == "react":
            if not FUNCNODES_REACT()[0]:
                if install_missing:
                    await install_package(
                        "funcnodes-react-flow",
                        version=None,
                        upgrade=True,
                        env_manager=self.venvmanager,
                        logger=self.logger,
                    )
                else:
                    raise ImportError("funcnodes-react-flow is not installed")
        else:
            raise ValueError(f"Frontend {fontendkey} not found")

    # endregion states

    # region save and load
    def request_save(self):
        self.saveloop.request_save()

    @exposed_method()
    def save(self):
        if self._save_disabled:
            return
        self.logger.debug("Saving worker")
        data: WorkerState = self.get_save_state()
        write_json_secure(data, self.local_nodespace, cls=JSONEncoder)
        self.write_config()
        return data

    @exposed_method()
    async def load_data(self, data: WorkerState):
        return await self.load(data)

    async def load(self, data: WorkerState | str | None = None):
        self.clear()
        self.logger.debug("Loading worker")
        self.nodespace_loop.pause()
        try:
            if data is None:
                if not self.local_nodespace.exists():
                    return
                try:
                    with open(self.local_nodespace, "r", encoding="utf-8") as f:
                        worker_data: WorkerState = json.loads(f.read(), cls=JSONDecoder)
                except json.JSONDecodeError as e:
                    self.logger.error(f"Error loading worker data: {e}")
                    worker_data = self.get_save_state()

            elif isinstance(data, str):
                worker_data: WorkerState = json.loads(data, cls=JSONDecoder)

            elif isinstance(data, dict):
                worker_data = cast(WorkerState, data)
            else:
                raise ValueError("data must be a dict or a json string or None")

            if "backend" not in worker_data:
                worker_data["backend"] = NodeSpaceJSON(nodes=[], edges=[], prop={})
            if "view" not in worker_data:
                worker_data["view"] = ViewState(renderoptions={})

            if "external_workers" in worker_data:
                for worker_id, worker_uuid in worker_data["external_workers"].items():
                    found = False
                    for worker in self.local_worker_lookup_loop.worker_classes:
                        if worker.NODECLASSID == worker_id:
                            for instance in worker_uuid:
                                if isinstance(instance, str):
                                    self.add_local_worker(worker, instance)
                                else:
                                    self.add_local_worker(
                                        worker,
                                        nid=instance["uuid"],
                                        name=instance.get("name", None),
                                        config=instance.get("config", None),
                                    )
                                found = True
                    if not found:
                        self.logger.warning(f"External worker {worker_id} not found")

            if "nodes" in worker_data["backend"]:
                nodes = worker_data["backend"]["nodes"]
                for node in nodes:
                    try:
                        await self.install_node(node)
                    except NodeClassNotFoundError:
                        pass

            if "meta" in worker_data:
                if "id" in worker_data["meta"]:
                    self._set_nodespace_id(worker_data["meta"]["id"])
            self.viewdata = worker_data["view"]
            self.nodespace.deserialize(worker_data["backend"])
            self.nodespace.remove_property("files_dir")
            self.nodespace.set_secret_property("files_dir", self.files_path.as_posix())
            nodesview = self.viewdata.get("nodes", {})
            for node in self.nodespace.nodes:
                if node.uuid in nodesview:
                    self.update_node_view(node, nodesview[node.uuid])

            await self.worker_event("fullsync")
            return self.request_save()
        finally:
            self.nodespace_loop.resume_in(2)

    # endregion save and load

    # region events

    async def worker_event(self, event: str, **kwargs):
        await self.send(
            worker_event_message(
                event=event,
                data=kwargs,
            )
        )

    async def send(self, data, **kwargs):
        """send data to the any receiver, in base class it is a no-op"""
        pass

    def _on_nodespaceevent(self, event, **kwargs):
        """handle nodespace events"""
        getattr(self, f"on_nodespaceevent_{event}", self.on_nodespaceevent)(
            event, **kwargs
        )

    @abstractmethod
    def on_nodespaceevent(self, event, **kwargs):
        """handle nodespace events"""

    def _on_libevent(self, event, **kwargs):
        """handle lib events"""
        self.loop_manager.async_call(self.worker_event("lib_update"))

    @abstractmethod
    def _on_nodespaceerror(
        self,
        error: Exception,
        src: NodeSpace,
    ):
        """handle nodespace errors"""

    # endregion events

    # region nodespace interaction

    # region library

    # def add_shelves_dependency(self, src: ShelfDict):
    #     self._shelves_dependencies[src["module"]] = src

    # def remove_shelves_dependency(self, src: ShelfDict):
    #     if src["module"] in self._shelves_dependencies:
    #         del self._shelves_dependencies[src["module"]]

    async def set_progress_state(
        self, message: str, status: str, progress: float, blocking: bool
    ):
        self._progress_state = {
            "message": message,
            "status": status,
            "progress": progress,
            "blocking": blocking,
        }

    @exposed_method()
    async def add_shelf(self, src: Union[str, ShelfDict], save: bool = True):
        await self.set_progress_state(
            message="Adding shelf", status="info", progress=0.0, blocking=True
        )
        warnings.warn(
            "add_shelf is deprecated",
            DeprecationWarning,
        )
        self.logger.info(f"Adding shelf {src}")
        try:
            shelfdata = find_shelf(src=src)
            if shelfdata is None:
                raise ValueError(f"Shelf in {src} not found")
            shelf, shelfdata = shelfdata
            if shelf is None:
                raise ValueError(f"Shelf in {src} not found")
            self.nodespace.add_shelf(shelf)
            if save:
                self.request_save()
            await self.set_progress_state(
                message="Shelf added", status="success", progress=1, blocking=False
            )
        finally:
            pass
        return True

    @deprecated(
        "Use add_shelf instead",
    )
    async def add_shelf_by_module(self, module: str):
        return await self.add_shelf(module)

    @exposed_method()
    async def add_package_dependency(
        self,
        name: str,
        dep: Optional[PackageDependency] = None,
        save: bool = True,
        version: Optional[str] = None,
        sync: bool = True,
        do_reload_base: bool = True,
    ):
        if version == "latest" or not version:
            version_spec = None
            str_v_spec = None
        else:
            version_spec = version_string_to_Specifier(version)
            str_v_spec = str(version_spec)

        if dep and "path" in dep:
            raise NotImplementedError("Local package dependencies not implemented")
        await self.set_progress_state(
            message=f"Add package dependency {name}",
            status="info",
            progress=0.0,
            blocking=True,
        )
        if do_reload_base:
            await reload_base(with_repos=False)

        try:
            if name not in AVAILABLE_REPOS:
                try_import_module(name)
            if name not in AVAILABLE_REPOS:
                raise ValueError(
                    f"Package {name} not found, available: {list(AVAILABLE_REPOS.keys())}"
                )

            repo = AVAILABLE_REPOS[name]
            if not repo:
                raise ValueError(f"Package {name} not found")

            if repo.version and repo.version not in repo.releases:
                repo.releases.append(repo.version)

            if version_spec:
                if (
                    version_spec.version not in repo.releases
                    and version_spec.version != repo.version
                ):
                    raise ValueError(
                        f"Version {version_spec.version} not found in {name}, available: {repo.releases}"
                    )

            if dep is None:
                dep = PipPackageDependency(
                    package=repo.package_name,
                    version=str_v_spec,
                )
            if version_spec:
                dep["version"] = str_v_spec

            if not repo.installed:
                await self.set_progress_state(
                    message=f"Install dependency {name}({dep.get('version', None)})",
                    status="info",
                    progress=0.40,
                    blocking=True,
                )

                repo = await install_repo(
                    name,
                    version=dep.get("version", None),
                    env_manager=self.venvmanager,
                    logger=self.logger,
                )
                if not repo:
                    raise ValueError(
                        f"Package {name} could not be installed with version {dep.get('version', None)}"
                    )
            elif version_spec:
                if not repo.version or not version_spec.contains(repo.version):
                    await self.set_progress_state(
                        message=f"Upgrade dependency {name}",
                        status="info",
                        progress=0.40,
                        blocking=True,
                    )
                    repo = await install_repo(
                        name,
                        version=dep.get("version", None),
                        upgrade=True,
                        env_manager=self.venvmanager,
                    )
                    if not repo:
                        raise ValueError(
                            f"Package {name} could not be updated with version {dep.get('version', None)}"
                        )

            if not repo:
                raise ValueError(
                    f"Package {name}({dep.get('version', None)}) could not be added"
                )

            module = repo.moduledata

            if module is None:
                raise ValueError(f"Module {name} not found")

            await self.set_progress_state(
                message=f"Adding dependency {name}",
                status="info",
                progress=0.80,
                blocking=True,
            )

            shelf = module.entry_points.get("shelf")
            if shelf:
                self.nodespace.add_shelf(shelf)

            external_worker = module.entry_points.get("external_worker")

            if external_worker:
                if not isinstance(external_worker, (list, tuple)):
                    external_worker = [external_worker]

                self.add_worker_dependency(
                    WorkerDict(
                        module=module.name,
                        worker_classes=[
                            ExternalWorkerSerClass(
                                module=worker_class.__module__,
                                class_name=worker_class.__name__,
                                name=getattr(
                                    worker_class, "NAME", worker_class.__name__
                                ),
                                _classref=worker_class,
                                instances=WeakSet(worker_class.running_instances()),
                            )
                            for worker_class in external_worker
                            if issubclass(worker_class, FuncNodesExternalWorker)
                        ],
                    )
                )
            self._package_dependencies[name] = PipPackageDependency(
                package=repo.package_name,
                version=dep.get("version", None),
            )

            if save:
                self.request_save()
            await self.set_progress_state(
                message=f"Package dependency added {name}",
                status="success",
                progress=1,
                blocking=False,
            )
        except Exception as exc:
            await self.set_progress_state(
                message=f"Could not install {name}({version}): {exc}",
                status="error",
                progress=0.0,
                blocking=True,
            )
            raise exc
        finally:
            if sync:
                await self.worker_event("fullsync")

    @exposed_method()
    async def remove_package_dependency(
        self, name: str, dep: Optional[PackageDependency] = None, save: bool = True
    ):
        if dep and "path" in dep:
            raise NotImplementedError("Local package dependencies not implemented")

        if name not in AVAILABLE_REPOS:
            raise ValueError(f"Package {name} not found")

        repo = AVAILABLE_REPOS[name]
        if dep is None:
            dep = PipPackageDependency(
                package=repo.package_name,
                version=None,
            )
        if not repo:
            raise ValueError(f"Package {name} not found")

        module = repo.moduledata

        if module is None:
            raise ValueError(f"Package {name} not found")

        shelf = module.entry_points.get("shelf")
        if shelf:
            self.nodespace.remove_shelf(shelf)

        external_worker = module.entry_points.get("external_worker")

        if external_worker:
            if not isinstance(external_worker, (list, tuple)):
                external_worker = [external_worker]

            await self.remove_worker_dependency(
                WorkerDict(
                    module=module.name,
                    worker_classes=[
                        ExternalWorkerSerClass(
                            module=worker_class.__module__,
                            class_name=worker_class.__name__,
                            name=getattr(worker_class, "NAME", worker_class.__name__),
                            _classref=worker_class,
                            instances=WeakSet(worker_class.running_instances()),
                        )
                        for worker_class in external_worker
                        if issubclass(worker_class, FuncNodesExternalWorker)
                    ],
                )
            )

        if name in self._package_dependencies:
            del self._package_dependencies[name]

        if save:
            self.request_save()

    @exposed_method()
    def remove_shelf(self, src: Union[str, ShelfDict], save: bool = True):
        warnings.warn(
            "remove_shelf is deprecated",
            DeprecationWarning,
        )
        shelfdata = find_shelf(src=src)
        if shelfdata is None:
            return {"error": f"Shelf in {src} not found"}
        shelf, shelfdata = shelfdata
        if shelf is None:
            raise ValueError(f"Shelf in {src} not found")
        self.nodespace.remove_shelf(shelf)
        if save:
            self.request_save()

    def add_worker_dependency(self, src: WorkerDict):
        if src["module"] not in self._worker_dependencies:
            self._worker_dependencies[src["module"]] = src
            for worker_class in src["worker_classes"]:
                if (
                    worker_class["_classref"]
                    not in self.local_worker_lookup_loop.worker_classes
                ):
                    self.local_worker_lookup_loop.worker_classes.append(
                        worker_class["_classref"]
                    )

            self.loop_manager.async_call(
                self.worker_event(
                    event="update_worker_dependencies",
                    worker_dependencies=self.get_worker_dependencies(),
                )
            )

    async def remove_worker_dependency(self, src: WorkerDict):
        if src["module"] in self._worker_dependencies:
            del self._worker_dependencies[src["module"]]
            for worker_class in src["worker_classes"]:
                await self.local_worker_lookup_loop.stop_local_workers_by_id(
                    worker_class["_classref"].NODECLASSID
                )
                if (
                    worker_class["_classref"]
                    in self.local_worker_lookup_loop.worker_classes
                ):
                    self.local_worker_lookup_loop.worker_classes.remove(
                        worker_class["_classref"]
                    )

            self.loop_manager.async_call(
                self.worker_event(
                    event="update_worker_dependencies",
                    worker_dependencies=self.get_worker_dependencies(),
                )
            )
            self.loop_manager.async_call(self.worker_event("lib_update"))

    # @worexposed_method()
    # def add_worker_package(self, src: Union[str, WorkerDict], save=True):
    #     self.set_progress_state_sync(
    #         message="Adding worker", status="info", progress=0.0, blocking=True
    #     )
    #     try:
    #         worker_data = find_worker(src=src)
    #         if worker_data is None:
    #             return {"error": f"Worker in {src} not found"}
    #         worker, worker_data = worker_data

    #         if worker is None:
    #             raise ValueError(f"Worker in {src} not found")
    #         self.add_worker_dependency(worker_data)

    #         if save:
    #             self.request_save()
    #         self.set_progress_state_sync(
    #             message="Worker added", status="success", progress=1, blocking=False
    #         )
    #     finally:
    #         pass
    #     return True

    @exposed_method()
    async def get_available_modules(self):
        await reload_base()
        ans = {
            "installed": [],
            "active": [],
            "available": [],
        }
        for modname, moddata in AVAILABLE_REPOS.items():
            data = {
                "name": modname,
                "description": moddata.description or "No description available",
                "version": moddata.version or "latest",
                "homepage": moddata.homepage or "",
                "source": moddata.source or "",
                "releases": moddata.releases or [],
            }
            if moddata.moduledata:
                if moddata.moduledata.version:
                    data["version"] = moddata.moduledata.version

            if (
                # self._shelves_dependencies.get(modname.replace("-", "_")) is not None or
                self._worker_dependencies.get(modname.replace("-", "_")) is not None
                or self._package_dependencies.get(modname) is not None
            ):  # replace - with _ to avoid issues with module names
                ans["active"].append(data)
            else:
                if moddata.installed:
                    ans["installed"].append(data)
                else:
                    ans["available"].append(data)

        return ans

    # endregion library

    # region nodes
    @exposed_method()
    def clear(self):
        self.logger.debug("Clearing worker")
        self.nodespace.clear()
        self.nodespace.set_secret_property("files_dir", self.files_path.as_posix())

    @requests_save
    @exposed_method()
    def add_node(self, id: str, **kwargs: Dict[str, Any]):
        return self.nodespace.add_node_by_id(id, **kwargs)

    @exposed_method()
    def get_node(self, id: str) -> Node:
        return self.nodespace.get_node_by_id(id)

    @requests_save
    @exposed_method()
    def remove_node(self, id: str) -> Union[str, None]:
        return self.nodespace.remove_node_by_id(id)

    @exposed_method()
    def trigger_node(self, nid: str):
        node = self.get_node(nid)
        node.request_trigger()
        return True

    @exposed_method()
    def get_node_status(self, nid: str):
        node = self.get_node(nid)
        return node.status()

    @requests_save
    @exposed_method()
    def set_default_value(self, nid: str, ioid: str, value: Any):
        node = self.get_node(nid)
        io = node.get_input(ioid)
        io.set_default(value)
        return True

    @exposed_method()
    def get_node_state(self, nid: str) -> FullNodeJSON:
        node = self.get_node(nid)
        return node._repr_json_()

    @exposed_method()
    def request_trigger(self, nid: str):
        node = self.get_node(nid)
        node.request_trigger()
        return True

    @requests_save
    @exposed_method()
    def update_node(self, nid: str, data: NodeJSON):
        try:
            node = self.get_node(nid)
        except Exception:
            return {"error": f"Node with id {nid} not found"}
        if not node:
            raise ValueError(f"Node with id {nid} not found")
        ans = {}

        for k, v in data.get("properties", {}).items():
            node.set_property(k, v)

        if "name" in data:
            n = data["name"]
            node.name = n
            ans["name"] = node.name

        if "description" in data:
            d = data["description"]
            node.description = str(d)
            ans["description"] = node.description

        if "reset_inputs_on_trigger" in data:
            node.reset_inputs_on_trigger = data["reset_inputs_on_trigger"]
            ans["reset_inputs_on_trigger"] = node.reset_inputs_on_trigger

        return ans

    @requests_save
    @exposed_method()
    def update_io_options(
        self,
        nid: str,
        ioid: str,
        name: Optional[str] = None,
        hidden: Optional[bool] = None,
    ):
        node = self.get_node(nid)
        io = node.get_input_or_output(ioid)

        if name is not None:
            if len(name) == 0:
                name = io.uuid
            io.name = name

        if hidden is not None:
            if len(io.connections) > 0:
                hidden = False
            io.hidden = hidden
        return io

    @requests_save
    @exposed_method()
    def update_node_view(self, nid: str, data: NodeViewState):
        node = (
            self.get_node(nid)
            if not isinstance(nid, Node)  # for internal use
            else nid
        )  # for internal use

        if "pos" in data and data["pos"]:
            node.set_property("frontend:pos", data["pos"])
        if "size" in data and data["size"]:
            node.set_property("frontend:size", data["size"])

    @exposed_method()
    def update_io_value_options(self, nid: str, ioid: str, options: ValueOptions):
        node = self.get_node(nid)
        io = node.get_input_or_output(ioid)
        io.update_value_options(**options)
        return io

    @exposed_method()
    def set_io_value(self, nid: str, ioid: str, value: Any, set_default: bool = False):
        node = self.get_node(nid)
        io = node.get_input(ioid)
        if set_default:  # novalue should not be set automatically as default via io set
            io.set_default(value)
        io.set_value(value)
        return io.value

    @exposed_method()
    def get_io_value(self, nid: str, ioid: str):
        node = self.get_node(nid)
        io = node.get_input_or_output(ioid)
        return JSONEncoder.apply_custom_encoding(io.value, preview=True)

    @exposed_method()
    def get_ios_values(self, nid: str) -> Dict[str, Any]:
        node = self.get_node(nid)
        return {
            **{
                ioid: JSONEncoder.apply_custom_encoding(io.value, preview=True)
                for ioid, io in node.inputs.items()
            },
            **{
                ioid: JSONEncoder.apply_custom_encoding(io.value, preview=True)
                for ioid, io in node.outputs.items()
            },
        }

    @exposed_method()
    def get_io_full_value(self, nid: str, ioid: str):
        node = self.get_node(nid)
        io = node.get_input_or_output(ioid)
        return ByteEncoder.encode(io.value, preview=False)

    async def install_node(self, nodedata: NodeJSON):
        nideid = nodedata["node_id"]
        if self.nodespace.lib.has_node_id(nideid):
            return
        await self.local_worker_lookup_loop.loop()

        for req in nodedata.get("requirements", []):
            if req["type"] == "nodeclass":
                _class = req["class"]
                _id = req["id"]
                for cls in self.local_worker_lookup_loop.worker_classes:
                    if cls.NODECLASSID == _class:
                        self.local_worker_lookup_loop.start_local_worker(cls, _id)

        if self.nodespace.lib.has_node_id(nideid):
            return

        raise NodeClassNotFoundError(f"Node with id {nideid} not found")

    # endregion nodes
    # region edges
    @requests_save
    @exposed_method()
    def add_edge(
        self,
        src_nid: str,
        src_ioid: str,
        trg_nid: str,
        trg_ioid: str,
        replace: bool = False,
    ):
        src = self.get_node(src_nid)
        tgt = self.get_node(trg_nid)
        srcio = src.get_input_or_output(src_ioid)
        tgtio = tgt.get_input_or_output(trg_ioid)
        return srcio.connect(tgtio, replace=replace)

    @requests_save
    @exposed_method()
    def remove_edge(
        self,
        src_nid: str,
        src_ioid: str,
        trg_nid: str,
        trg_ioid: str,
    ):
        src = self.get_node(src_nid)
        tgt = self.get_node(trg_nid)
        srcio = src.get_input_or_output(src_ioid)
        tgtio = tgt.get_input_or_output(trg_ioid)

        srcio.disconnect(tgtio)
        return True

    # endregion edges
    # endregion nodespace interaction

    def _set_nodespace_id(self, nsid: str):
        if nsid is None:
            nsid = uuid4().hex

        if len(nsid) == 32:
            self._nodespace_id = nsid
        else:
            raise ValueError("nsid must be 32 characters long")

    def initialize_nodespace(self):
        try:
            self.loop_manager.async_call(self.load())
        except FileNotFoundError:  # pragma: no cover
            pass

    @property
    def runstate(self) -> runstateLiteral:
        return self._runstate

    @runstate.setter
    def runstate(self, value: runsstatePackage):
        details = None
        if isinstance(value, tuple):
            details = value[1]
            value = value[0]
        value = str(value).strip()
        pf = self._runstate_file
        if not pf.parent.exists():
            pf.parent.mkdir(parents=True, exist_ok=True)  # pragma: no cover
        self._runstate = value
        with open(pf, "w") as f:
            f.write(value)
            if details:
                f.write(f"\n{details}")

    @exposed_method()
    def get_runstate(self) -> runstateLiteral:
        return self.runstate

    async def wait_for_running(self, timeout: Optional[float] = None):
        if self._runstate not in ["undefined", "starting", "running"]:
            raise RuntimeError(
                "Worker not started or running, you would wait a long time"
            )

        if timeout is not None:
            timeout = float(timeout)
            if timeout <= 0:
                raise ValueError("Timeout must be greater than 0")
            async with asyncio.timeout(timeout):
                while not self.is_running():
                    await asyncio.sleep(min(0.1, timeout / 10))
        else:
            while not self.is_running():
                await asyncio.sleep(0.1)

    async def _prerun(self):
        self._check_process_file(hard=True)
        self._write_process_file()
        self.runstate = ("starting", "Loading packages")
        await reload_base(with_repos=False)
        self._save_disabled = True
        self.logger.debug("Starting worker with sys.executable: %s", sys.executable)
        self.logger.info("Starting worker forever")
        self.loop_manager.reset_loop()
        self.runstate = ("starting", "Loading config")
        await self.ini_config()

        self.runstate = ("starting", "Loading nodespace")
        self.initialize_nodespace()

        self._save_disabled = False

        if (
            os.environ.get("SUBPROCESS_MONITOR_PORT", None) is not None
            and USE_SUBPROCESS_MONITOR
        ):
            if not os.environ.get("SUBPROCESS_MONITOR_KEEP_RUNNING"):
                subprocess_monitor.call_on_manager_death(
                    self.stop,
                )

    def run_forever(self):
        asyncio.run(self.run_forever_async())

    async def run_forever_async(self):
        self.logger.debug("Starting worker forever async")
        await self._prerun()
        await self.worker_event("starting")
        try:
            self.runstate = "running"
            await self.loop_manager.run_forever_async()
        finally:
            self.stop()

        # run 1 second to ensure all tasks are finished
        await asyncio.sleep(1)

    def run_forever_threaded(self, wait_for_running=True):
        self.logger.debug("Starting worker forever in sub thread")

        runthread = threading.Thread(target=self.run_forever, daemon=True)
        runthread.start()
        if wait_for_running:
            while not self.is_running():
                time.sleep(0.1)
        return runthread

    @classmethod
    def init_and_run_forever(
        cls,
        *args,
        **kwargs,
    ):
        worker = cls(*args, **kwargs)
        worker.run_forever()
        worker.logger.debug("Worker initialized and running stopped")

    def stop(self, save: bool = True):
        if self.is_running():
            self.loop_manager.async_call(self.worker_event("stopping"))
        self.runstate = "stopped"
        if save:
            self.save()
        self._save_disabled = True

        self.loop_manager.stop()
        for handler in self.logger.handlers:
            try:
                handler.flush()
            except Exception:  # pragma: no cover
                pass

        if self._process_file.exists():
            self._process_file.unlink()
        if self._runstate_file.exists():
            self._runstate_file.unlink()

    def is_running(self):
        return self.loop_manager.running

    def cleanup(self):
        try:
            self.runstate = "removed"
        except NameError:
            pass
        if self.is_running():  # pragma: no cover
            self.stop()
        self.loop_manager.stop()
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)

        self.nodespace.cleanup()

    def __del__(self):
        self.cleanup()

    async def run_cmd(self, json_msg: CmdMessage):
        cmd = json_msg["cmd"]
        if cmd not in self._exposed_methods:
            raise self.UnknownCmdException(
                f"Unknown command {cmd} , available commands: {', '.join(self._exposed_methods.keys())}"
            )
        kwargs = json_msg.get("kwargs", {})
        exp_method = self._exposed_methods[cmd]
        func = exp_method[0]
        self.logger.debug("Calling %s with %s", cmd, kwargs)
        if asyncio.iscoroutinefunction(func):
            result = await func(**kwargs)
        else:
            result = func(**kwargs)
        return result

    @exposed_method()
    def group_nodes(self, node_ids: List[str], group_ids: List[str]):
        """
        Groups the given node IDs into a new group using the hierarchical grouping logic.
        Returns the updated group mapping.
        """
        self.nodespace.groups.group_together(node_ids, group_ids)

        return self.nodespace.groups.get_all_groups()

    @exposed_method()
    def get_groups(self):
        return self.nodespace.groups.serialize()

    @requests_save
    @exposed_method()
    def update_group(self, gid: str, data: NodeGroup):
        try:
            group = self.nodespace.groups.get_group(gid)
        except Exception:
            return {"error": f"Group with id {gid} not found"}
        if not group:
            raise ValueError(f"Group with id {gid} not found")
        ans = {}

        if "position" in data:
            group["position"] = [float(data["position"][0]), float(data["position"][1])]
            ans["position"] = group["position"]

        return ans

    @exposed_method()
    def remove_group(self, gid: str):
        self.nodespace.groups.remove_group(gid)
        return True


class TriggerNode(TypedDict):
    id: str


class NodeSpaceEvent(TypedDict):
    type: Literal["nsevent"]
    event: str
    data: Dict[str, Any]
