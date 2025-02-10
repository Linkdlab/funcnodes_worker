try:
    from funcnodes_react_flow import (
        FUNCNODES_REACT_PLUGIN,
        get_react_plugin_content,
    )

    FUNCNODES_REACT = True
except (ModuleNotFoundError, ImportError):
    FUNCNODES_REACT = False
    FUNCNODES_REACT_PLUGIN = None
    get_react_plugin_content = None

try:
    import venvmngr

    USE_VENV = True
except (ModuleNotFoundError, ImportError):
    USE_VENV = False
    venvmngr = None

try:
    import subprocess_monitor

    USE_SUBPROCESS_MONITOR = True
except (ModuleNotFoundError, ImportError):
    subprocess_monitor = None
    USE_SUBPROCESS_MONITOR = False


try:
    import requests

    USE_HTTP = True
except (ModuleNotFoundError, ImportError):
    requests = None
    USE_HTTP = False

try:
    import funcnodes

    IN_FUNCNODES = True
except (ModuleNotFoundError, ImportError):
    funcnodes = None
    IN_FUNCNODES = False

__all__ = [
    "FUNCNODES_REACT",
    "FUNCNODES_REACT_PLUGIN",
    "get_react_plugin_content",
    "USE_VENV",
    "venvmngr",
    "subprocess_monitor",
    "USE_SUBPROCESS_MONITOR",
    "requests",
    "USE_HTTP",
    "funcnodes",
    "IN_FUNCNODES",
]
