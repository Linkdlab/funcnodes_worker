from __future__ import annotations
from typing import Optional

from funcnodes_core import FUNCNODES_LOGGER
from funcnodes_worker import CustomLoop
from .remote_worker import RemoteWorker
from multiprocessing import Queue

import json


class MsgLoop(CustomLoop):
    """ """

    def __init__(
        self,
        worker: MsQueueWorker,
        delay=0.1,
        *args,
        **kwargs,
    ) -> None:
        """
        Initializes a new WSLoop instance.

        Args:
          worker (WSWorker): The WebSocket worker.
          host (str): The host address for the WebSocket server.
          port (int): The port number for the WebSocket server.
          delay (int): The delay between loop iterations.
          *args: Additional arguments.
          **kwargs: Additional keyword arguments.
        """
        self._worker = worker
        super().__init__(*args, delay=delay, **kwargs)

    async def loop(self):
        """
        The main loop for the WebSocket server.
        """
        try:
            while not self._worker.receive_queue.empty():
                queue_message = self._worker.receive_queue.get()
                message = queue_message["msg"]
                target = queue_message.get("target")
                await self._worker.receive_message(message, target=target)
        except Exception as e:
            FUNCNODES_LOGGER.exception(e)

    async def stop(self):
        """
        Stops the WebSocket server.
        """
        self._worker.receive_queue.close()
        self._worker.send_queue.close()
        await super().stop()


class MsQueueWorker(RemoteWorker):
    """
    Remote worker for WebSocket connections.
    """

    def __init__(
        self,
        receive_queue: Queue,
        send_queue: Queue,
        **kwargs,
    ) -> None:
        """
        Initializes a new WSWorker object.

        Args:
          host (str, optional): The host to connect to. Defaults to None.
          port (int, optional): The port to connect to. Defaults to None.
          **kwargs: Additional keyword arguments.

                  Notes:
          If host or port are not provided, they will be retrieved from the config dictionary if available.

        Examples:
          >>> worker = WSWorker(host='127.0.0.1', port=9382)
          >>> worker = WSWorker()
        """
        super().__init__(**kwargs)
        c = self.config
        if c is None:
            c = {}

        self.receive_queue = receive_queue
        self.send_queue = send_queue

        self.ws_loop = MsgLoop(worker=self)
        self.loop_manager.add_loop(self.ws_loop)

    async def sendmessage(self, msg: str, target: Optional[str] = None):
        """send a message to the frontend"""
        quemessage = {"msg": msg, "target": target}
        self.send_queue.put(quemessage)

    def stop(self):
        """
        Stops the WSWorker.

        Returns:
          None.

        Examples:
          >>> worker.stop()
        """
        super().stop()
