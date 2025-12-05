# Changelog

## 1.4.0 (2025-12-05)

### Feat

- **worker**: add optional name and config parameters to external worker initialization

### Fix

- **loop**: await task shutdown and prune stale worker state

### Refactor

- **tests**: migrate test_loops to pytest and implement fixtures
- **tests**: migrate WSWorker tests to pytest and implement fixtures
- **tests**: remove unused pytestmark from test_socketworker.py
- **tests**: migrate SocketWorker tests to pytest and utilize fixtures
- **tests**: migrate test_client_connection to pytest and improve test structure
- **tests**: update worker fixture to use test name for UUID

## 1.3.0 (2025-11-27)

### Feat

- **external-worker**: support exportable configs and nodeshelf updates
- **websocket**: implement graceful client connection closure and enhance message enqueue handling
- **worker**: enhance FuncNodesExternalWorker with nodeshelf property and logging for configuration updates
- **external_worker**: introduce ExternalWorkerConfig for improved configuration management and update FuncNodesExternalWorker to utilize it

### Fix

- **worker**: align external worker shelf updates and export
- **loop**: handle closed or missing event loop for tasks
- **tests**: correct asyncio_default_fixture_loop_scope format in pytest configuration

## 1.2.1 (2025-11-06)

### Fix

- **websocket**: handle web.ClientError during message sending to prevent unhandled exceptions

### Refactor

- **tests**: remove unused data_path assignment in TestWorkerInitCases

## 1.2.0 (2025-11-05)

### Refactor

- **worker**: update data path handling to set default data_path to None
- **worker**: add utility functions for worker directory management and update data path handling

## v1.1.1 - 2025-10-22

- Bump version metadata for the stable 1.1.1 release (no code changes from v1.1.1a1).

## v1.1.1a1 - 2025-10-21

- Use weak references in `LoopManager` so worker instances can be garbage-collected without circular references.

## v1.1.1a0 - 2025-10-21

- Prepare the 1.1.1 alpha by updating version metadata and the dependency lock file.

## v1.1.0 - 2025-10-21

- Tighten worker process file management and the accompanying tests to validate state tracking and directories.
- Adopt a `.flake8` configuration and refreshed pre-commit hooks to enforce consistent linting.
- Add the `python-slugify` dependency alongside the 1.1.0 version bump.

## v1.0.1 - 2025-10-21

- Refine runstate handling by renaming types, preserving value casing, and improving debug logging readability.
- Update worker tests to manage setup and teardown resources more reliably.
- Bump funcnodes-core, funcnodes, and venvmngr dependencies for the 1.0.1 release.

## v1.0.0 - 2025-08-29

- Promote the worker to 1.0.0 and align funcnodes-core with the stable release.

## v1.0.0a0 - 2025-08-29

- Add hierarchical node grouping capabilities.
- Improve worker shutdown by guaranteeing cleanup and expanding debug logging for stop flows.
- Allow prerelease dependencies during package installation.
- Switch to the Hatchling build system and update subprocess-monitor, funcnodes-core, and objgraph for the alpha.

## v0.3.2 - 2025-06-05

- Add descriptive metadata and a `reset_inputs_on_trigger` flag to workers.
- Specify typing for `files_uploaded` in the WebSocket loop to improve type safety.
- Update funcnodes-core to 0.3.54 as part of the 0.3.2 release.

## v0.3.1 - 2025-03-27

- Introduce `update_io_value_options` to refresh IO value choices dynamically.

## v0.3.0 - 2025-03-26

- Refactor remote and socket workers to improve byte message handling and add a `send_bytes` helper.
- Implement a `ClientConnection` helper for WebSocket messaging and fix event propagation through `on_nodespaceevent`.
- Update funcnodes-core to 0.3.51/0.3.50 for compatibility with the new messaging flow.

## v0.2.5 - 2025-03-02

- Refine `reload_base` with retry logic when core setup fails.
- Decode JSON payloads upon receipt and ensure worker loops break correctly on shutdown.
- Correct an earlier version bump prior to tagging 0.2.5.

## v0.2.4 - 2025-03-02

- Teach `RemoteWorker` to respond to ping messages and bump the version to 0.2.4.

## v0.2.3 - 2025-03-01

- Support PEP 440 specifiers when evaluating package versions and improve installation logging.
- Fix string formatting regressions introduced in earlier releases.

## v0.2.2 - 2025-02-28

- Update funcnodes-core and funcnodes-react-flow dependencies as part of the 0.2.2 release.
- Store sensitive node properties via `secret_property` for better security.

## v0.2.1 - 2025-02-27

- Harden tests by invoking `fn_set_in_test`, using `wait_for_running` with timeouts, and cleaning up view serialization.
- Fix property lookup to handle missing values and improve node view traversal.
- Ignore `.tox` and `.funcnodes` artifacts in version control.

## v0.2.0 - 2025-02-27

- Update view state handling to use optional types and to populate nodes from the nodespace copy.
- Increase test wait durations for more reliable triggering.
- Bump funcnodes-core to 0.3.42 while tagging 0.2.0.

## v0.1.14 - 2025-02-27

- Improve upload handling by decoding string payloads, validating paths, and creating missing directories.
- Release 0.1.14 with the updated upload safeguards.

## v0.1.13 - 2025-02-27

- Expose the upload API so string payloads are decoded automatically.

## v0.1.12 - 2025-02-26

- Add a synchronous option to `add_package_dependency` and make full sync optional.
- Retry base reloads during repository installation and lengthen sleeps to stabilise tests.

## v0.1.11 - 2025-02-21

- Add a `get_runstate` accessor, guard against double stops, and enhance run lifecycle logging.
- Rework `LoopManager` to co-ordinate tasks via `async_call` for cleaner shutdown.
- Normalise test data paths to POSIX format while bumping to 0.1.11.

## v0.1.10 - 2025-02-19

- Add pause and resume controls to `CustomLoop` and automatically pause nodespace loops during data loads.
- Enhance loop state management as part of the 0.1.10 release.

## v0.1.9 - 2025-02-19

- Convert `load_data` to an asynchronous workflow for more efficient loading.
- Log package installation progress and remove the documentation deployment workflow.

## v0.1.8 - 2025-02-17

- Ensure `files_path` directories are created on demand and add upload coverage tests.

## v0.1.7 - 2025-02-17

- Switch package installation to `PackageInstallerTool` and update the `asynctoolkit` dependency to 0.1.1.

## v0.1.6 - 2025-02-17

- Make worker methods asynchronous, cover package installation via micropip, and add tests for dependency additions.

## v0.1.5 - 2025-02-17

- Track worker runstate transitions explicitly and validate loops before reuse.
- Refactor repository loading and installation to support asynchronous execution and clarify type hints.

## v0.1.4 - 2025-02-13

- Fix typos around the `receive` method and its references across the codebase.

## v0.1.3 - 2025-02-13

- Overhaul plugin loading and error handling for missing optional dependencies.
- Add extensive SocketWorker and ExternalWorker tests, introduce `UnknownCmdException`, and refactor `LoopManager` to avoid threading.
- Migrate to `pathlib`/`importlib.metadata`, add deprecation warnings, and update packaging docs and tox configuration.

## v0.1.2 - 2025-02-10

- Add optional `aiohttp` support with graceful fallbacks while bumping to 0.1.2.

## v0.1.1 - 2025-02-10

- Handle optional `requests` and `psutil` imports, integrating `subprocess_monitor` only when available, and release 0.1.1.

## v0.1.0 - 2025-02-10

- Initial release of the FuncNodes worker package and continuous integration workflows.
