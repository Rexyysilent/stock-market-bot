"""Crash-safe helpers for the pipeline's small JSON state files."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import tempfile
from pathlib import Path


def load_json_state(path, default=None):
    """Load JSON state.

    Missing files return ``default``. Corrupt or unreadable existing files are
    not silently treated as empty state: callers must surface the failure.
    """
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path, value, *, indent=1, encoder_cls=None):
    """Durably replace one JSON state file without exposing a partial write."""
    payload = json.dumps(
        value,
        indent=indent,
        ensure_ascii=False,
        allow_nan=False,
        cls=encoder_cls,
    ).encode("utf-8") + b"\n"
    atomic_write_bytes(path, payload)


def atomic_write_bytes(path, payload):
    """Durably replace one file with bytes from the same filesystem."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def atomic_text_writer(path, *, encoding="utf-8"):
    """Yield a text handle and replace ``path`` only after a clean close."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(
            descriptor, "w", encoding=encoding, newline="\n"
        ) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def exclusive_file_lock(path):
    """Hold a non-blocking process lock for one pipeline run.

    The lock is advisory and released by the operating system if the process
    dies. The small marker file can remain safely; ownership lives on its open
    handle rather than on file existence.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(f"pipeline already running: {path}") from exc

    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def state_transaction(paths):
    """Restore selected mutable state files when a run does not commit.

    Export output and append-only archives are intentionally outside this
    transaction. This protects signal histories from advancing when a stage,
    serialization, canonical archive, or mirror verification fails.
    """
    snapshots = {}
    ordered = []
    for item in paths:
        path = Path(item)
        key = os.path.normcase(os.path.abspath(path))
        if key in snapshots:
            continue
        ordered.append(path)
        snapshots[key] = path.read_bytes() if path.exists() else None

    try:
        yield
    except BaseException:
        rollback_errors = []
        for path in reversed(ordered):
            key = os.path.normcase(os.path.abspath(path))
            prior = snapshots[key]
            try:
                if prior is None:
                    if path.exists():
                        path.unlink()
                else:
                    atomic_write_bytes(path, prior)
            except Exception as exc:  # retain the original failure
                rollback_errors.append(f"{path}: {exc}")
        if rollback_errors:
            raise RuntimeError(
                "state rollback failed: " + "; ".join(rollback_errors)
            )
        raise
