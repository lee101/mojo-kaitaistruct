from __future__ import annotations

import ctypes
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.environ.get("MOJO_KAITAISTRUCT_LIB") or os.path.join(
    ROOT, "dist", "libmojo-kaitaistruct.so"
)
SRC = os.path.join(ROOT, "src", "kaitaistruct.mojo")

I = ctypes.c_int64
_SIGNATURES = {
    "mks_xor_one": ([I, I, I, I], None),
    "mks_xor_many": ([I, I, I, I, I], None),
    "mks_rotate_left": ([I, I, I, I], None),
}

_pybytes_as_string = ctypes.pythonapi.PyBytes_AsString
_pybytes_as_string.argtypes = [ctypes.py_object]
_pybytes_as_string.restype = ctypes.c_void_p
_pybytes_from_string_and_size = ctypes.pythonapi.PyBytes_FromStringAndSize
_pybytes_from_string_and_size.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t]
_pybytes_from_string_and_size.restype = ctypes.py_object


class BuildError(RuntimeError):
    pass


def build(force: bool = False) -> str:
    if os.environ.get("MOJO_KAITAISTRUCT_LIB") and os.path.exists(LIB) and not force:
        return LIB
    if not force and os.path.exists(LIB) and os.path.getmtime(LIB) >= os.path.getmtime(SRC):
        return LIB
    pixi = shutil.which("pixi")
    if not pixi:
        raise BuildError("shared library is missing; run `pixi run build`")
    proc = subprocess.run(
        [pixi, "run", "--manifest-path", os.path.join(ROOT, "pixi.toml"), "build"],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode or not os.path.exists(LIB):
        raise BuildError((proc.stderr or proc.stdout).strip()[:4000])
    return LIB


_library = None


def lib() -> ctypes.CDLL:
    global _library
    if _library is None:
        _library = ctypes.CDLL(build())
        for name, (argtypes, restype) in _SIGNATURES.items():
            function = getattr(_library, name)
            function.argtypes = argtypes
            function.restype = restype
    return _library


def address(buffer) -> int:
    return ctypes.addressof(ctypes.c_uint8.from_buffer(buffer))


def source_buffer(data):
    if isinstance(data, bytes):
        return data, len(data), int(_pybytes_as_string(data)) if data else 0

    try:
        view = memoryview(data)
    except TypeError:
        copied = bytearray(data)
        return copied, len(copied), address(copied) if copied else 0

    if view.itemsize != 1 or view.format != "B":
        raise TypeError(
            "data must be an unsigned-byte buffer with format 'B' and itemsize 1, "
            f"not format {view.format!r} with itemsize {view.itemsize}"
        )

    if view.c_contiguous:
        byte_view = view.cast("B")
        if not byte_view:
            return byte_view, 0, 0
        if not byte_view.readonly:
            return byte_view, byte_view.nbytes, address(byte_view)
        array_interface = getattr(byte_view.obj, "__array_interface__", None)
        if array_interface is not None:
            pointer = int(array_interface["data"][0])
            if pointer:
                return byte_view, byte_view.nbytes, pointer
        if isinstance(byte_view.obj, bytes) and byte_view.nbytes == len(byte_view.obj):
            return byte_view, byte_view.nbytes, int(_pybytes_as_string(byte_view.obj))

    copied = bytearray(view)
    return copied, len(copied), address(copied) if copied else 0


def writable_bytes(size: int):
    result = _pybytes_from_string_and_size(None, size)
    return result, int(_pybytes_as_string(result)) if size else 0
