# FuncNodes Worker

**FuncNodes Worker** (`funcnodes_worker`) is the execution engine for the [FuncNodes](https://github.com/Linkdlab/FuncNodes) ecosystem. It provides the runtime environment where nodes are executed, managed, and controlled.

> [!NOTE]
> For the full application usage, please refer to the main [FuncNodes repository](https://github.com/Linkdlab/FuncNodes).

## Key Features

- **Execution Runtime**: Manages the `NodeSpace` and the main event loop, ensuring efficient asynchronous execution of nodes.
- **Remote Control**:
    - **WebSocket Worker**: Allows remote management and frontend interaction via `aiohttp` WebSockets.
    - **Message Queue Worker**: Supports communication via multiprocessing queues for inter-process coordination.
- **Process Management**: Handles state persistence, heartbeats, and safe shutdown procedures.
- **Extensibility**: Supports "External Workers" to offload tasks to auxiliary processes or environments.

## Installation

```bash
pip install funcnodes-worker
```

## Dependencies

- **funcnodes-core**: The core logic definitions.
- **aiohttp**: For WebSocket communication (optional but recommended).
- **pydantic**: For configuration and data validation.

## Usage

In most cases, `funcnodes-worker` is used internally by the `funcnodes` main package. However, you can instantiate a worker programmatically if needed:

```python
from funcnodes_worker import WSWorker

# Create a worker that listens on a specific host and port
worker = WSWorker(host="localhost", port=9382)

# Start the worker loop (usually handled by an async runner)
# await worker.run()
```

## Documentation

For comprehensive documentation, visit the [FuncNodes Documentation](https://linkdlab.github.io/FuncNodes).
