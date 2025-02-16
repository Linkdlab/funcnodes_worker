from typing import Tuple
from types import ModuleType
from collections.abc import Callable


class DependencyError(Exception):
    pass


def placeholder_function(
    name,
    dep,
) -> Callable:
    def _f(*args, **kwargs):
        raise DependencyError(f"{name} is not installed, please install {dep}")

    return _f


def placeholder_module(name, dep) -> ModuleType:
    class _Module:
        def __getattribute__(self, _name):
            raise DependencyError(f"{name} is not installed, please install {dep}")

    return _Module()


def palceholder_obj(name, dep) -> object:
    class _Obj:
        def __getattribute__(self, _name):
            raise DependencyError(f"{name} is not installed, please install {dep}")

    return _Obj()


class PlaceHolderClass:
    classname = None
    dependency = None

    def __getattribute__(self, _name):
        raise DependencyError(
            f"{self.classname} is not installed, please install {self.dependency}"
        )


def placeholder_class(name, dep) -> object:
    class _PlaceHolderClass(PlaceHolderClass):
        classname = name
        dependency = dep

    return _PlaceHolderClass()


def FUNCNODES_REACT() -> Tuple[bool, ModuleType]:
    try:
        import funcnodes_react_flow

        FUNCNODES_REACT = True
    except (ModuleNotFoundError, ImportError):
        funcnodes_react_flow = placeholder_module(
            "funcnodes_react_flow", "funcnodes-react-flow"
        )
        FUNCNODES_REACT = False

    return FUNCNODES_REACT, funcnodes_react_flow


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
    import aiohttp

    USE_HTTP = True
except (ModuleNotFoundError, ImportError):
    requests = None
    aiohttp = None
    USE_HTTP = False

try:
    import funcnodes

    IN_FUNCNODES = True  # pragma: no cover
except (ModuleNotFoundError, ImportError):
    funcnodes = None
    IN_FUNCNODES = False


__all__ = [
    "FUNCNODES_REACT",
    "USE_VENV",
    "venvmngr",
    "subprocess_monitor",
    "USE_SUBPROCESS_MONITOR",
    "requests",
    "aiohttp",
    "USE_HTTP",
    "funcnodes",
    "IN_FUNCNODES",
]
