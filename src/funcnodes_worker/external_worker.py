from __future__ import annotations
from typing import Dict, List, TypedDict, Union, Any, Optional, Type
from funcnodes_worker.loop import CustomLoop
from funcnodes_core import (
    NodeClassMixin,
    JSONEncoder,
    Encdata,
    EventEmitterMixin,
    Shelf,
    FUNCNODES_LOGGER,
)
from weakref import WeakValueDictionary
from pydantic import BaseModel
from weakref import ref


class ExternalWorkerConfig(BaseModel):
    """
    A class that represents the configuration of an external worker.
    """


class FuncNodesExternalWorker(NodeClassMixin, EventEmitterMixin, CustomLoop):
    """
    A class that represents an external worker with a loop and nodeable methods.
    """

    config_cls: Type[ExternalWorkerConfig] = ExternalWorkerConfig

    RUNNING_WORKERS: Dict[str, WeakValueDictionary[str, FuncNodesExternalWorker]] = {}
    IS_ABSTRACT = True

    def __init__(
        self,
        workerid,
        config: Optional[Union[ExternalWorkerConfig, Dict[str, Any]]] = None,
    ) -> None:
        """
        Initializes the FuncNodesExternalWorker class.

        Args:
          workerid (str): The id of the worker.
        """
        super().__init__(
            delay=1,
        )
        self.uuid = workerid

        self._config = self.config_cls()
        try:
            self.update_config(config)
        except Exception:
            pass
        if self.NODECLASSID not in FuncNodesExternalWorker.RUNNING_WORKERS:
            FuncNodesExternalWorker.RUNNING_WORKERS[self.NODECLASSID] = (
                WeakValueDictionary()
            )
        FuncNodesExternalWorker.RUNNING_WORKERS[self.NODECLASSID][self.uuid] = self

    def update_config(
        self, config: Optional[Union[ExternalWorkerConfig, Dict[str, Any]]] = None
    ):
        if config is None:
            return
        preconfig = config if isinstance(config, dict) else config.model_dump()
        self._config = self.config_cls(**{**self._config.model_dump(), **preconfig})
        self.post_config_update()
        FUNCNODES_LOGGER.info(f"config updated for worker {self.uuid}: {self._config}")
        return self._config

    def post_config_update(self):
        """
        This method is called after the config is updated to allow the worker to perform any necessary actions.
        """
        pass

    @property
    def config(self) -> ExternalWorkerConfig:
        return self._config

    @property
    def nodeshelf(self) -> Optional[ref[Shelf]]:
        ns = self.get_nodeshelf()
        print(f"nodeshelf: {ns}")
        if ns is None:
            return None
        if ns.name != self.uuid:
            ns = Shelf(
                name=self.uuid,
                description=ns.description,
                nodes=list(ns.nodes),
                subshelves=list(ns.subshelves),
            )
        return ref(ns)

    def get_nodeshelf(self) -> Optional[Shelf]:
        return None

    @classmethod
    def running_instances(cls) -> List[FuncNodesExternalWorker]:
        """
        Returns a list of running instances of FuncNodesExternalWorker.

        Returns:
          List[FuncNodesExternalWorker]: A list of running instances of FuncNodesExternalWorker.

        Examples:
          >>> FuncNodesExternalWorker.running_instances()
          [FuncNodesExternalWorker("worker1"), FuncNodesExternalWorker("worker2")]
        """
        if cls.NODECLASSID not in FuncNodesExternalWorker.RUNNING_WORKERS:
            return []

        res = []

        for ins in FuncNodesExternalWorker.RUNNING_WORKERS[cls.NODECLASSID].values():
            if ins.running:
                res.append(ins)
        return res

    async def stop(self):
        self._logger.debug("stopping external worker %s", self.uuid)
        self.emit("stopping")
        self.cleanup()
        await super().stop()

    def serialize(self) -> FuncNodesExternalWorkerJson:
        """
        Serializes the FuncNodesExternalWorker class.
        """
        return FuncNodesExternalWorkerJson(
            uuid=self.uuid,
            nodeclassid=self.NODECLASSID,
            running=self.running,
            name=self.name,
            config=self.config.model_dump(mode="json"),
        )


class FuncNodesExternalWorkerJson(TypedDict):
    """
    A class that represents a JSON object for FuncNodesExternalWorker.
    """

    uuid: str
    nodeclassid: str
    running: bool
    name: str


def encode_external_worker(obj, preview=False):  # noqa: F841
    if isinstance(obj, FuncNodesExternalWorker):
        return Encdata(
            data=obj.serialize(),
            handeled=True,
            done=True,
            continue_preview=False,
        )
    return Encdata(data=obj, handeled=False)  # pragma: no cover


JSONEncoder.add_encoder(encode_external_worker, [FuncNodesExternalWorker])


__all__ = [
    "FuncNodesExternalWorker",
    # "instance_nodefunction"
]
