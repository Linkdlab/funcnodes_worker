import asyncio
import json

import funcnodes_core as fn
from pytest_funcnodes import funcnodes_test


CSV_TEXT = """package_name,description,releases
funcnodes-randompackageßnamefortestA,Basic nodes,"0.1.0,0.2.0"
funcnodes-randompackageßnamefortestB,R2 nodes,"1.0.0"
"""


def _cache_csv_path():
    return fn.config.get_config_dir() / "cache" / "funcnodes_modules.csv"


@funcnodes_test
def test_load_cached_repo_csv_success():
    from funcnodes_worker.utils import modules as mod

    mod.AVAILABLE_REPOS.clear()
    cache_path = _cache_csv_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(CSV_TEXT, encoding="utf-8")

    assert mod.load_cached_repo_csv() is True
    assert "funcnodes-randompackageßnamefortestA" in mod.AVAILABLE_REPOS
    assert (
        mod.AVAILABLE_REPOS["funcnodes-randompackageßnamefortestA"].description
        == "Basic nodes"
    )


@funcnodes_test
def test_load_cached_repo_csv_missing_returns_false():
    from funcnodes_worker.utils import modules as mod

    mod.AVAILABLE_REPOS.clear()
    assert mod.load_cached_repo_csv() is False


@funcnodes_test
async def test_load_repo_csv_falls_back_to_cache_on_remote_failure(monkeypatch):
    from funcnodes_worker.utils import modules as mod

    mod.AVAILABLE_REPOS.clear()
    cache_path = _cache_csv_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(CSV_TEXT, encoding="utf-8")

    async def failing_run(self, url=None, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(mod.HTTPTool, "run", failing_run)

    await mod.load_repo_csv(use_cache=True, update_cache=True)
    assert "funcnodes-randompackageßnamefortestB" in mod.AVAILABLE_REPOS


@funcnodes_test
def test_save_repo_csv_to_cache_writes_meta():
    from funcnodes_worker.utils import modules as mod

    mod.AVAILABLE_REPOS.clear()
    mod.save_repo_csv_to_cache(CSV_TEXT)

    cache_path = _cache_csv_path()
    meta_path = cache_path.with_suffix(cache_path.suffix + ".meta.json")

    assert cache_path.exists()
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["source_url"].startswith("https://")
    assert "last_updated" in meta


@funcnodes_test
async def test_background_refresh_accumulates_callbacks(monkeypatch):
    from funcnodes_worker.utils import modules as mod

    mod.AVAILABLE_REPOS.clear()

    class _FakeResp:
        async def text(self):
            return CSV_TEXT

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeResp()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_run(self, url=None, **kwargs):
        return _FakeCtx()

    monkeypatch.setattr(mod.HTTPTool, "run", fake_run)

    called = []

    async def cb1(repos):
        called.append("cb1")

    async def cb2(repos):
        called.append("cb2")

    task1 = mod.start_background_repo_refresh(callback=cb1)
    task2 = mod.start_background_repo_refresh(callback=cb2)
    assert task1 is task2

    await task1
    assert set(called) == {"cb1", "cb2"}


@funcnodes_test
async def test_get_available_modules_does_not_block_on_remote(monkeypatch):
    from funcnodes_worker import Worker
    from funcnodes_worker.utils import modules as mod

    class _SlowResp:
        async def text(self):
            await asyncio.sleep(0.5)
            return CSV_TEXT

    class _SlowCtx:
        async def __aenter__(self):
            return _SlowResp()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def slow_run(self, url=None, **kwargs):
        return _SlowCtx()

    monkeypatch.setattr(mod.HTTPTool, "run", slow_run)
    # setup() can be slow/unrelated here; avoid blocking the loop for this timing test
    monkeypatch.setattr(mod, "setup", lambda: None)

    class _TestWorker(Worker):
        def _on_nodespaceerror(self, error, src):
            return None

        def on_nodespaceevent(self, event, **kwargs):
            return None

    worker = _TestWorker(uuid="TestWorker_nonblocking")
    mod.AVAILABLE_REPOS.clear()
    cache_path = fn.config.get_config_dir() / "cache" / "funcnodes_modules.csv"
    if cache_path.exists():
        cache_path.unlink()

    res = await worker.get_available_modules()
    assert res is not None
    assert len(res["available"]) + len(res["active"]) == 0
    assert mod._background_refresh_task is not None

    await asyncio.sleep(1)
    assert mod._background_refresh_task is None
    assert len(mod._background_refresh_callbacks) == 0
    res = await worker.get_available_modules()
    assert res is not None
    assert len(res["available"]) + len(res["active"]) == 2
