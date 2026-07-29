# mojo-kaitaistruct

A drop-in Python runtime for [Kaitai Struct](https://kaitai.io/) with its
bulk byte transformations implemented in Mojo.

Generated Kaitai Python parsers can continue to use:

```python
from kaitaistruct import KaitaiStruct, KaitaiStream
```

The module implements the public `kaitaistruct` 0.11 API on Python 3. Stream state and Python
IO remain in Python, where crossing an FFI boundary for every scalar would cost
more than `struct.unpack`. The operations that traverse whole binary payloads
run in compiled Mojo: single-byte XOR, repeating-key XOR, and one-byte-group
rotate-left.

## Install

The repository uses Pixi and the pinned Mojo nightly:

```bash
pixi install
pixi run build
pixi run test
```

`kaitaistruct` 0.11 is installed in the development environment solely as the
parity-test and benchmark reference. Put `python/` on `PYTHONPATH` when using
the checkout outside `pixi run`, or package that directory in the normal way.

## Usage

This complete example runs after `pixi install && pixi run build`:

```bash
pixi run python - <<'PY'
from kaitaistruct import KaitaiStruct, KaitaiStream

class Packet(KaitaiStruct):
    def __init__(self, io, _parent=None, _root=None):
        super().__init__(io)
        self.magic = self._io.read_bytes(4)
        self.length = self._io.read_u4le()
        key = self._io.read_u1()
        self.payload = KaitaiStream.process_xor_one(
            self._io.read_bytes(self.length), key
        )

plain = b"binary payload"
key = 0x5A
encoded = KaitaiStream.process_xor_one(plain, key)
packet = Packet.from_bytes(b"MKS1" + len(plain).to_bytes(4, "little") + bytes([key]) + encoded)
assert packet.magic == b"MKS1"
assert packet.payload == plain
print(packet.payload)
PY
```

## Coverage

Covered and parity-tested against the real upstream Python package:

- `KaitaiStruct` and `ReadWriteKaitaiStruct`, including construction helpers,
  context management, dirty-state handling, and child-stream write-back
- `KaitaiStream` positioning, EOF/size queries, and fixed/full/terminated byte reads
- all signed, unsigned, and floating-point scalar reads and writes in both endiannesses
- unaligned big- and little-endian bit reads and writes
- byte stripping/termination helpers, enum resolution, and byte-array helpers
- single-key XOR, repeating-key XOR, and rotate-left processing
- the upstream validation, stream, argument, and consistency exception hierarchy

The test suite includes randomized bit-stream comparisons and a
generated-parser-style integration test. It asserts values, stream positions,
serialized bytes, error messages, and error metadata rather than only checking
that calls complete.

Not covered: Python 2 compatibility and rotations with `group_size` other than
1; upstream itself raises `NotImplementedError` for the latter. Compression,
checksums, format-specific parsing, and `.ksy` compilation are not part of the
Python binary runtime and are outside this repository’s scope.

## Benchmarks

Measured with `pixi run bench` on an Intel Xeon E5-2697 v4 at 2.30 GHz,
Linux 6.8.0-136-generic, Python 3.13.14. Each row uses an 8 MiB input, takes the
best of three complete Python API calls, includes allocation and FFI overhead,
and verifies the result against upstream before printing.

| operation | mojo-kaitaistruct | upstream 0.11 | speedup |
| --- | ---: | ---: | ---: |
| `process_xor_one`, 8 MiB | 2.33 ms | 794.60 ms | 341.76x |
| `process_xor_many`, 8 MiB / 13-byte key | 69.27 ms | 1043.93 ms | 15.07x |
| `process_rotate_left`, 8 MiB | 8.11 ms | 1490.19 ms | 183.75x |

These results compare Mojo loops with upstream’s Python generator expressions.
They do not imply that scalar reads are faster: those intentionally retain the
upstream `struct.Struct` strategy.

No parallel or GPU path is enabled.

## How it works

`build/build.sh` compiles the single `src/kaitaistruct.mojo` compilation unit
with `mojo build --emit shared-lib` into
`dist/libmojo-kaitaistruct.so`. The Python package loads that library with
`ctypes`. Buffers cross the C ABI as 64-bit integer addresses; Mojo reconstructs
`UnsafePointer[UInt8, AnyOrigin[mut=True]]` values internally, so no parametric
pointer type appears in an exported signature.

The caller owns all memory. Python passes `bytes` and contiguous, unsigned-byte
buffer inputs directly, including writable or read-only contiguous NumPy
`uint8` arrays, and allocates one immutable `bytes` result for Mojo to fill
before returning it. Non-contiguous byte buffers are copied; buffers with an
element width other than one byte or a signed/non-integer format are rejected
instead of silently narrowed or reinterpreted. Kernels
allocate nothing, retain no pointers, and write exactly the supplied output
length. Single-key XOR processes machine SIMD widths before a scalar tail;
repeating-key XOR and rotation use tight scalar loops.

This project is MIT licensed. Kaitai Struct is an independent MIT-licensed
project copyright the Kaitai Project; its package is the compatibility target,
not a runtime dependency of the shipped module.
