import importlib.metadata
import importlib.util
import os
import platform
import random
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python"))

import kaitaistruct as mojo  # noqa: E402


def load_upstream():
    path = importlib.metadata.distribution("kaitaistruct").locate_file("kaitaistruct.py")
    spec = importlib.util.spec_from_file_location("_kaitaistruct_benchmark_upstream", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def time_best(function, repetitions=3):
    samples = []
    value = None
    for _ in range(repetitions):
        start = time.perf_counter()
        value = function()
        samples.append(time.perf_counter() - start)
    return min(samples), statistics.median(samples), value


def cpu_name():
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as cpuinfo:
            for line in cpuinfo:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown CPU"


def main():
    upstream = load_upstream()
    size = 8 * 1024 * 1024
    data = random.Random(2026).randbytes(size)
    cases = [
        (
            "process_xor_one, 8 MiB",
            lambda: mojo.KaitaiStream.process_xor_one(data, 0xA7),
            lambda: upstream.KaitaiStream.process_xor_one(data, 0xA7),
        ),
        (
            "process_xor_many, 8 MiB / 13-byte key",
            lambda: mojo.KaitaiStream.process_xor_many(data, b"Kaitai-Mojo!!"),
            lambda: upstream.KaitaiStream.process_xor_many(data, b"Kaitai-Mojo!!"),
        ),
        (
            "process_rotate_left, 8 MiB",
            lambda: mojo.KaitaiStream.process_rotate_left(data, 3, 1),
            lambda: upstream.KaitaiStream.process_rotate_left(data, 3, 1),
        ),
    ]

    mojo.KaitaiStream.process_xor_one(data[:64], 1)
    print(f"Machine: {cpu_name()}; {platform.system()} {platform.release()}; Python {platform.python_version()}")
    print()
    print("| operation | mojo-kaitaistruct | upstream 0.11 | speedup |")
    print("| --- | ---: | ---: | ---: |")
    for name, mojo_fn, upstream_fn in cases:
        mojo_best, _, mojo_value = time_best(mojo_fn)
        upstream_best, _, upstream_value = time_best(upstream_fn)
        if mojo_value != upstream_value:
            raise AssertionError(f"benchmark outputs differ for {name}")
        speedup = upstream_best / mojo_best
        print(
            f"| {name} | {mojo_best * 1000:.2f} ms | "
            f"{upstream_best * 1000:.2f} ms | {speedup:.2f}x |"
        )


if __name__ == "__main__":
    main()
