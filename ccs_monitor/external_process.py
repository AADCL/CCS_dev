"""Keep frozen-library search paths out of system executables."""
from __future__ import annotations
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import threading
from .runtime_paths import is_frozen, resource_root

_dll_lock = threading.RLock()

def external_environment() -> dict[str, str]:
    environment = dict(os.environ)
    if not is_frozen():
        return environment
    if sys.platform != "win32":
        if "LD_LIBRARY_PATH_ORIG" in environment:
            environment["LD_LIBRARY_PATH"] = environment["LD_LIBRARY_PATH_ORIG"]
        else:
            environment.pop("LD_LIBRARY_PATH", None)
    root = resource_root().resolve()
    entries = []
    for entry in environment.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            Path(entry).resolve().relative_to(root)
        except ValueError:
            entries.append(entry)
    environment["PATH"] = os.pathsep.join(entries)
    return environment

@contextmanager
def external_dll_search():
    with _dll_lock:
        if not is_frozen() or sys.platform != "win32":
            yield
            return
        import ctypes
        kernel = ctypes.windll.kernel32
        buffer = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(buffer), buffer)
        kernel.SetDllDirectoryW(None)
        try:
            yield
        finally:
            kernel.SetDllDirectoryW(buffer.value or None)

def start_external_process(process, program: str, arguments: list[str]) -> None:
    from PySide6.QtCore import QProcessEnvironment
    environment = QProcessEnvironment()
    for key, value in external_environment().items():
        environment.insert(key, value)
    process.setProcessEnvironment(environment)
    with external_dll_search():
        process.start(program, arguments)
        if is_frozen() and sys.platform == "win32":
            process.waitForStarted(5000)
