from ._opts import aiohttp, placeholder_function


from .worker import Worker
from .remote_worker import RemoteWorker
from .external_worker import FuncNodesExternalWorker, ExternalWorkerConfig
from .loop import CustomLoop

if not aiohttp:
    WSWorker = placeholder_function("WSWorker", "funcnodes_worker[http]")
else:
    from .websocket import WSWorker


from .message_queue_worker import MsQueueWorker
from .socket import SocketWorker


__all__ = [
    "ExternalWorkerConfig",
    "Worker",
    "RemoteWorker",
    "FuncNodesExternalWorker",
    "CustomLoop",
    "WSWorker",
    "MsQueueWorker",
    "SocketWorker",
]
