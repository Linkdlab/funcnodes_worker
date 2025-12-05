from __future__ import annotations
from abc import abstractmethod
from typing import List, Callable, Tuple, Any, Awaitable, Optional, Dict
import json

from funcnodes_core import (
    NodeSpace,
    JSONEncoder,
    JSONDecoder,
    ByteEncoder,
    BytesEncdata,
)
import traceback
from .worker import (
    Worker,
    ProgressStateMessage,
    NodeSpaceEvent,
    ErrorMessage,
    CmdMessage,
    ResultMessage,
    WorkerJson,
)


class RemoteWorkerJson(WorkerJson):
    pass


class RemoteWorker(Worker):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._messagehandlers: List[
            Callable[[dict], Awaitable[Tuple[bool | None, str]]]
        ] = []

    async def set_progress_state(self, *args, **kwargs):
        await super().set_progress_state(*args, **kwargs)
        await self.send(ProgressStateMessage(type="progress", **self._progress_state))

    async def send(self, data, **kwargs):
        if not isinstance(data, str):
            data = json.dumps(data, cls=JSONEncoder)
        # self.logger.debug(f"Sending message {data}")
        await self.sendmessage(data, **kwargs)

    @abstractmethod
    async def sendmessage(self, msg: str, **kwargs):
        """send a message to the frontend"""

    @abstractmethod
    async def send_bytes(self, data: bytes, header: dict, **sendkwargs):
        """send a message to the frontend"""

    async def send_byte_object(
        self,
        obj: Any,
        type: str,
        preview=False,
        header: Optional[Dict[str, str]] = None,
        **sendkwargs,
    ):
        if isinstance(obj, BytesEncdata):
            enc = obj
        else:
            enc = ByteEncoder.encode(obj, preview=preview)
        if header is None:
            header = {}
        header["mime"] = enc.mime
        header["type"] = type
        if preview:
            header["preview"] = "1"
        await self.send_bytes(enc.data, header, **sendkwargs)

    def on_nodespaceevent_after_set_value(
        self, event, src: NodeSpace, node: str, io: str, result: Any, **kwargs
    ):
        _node = src.get_node_by_id(node)
        if _node is None:
            return
        _io = _node.get_input_or_output(io)
        if _io is None:
            return
        self.loop_manager.async_call(
            self.send_byte_object(
                result,
                header=dict(node=_node.uuid, io=_io.uuid),
                type="io_value",
                preview=True,
            )
        )

    def on_nodespaceevent(self, event, src: NodeSpace, **kwargs):
        if event in {
            "before_set_value",
            "before_request_trigger",
            "after_request_trigger",
            "before_disconnect",
            "before_connect",
            "before_trigger",
            "after_trigger",
            "before_unforward",
            "before_forward",
        }:
            return
        if event == "node_trigger_error":
            self.logger.exception(kwargs["error"])
        event_bundle: NodeSpaceEvent = {
            "type": "nsevent",
            "event": event,
            "data": kwargs,
        }
        if event in ("after_set_value", "before_set_value"):
            event_bundle = JSONEncoder.apply_custom_encoding(event_bundle, preview=True)

        self.loop_manager.async_call(self.send(event_bundle))
        return event_bundle

    def _on_nodespaceerror(
        self,
        error: Exception,
        src: NodeSpace,
    ):
        """handle nodespace errors"""
        error_bundle = {
            "type": "error_event",
            "error": repr(error),
            "tb": list(traceback.TracebackException.from_exception(error).format()),
        }
        self.logger.exception(error)
        self.loop_manager.async_call(self.send(error_bundle))

    async def receive_message(self, json_msg: dict, **sendkwargs):
        self.logger.debug(f"received message {json_msg}")

        if isinstance(json_msg, str):
            json_msg = json.loads(json_msg, cls=JSONDecoder)

        if "type" not in json_msg:
            return
        try:
            if json_msg["type"] == "cmd":
                await self._handle_cmd_msg(json_msg, json_response=True, **sendkwargs)
            if json_msg["type"] == "ping":
                await self.send('{"type": "pong"}')
        except Exception as exc:
            self.logger.exception(exc)
            await self.send(
                ErrorMessage(
                    type="error",
                    error=str(exc),
                    tb=traceback.format_exception(exc),
                    id=json_msg.get("id"),
                )
            )

    recieve_message = receive_message

    async def _handle_cmd_msg(
        self, json_msg: CmdMessage, json_response=False, **sendkwargs
    ):
        result = await self.run_cmd(json_msg)
        if "as_bytes" in json_msg:
            json_response = not json_msg["as_bytes"]

        if json_response:
            await self.send(
                ResultMessage(type="result", result=result, id=json_msg.get("id")),
                **sendkwargs,
            )
        else:
            await self.send_byte_object(
                result, type="result", header=dict(id=json_msg.get("id")), **sendkwargs
            )

    def update_config(self, config: WorkerJson) -> RemoteWorkerJson:
        return super().update_config(config)

    def exportable_config(self) -> dict:
        """creates a copy of the config without the process specific data"""
        return super().exportable_config()
