"""Atomic writes and the cross-process lock."""

from __future__ import annotations

import asyncio
import os

from rtings_mcp.fsutil import (
    TMP_PREFIX,
    atomic_write_bytes,
    ensure_dir,
    file_lock,
    file_lock_sync,
    sweep_temp_files,
)


def test_atomic_write_keeps_the_temp_file_in_the_target_dir(tmp_path, monkeypatch):
    """`os.replace` across filesystems is a copy, not an atomic rename."""
    seen = {}
    import tempfile as tempfile_module

    real = tempfile_module.mkstemp

    def spy(*args, **kwargs):
        seen["dir"] = kwargs.get("dir")
        return real(*args, **kwargs)

    monkeypatch.setattr(tempfile_module, "mkstemp", spy)
    target = tmp_path / "nested" / "file.json"
    atomic_write_bytes(target, b"{}")
    assert seen["dir"] == str(target.parent)


def test_written_files_are_owner_only(tmp_path):
    target = tmp_path / "f.json"
    atomic_write_bytes(target, b"{}")
    assert oct(target.stat().st_mode)[-3:] == "600"
    ensure_dir(tmp_path / "d")
    assert oct((tmp_path / "d").stat().st_mode)[-3:] == "700"


def test_mtime_is_set_to_fetched_at(tmp_path):
    target = tmp_path / "f.json"
    atomic_write_bytes(target, b"{}", mtime=1_700_000_000.0)
    assert abs(target.stat().st_mtime - 1_700_000_000.0) < 1


def test_overwrite_is_atomic_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "f.json"
    atomic_write_bytes(target, b"1")
    atomic_write_bytes(target, b"22")
    assert target.read_bytes() == b"22"
    assert not list(tmp_path.glob(f"{TMP_PREFIX}*"))


def test_sweep_removes_orphaned_temp_files(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / f"{TMP_PREFIX}abc").write_text("junk")
    (tmp_path / "keep.json").write_text("{}")
    assert sweep_temp_files(tmp_path) == 1
    assert (tmp_path / "keep.json").exists()


async def test_async_lock_serializes_and_does_not_block_the_loop(tmp_path):
    lock_path = tmp_path / "k.lock"
    order = []

    async def worker(name):
        async with file_lock(lock_path):
            order.append(f"{name}-in")
            await asyncio.sleep(0.01)
            order.append(f"{name}-out")

    # A heartbeat proves the event loop kept running while the lock was held.
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(6):
            await asyncio.sleep(0.005)
            ticks += 1

    await asyncio.gather(worker("a"), worker("b"), heartbeat())
    assert order in (
        ["a-in", "a-out", "b-in", "b-out"],
        ["b-in", "b-out", "a-in", "a-out"],
    )
    assert ticks > 0


def test_sync_lock_releases_on_exception(tmp_path):
    lock_path = tmp_path / "k.lock"
    try:
        with file_lock_sync(lock_path):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with file_lock_sync(lock_path):  # would hang if the lock leaked
        pass
    assert os.path.exists(lock_path)
