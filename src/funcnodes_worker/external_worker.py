from __future__ import annotations
from pathlib import Path
from typing import Dict, List, TypedDict, Union, Any, Optional, Type, ClassVar
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

    EXPORT_EXCLUDE_FIELDS: ClassVar[set[str]] = set()

    @classmethod
    def export_exclude_fields(cls) -> set[str]:
        """Returns field names that should be removed when exporting config."""
        excluded = set(getattr(cls, "EXPORT_EXCLUDE_FIELDS", set()))
        fields = getattr(cls, "model_fields", None) or getattr(cls, "__fields__", {})
        for name, field in fields.items():
            extra = getattr(field, "json_schema_extra", None)
            if extra is None and hasattr(field, "field_info"):
                extra = getattr(field.field_info, "extra", {}) or getattr(
                    field.field_info, "json_schema_extra", None
                )
            if extra and extra.get("export") is False:
                excluded.add(name)
        return excluded

    def exportable_dict(self) -> dict:
        """Serialize config without export-excluded fields."""
        return self.model_dump(mode="json", exclude=self.export_exclude_fields())


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
        data_path: Optional[str] = None,
        name: Optional[str] = None,
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
        self._nodeshelf: Optional[Shelf] = None
        self._config = self.config_cls()
        self._data_path: Optional[Path] = Path(data_path) if data_path else None
        if name:
            self.name = name
        try:
            self.update_config(config)
        except Exception:
            pass
        if self.NODECLASSID not in FuncNodesExternalWorker.RUNNING_WORKERS:
            FuncNodesExternalWorker.RUNNING_WORKERS[self.NODECLASSID] = (
                WeakValueDictionary()
            )
        FuncNodesExternalWorker.RUNNING_WORKERS[self.NODECLASSID][self.uuid] = self

    @property
    def data_path(self) -> Optional[Path]:
        if self._data_path is None:
            return None
        if not self._data_path.exists():
            self._data_path.mkdir(parents=True, exist_ok=True)
        return self._data_path

    @data_path.setter
    def data_path(self, data_path: Optional[Path]):
        if data_path is None:
            self._data_path = None
        else:
            self._data_path = data_path.resolve()
        if not self._data_path.exists():
            self._data_path.mkdir(parents=True, exist_ok=True)

    def update_config(
        self, config: Optional[Union[ExternalWorkerConfig, Dict[str, Any]]] = None
    ):
        if config is None:
            return
        preconfig = config if isinstance(config, dict) else config.model_dump()
        self._config = self.config_cls(**{**self._config.model_dump(), **preconfig})
        try:
            self.post_config_update()
        except Exception as e:
            FUNCNODES_LOGGER.exception(e)
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
        if ns is None:
            return None
        return ref(ns)  #

    @nodeshelf.setter
    def nodeshelf(self, ns: Optional[Shelf]):
        self.set_nodeshelf(ns)

    def get_nodeshelf(self) -> Optional[Shelf]:
        return self._nodeshelf

    def set_nodeshelf(self, ns: Optional[Shelf]):
        if ns is None:
            self._nodeshelf = ns
        if not isinstance(ns, Shelf):
            raise ValueError("ns must be a Shelf or None")
        self._nodeshelf = ns
        self.emit("nodes_update")

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

    def serialize(self, export: bool = False) -> FuncNodesExternalWorkerJson:
        """
        Serializes the FuncNodesExternalWorker class.
        """
        cfg = (
            self.config.exportable_dict()
            if export and hasattr(self.config, "exportable_dict")
            else self.config.model_dump(mode="json")
        )
        return FuncNodesExternalWorkerJson(
            uuid=self.uuid,
            nodeclassid=self.NODECLASSID,
            running=self.running,
            name=self.name,
            config=cfg,
        )

    async def loop(self):
        pass


class FuncNodesExternalWorkerJson(TypedDict):
    """
    A class that represents a JSON object for FuncNodesExternalWorker.
    """

    uuid: str
    nodeclassid: str
    running: bool
    name: str
    config: dict


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
