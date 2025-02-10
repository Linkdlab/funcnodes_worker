from ._opts import aiohttp, placeholder_function


from .worker import Worker
from .remote_worker import RemoteWorker
from .external_worker import FuncNodesExternalWorker
from .loop import CustomLoop

if aiohttp:
    from .websocket import WSWorker
else:
    WSWorker = placeholder_function("WSWorker", "http")

from .message_queue_worker import MsQueueWorker
from .socket import SocketWorker


__all__ = [
    "Worker",
    "RemoteWorker",
    "FuncNodesExternalWorker",
    "CustomLoop",
    "WSWorker",
    "MsQueueWorker",
    "SocketWorker",
]
