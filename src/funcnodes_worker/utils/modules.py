from __future__ import annotations
from collections.abc import Callable
import csv
import importlib
import importlib.metadata
from datetime import datetime, timezone
from pathlib import Path

from typing import Any, Awaitable, List, Optional, Dict, Union
from funcnodes_core import AVAILABLE_MODULES, setup, FUNCNODES_LOGGER
from funcnodes_core._setup import setup_module
from funcnodes_core.utils.plugins import InstalledModule
from funcnodes_core.utils.cache import (
    get_cache_path,
    set_cache_meta_for,
    write_cache_text,
)
from dataclasses import dataclass, field
import logging
from asynctoolkit.defaults.http import HTTPTool
from asynctoolkit.defaults.packageinstaller import PackageInstallerTool
import asyncio
from packaging.specifiers import Specifier, InvalidSpecifier
from packaging.version import Version
from .._opts import venvmngr


@dataclass
class AvailableRepo:
    """
    Data class representing an available repository/package.

    Fields:
      - package_name: The package's name.
      - installed: Flag indicating whether the package is installed.
      - version: Version of the installed package.
      - description: A short description of the package.
      - entry_point__module, entry_point__shelf, entry_point__external_worker: Optional entry points for
            different functionalities.
      - moduledata: An instance of InstalledModule containing module-specific data.
      - last_updated, homepage, source, summary: Additional metadata.
      - releases: A list of available releases/versions.
    """

    package_name: str
    installed: bool
    version: str = ""
    description: str = ""
    entry_point__module: Optional[str] = None
    entry_point__shelf: Optional[str] = None
    entry_point__external_worker: Optional[str] = None
    moduledata: Optional[InstalledModule] = None
    last_updated: Optional[str] = None
    homepage: Optional[str] = None
    source: Optional[str] = None
    summary: Optional[str] = None
    releases: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data):
        """
        Create an AvailableRepo instance from a dictionary.
        Ensures defaults for missing keys and processes the 'releases' field.
        """
        # Ensure 'installed' key exists; default to False if missing
        data.setdefault("installed", False)
        # Ensure 'releases' key exists; default to empty string if missing
        data.setdefault("releases", "")
        # Process the releases: split by comma and strip whitespace
        releases = data["releases"]
        releases = releases.strip().split(",")
        releases = [v.strip() for v in releases]
        releases = [v for v in releases if v]
        data["releases"] = releases

        # Only pass the keys that are defined in the dataclass fields
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def _repr_json_(self) -> dict[str, Any]:
        return {
            "package_name": self.package_name,
            "installed": self.installed,
            "version": self.version,
            "description": self.description,
            "entry_point__module": self.entry_point__module,
            "entry_point__shelf": self.entry_point__shelf,
            "entry_point__external_worker": self.entry_point__external_worker,
            "moduledata": self.moduledata._repr_json_() if self.moduledata else None,
            "last_updated": self.last_updated,
            "homepage": self.homepage,
            "source": self.source,
            "summary": self.summary,
            "releases": self.releases,
        }


def version_string_to_Specifier(version: str) -> Specifier:
    version = str(version).strip()
    try:
        return Specifier(version)
    except InvalidSpecifier:
        # version can be only a single version, not a range, so we need to convert it to a range
        return Specifier("==" + version)


def version_to_range(version: str) -> str:
    if not version:
        return None
    version = str(version).strip()
    return str(version_string_to_Specifier(version))


# Global dictionary mapping package names to their repository information
AVAILABLE_REPOS: Dict[str, AvailableRepo] = {}


REPO_CSV_URL = "https://raw.githubusercontent.com/Linkdlab/funcnodes_repositories/refs/heads/main/funcnodes_modules.csv"


def get_cached_csv_path() -> Path:
    return get_cache_path("funcnodes_modules.csv")


def _parse_repo_csv(text: str):
    """Parse CSV text and update AVAILABLE_REPOS."""
    reader = csv.DictReader(text.splitlines(), delimiter=",")
    for line in reader:
        try:
            data = AvailableRepo.from_dict(line)
            if data.package_name in AVAILABLE_REPOS:
                moddata = AVAILABLE_REPOS[data.package_name].moduledata
                data.moduledata = moddata
            if data.moduledata:
                data.installed = True
            AVAILABLE_REPOS[data.package_name] = data
        except Exception as e:
            FUNCNODES_LOGGER.exception(e)


def load_cached_repo_csv() -> bool:
    """
    Load repository data from local cache.
    Returns True if cache was loaded successfully, False otherwise.
    """
    cache_path = get_cached_csv_path()
    if not cache_path.exists():
        return False

    try:
        text = cache_path.read_text(encoding="utf-8")
        _parse_repo_csv(text)
        FUNCNODES_LOGGER.debug("Loaded repository data from cache")
        return True
    except Exception as e:
        FUNCNODES_LOGGER.warning(f"Failed to load cached repo CSV: {e}")
        return False


def save_repo_csv_to_cache(text: str):
    """Save repository CSV data to local cache."""
    cache_path = get_cached_csv_path()
    try:
        write_cache_text(cache_path, text)
        meta = {
            "last_updated": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "source_url": REPO_CSV_URL,
        }
        set_cache_meta_for(cache_path, meta)
        FUNCNODES_LOGGER.debug("Saved repository data to cache")
    except Exception as e:
        FUNCNODES_LOGGER.warning(f"Failed to save repo CSV to cache: {e}")


_background_refresh_task: Optional[asyncio.Task] = None
_background_refresh_callbacks: List[
    Callable[[Dict[str, AvailableRepo]], Union[None, Awaitable[None]]]
] = []


async def refresh_repo_csv_background():
    """
    Refresh repository CSV in the background.

    Any callbacks registered via start_background_repo_refresh will be invoked on success.
    """
    global _background_refresh_task
    global _background_refresh_callbacks

    callbacks = _background_refresh_callbacks
    try:
        async with await HTTPTool().run(url=REPO_CSV_URL) as resp:
            text = await resp.text()

        _parse_repo_csv(text)
        save_repo_csv_to_cache(text)

    except Exception as e:
        FUNCNODES_LOGGER.debug(f"Background repo refresh failed: {e}")
        callbacks = []
    finally:
        _background_refresh_task = None
        _background_refresh_callbacks = []

    for cb in callbacks:
        try:
            res = cb(AVAILABLE_REPOS)
            if asyncio.iscoroutine(res):
                await res
        except Exception as e:
            FUNCNODES_LOGGER.debug(f"Repo refresh callback failed: {e}, callback: {cb}")


def start_background_repo_refresh(
    callback: Optional[
        Callable[[Dict[str, AvailableRepo]], Union[None, Awaitable[None]]]
    ] = None,
):
    """Start a background task to refresh the repo CSV."""
    global _background_refresh_task

    if callback is not None:
        _background_refresh_callbacks.append(callback)

    if _background_refresh_task is not None:
        return _background_refresh_task

    _background_refresh_task = asyncio.create_task(refresh_repo_csv_background())
    return _background_refresh_task


async def load_repo_csv(use_cache: bool = True, update_cache: bool = True):
    """
    Load repository metadata from a remote CSV file and update AVAILABLE_REPOS.
    """
    cache_loaded = False
    if use_cache:
        cache_loaded = load_cached_repo_csv()
        if cache_loaded and not update_cache:
            return

    try:
        async with await HTTPTool().run(url=REPO_CSV_URL) as resp:
            text = await resp.text()

        _parse_repo_csv(text)

        if update_cache:
            save_repo_csv_to_cache(text)

    except Exception as e:
        FUNCNODES_LOGGER.exception(e)
        if use_cache and not cache_loaded:
            load_cached_repo_csv()


async def install_package(
    package_name,
    version=None,
    upgrade=False,
    env_manager: Optional[venvmngr.VenvManager] = None,
    logger: Optional[logging.Logger] = None,
):
    """
    Install a Python package using pip (or an environment manager if provided).

    Parameters:
      - package_name (str): Name of the package to install.
      - version (str, optional): Specific version to install. Defaults to None.
      - upgrade (bool, optional): Whether to upgrade if already installed. Defaults to False.
      - env_manager (VenvManager, optional): Custom environment manager for installation.
      - logger (Logger, optional): Logger for output messages.

    Returns:
      - bool: True if installation succeeded or package is already installed, False otherwise.
    """
    if version:
        target_ver = version_string_to_Specifier(version)
    else:
        target_ver = None
    if logger:
        info = logger.info
        debug = logger.debug
        error = logger.error
    else:
        info = print
        debug = print
        error = print

    if env_manager is None:
        try:
            # Check if the package is already installed using importlib.metadata
            installed_version = Version(importlib.metadata.version(package_name))
            # check if the version fulfills the requirements
            if target_ver and target_ver.contains(installed_version):
                upgrade = False

            if upgrade:
                info(
                    f"Package '{package_name}'({installed_version}) is already installed. Upgrading ({target_ver})...",
                )
            else:
                info(
                    f"Package '{package_name}'({installed_version}) is already installed {target_ver}.",
                )
                return True
        except importlib.metadata.PackageNotFoundError:
            pass

        try:
            # Execute the pip command as a subprocess.

            info(f"Installing package '{package_name}'...")

            await PackageInstallerTool().run(
                package_name,
                version=str(target_ver) if target_ver else version,
                upgrade=upgrade,
            )
            debug(f"Installed package '{package_name}'...")
            return True
        except Exception:
            # Return False if pip returns an error.
            return False
    # If an environment manager is provided, use it to install the package.
    lines = []
    try:
        # Callback function to collect output lines.
        def cb(line):
            lines.append(line)

        env_manager.install_package(
            package_name=package_name,
            version=str(target_ver) if target_ver else version,
            upgrade=upgrade,
            stderr_callback=cb,
            stdout_callback=cb,
        )
        if len(lines) > 0:
            output = "\n".join(lines)
            info(output)
        return True
    except Exception as e:
        error(e)
        if len(lines) > 0:
            output = "\n".join(lines)
            error(output)
        return False


async def install_repo(
    package_name: str,
    upgrade: bool = False,
    version=None,
    env_manager: Optional[venvmngr.VenvManager] = None,
    logger: Optional[logging.Logger] = None,
) -> Optional[AvailableRepo]:
    """
    Install a repository package and update its repository info.

    Parameters:
      - package_name (str): Name of the repository package.
      - upgrade (bool): Whether to upgrade if already installed.
      - version: Specific version to install.
      - env_manager: Optional environment manager for installation.
      - logger: Optional logger for messages.

    Returns:
      - An AvailableRepo instance if installation and import succeeded, or None otherwise.
    """
    if package_name not in AVAILABLE_REPOS:
        return False  # Repository metadata not found

    # Attempt to install the package
    if not await install_package(
        package_name, version, upgrade, env_manager=env_manager, logger=logger
    ):
        return None

    await reload_base(with_repos=False)

    if package_name in AVAILABLE_REPOS:
        try_import_repo(package_name)
        return AVAILABLE_REPOS[package_name]

    return None


def try_import_module(name: str) -> Optional[AvailableRepo]:
    """
    Attempt to import a module by its name and update its repository info.

    The function first looks up the repository info in AVAILABLE_REPOS by trying
    several key formats (with underscores or hyphens). If not found, it tries to import
    the module dynamically and then creates a new AvailableRepo entry.

    Returns:
      - An AvailableRepo instance if the import is successful, or None otherwise.
    """
    # Try to find the repo using different name formats
    repo = (
        AVAILABLE_REPOS.get(name)
        or AVAILABLE_REPOS.get(name.replace("_", "-"))
        or AVAILABLE_REPOS.get(name.replace("-", "_"))
    )
    if not repo:
        try:
            # Normalize the module name: replace hyphens with underscores
            modulename = name.replace("-", "_")
            module = importlib.import_module(modulename)
            # Process the module using the setup_module function to generate module data
            module_data = setup_module(InstalledModule(name=name, module=module))

            # Create a new repo entry and mark it as installed
            repo = AvailableRepo(
                package_name=name, installed=True, moduledata=module_data
            )
            # Use a normalized key in the global dictionary
            AVAILABLE_REPOS[name.replace("_", "-")] = repo
        except Exception as e:
            print(f"Error importing {name}: {e}")

    # Return the repository info after trying to import (using normalized name)
    return try_import_repo(name.replace("_", "-"))


def try_import_repo(name: str) -> Optional[AvailableRepo]:
    """
    Try to import the module associated with the repository.

    If the module has already been imported and module data is available,
    simply return the repository info. Otherwise, attempt to import the module,
    process it with setup_module, and update the repository info.

    Returns:
      - The AvailableRepo instance if successful, or None otherwise.
    """
    if name not in AVAILABLE_REPOS:
        return None

    repo = AVAILABLE_REPOS[name]
    if repo.moduledata:
        # Module already imported; nothing more to do.
        return repo
    try:
        # Normalize the module name for import
        modulename = repo.package_name.replace("-", "_")
        module = importlib.import_module(modulename)

        # Process the module to get its metadata
        moduledata = setup_module(InstalledModule(name=modulename, module=module))

        # Update the repo entry with the module data
        repo.moduledata = moduledata
        return repo
    except Exception as e:
        print(f"Error importing {repo.package_name}: {e}")
    return None


async def reload_base(
    with_repos: bool = True,
    retries: int = 2,
    retries_delay: float = 1,
    background_repo_refresh: bool = False,
    repo_refresh_callback: Optional[
        Callable[[Dict[str, AvailableRepo]], Union[None, Awaitable[None]]]
    ] = None,
):
    """
    Reload the core setup and update repository/module information.

    This function calls the global setup function, reloads repository data
    from the CSV (if requested), and synchronizes AVAILABLE_REPOS with the data
    from AVAILABLE_MODULES (which holds installed module data). If the core setup
    fails, it retries a specified number of times with a delay between retries.
    This is to prevent this function to fail because of newly installed modules, which
    are only partially initialized.


    Parameters:
        - with_repos (bool): Whether to load repository data from the CSV.
        - retries (int): Number of retries for the core setup.
        - retries_delay (int): Delay between retries in seconds.

    Raises:
        - Exception: If the core setup fails after all retries.
    """
    # Initialize or refresh the core setup
    retries = max(retries, 0)
    while retries >= 0:
        retries -= 1
        try:
            setup()
            break
        except Exception as e:
            if retries < 0:
                raise e
            await asyncio.sleep(retries_delay)
            continue

    if with_repos:
        if background_repo_refresh:
            load_cached_repo_csv()
            start_background_repo_refresh(callback=repo_refresh_callback)
        else:
            try:
                await load_repo_csv(use_cache=True, update_cache=True)
            except Exception:
                pass
    # Update the 'installed' flag for each repo based on the presence of module data
    for repo in AVAILABLE_REPOS.values():
        if repo.moduledata:
            repo.installed = True
        else:
            repo.installed = False

    # Loop through the installed modules and update/add corresponding repo entries.
    for modulename, moduledata in AVAILABLE_MODULES.items():
        # Normalize module name to use hyphens (as seen on PyPI)
        modulename = modulename.replace("_", "-")
        if modulename in AVAILABLE_REPOS:
            AVAILABLE_REPOS[modulename].installed = True
            AVAILABLE_REPOS[modulename].moduledata = moduledata
        else:
            AVAILABLE_REPOS[modulename] = AvailableRepo(
                package_name=modulename,
                installed=True,
                summary=moduledata.description,
                moduledata=moduledata,
            )
    # Finally, update the version information for each repo if available.
    for modulename, repo in AVAILABLE_REPOS.items():
        if repo.moduledata:
            if repo.moduledata.version:
                repo.version = repo.moduledata.version
