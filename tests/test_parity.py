import ctypes
import enum
import io
import random
import struct

import numpy as np
import pytest

import kaitaistruct as mojo


NUMERIC_CASES = [
    ("read_s1", "b", -17),
    ("read_s2be", ">h", -12345),
    ("read_s4be", ">i", -123456789),
    ("read_s8be", ">q", -1234567890123456789),
    ("read_s2le", "<h", -12345),
    ("read_s4le", "<i", -123456789),
    ("read_s8le", "<q", -1234567890123456789),
    ("read_u1", "B", 231),
    ("read_u2be", ">H", 54321),
    ("read_u4be", ">I", 3234567890),
    ("read_u8be", ">Q", 12345678901234567890),
    ("read_u2le", "<H", 54321),
    ("read_u4le", "<I", 3234567890),
    ("read_u8le", "<Q", 12345678901234567890),
    ("read_f4be", ">f", 123.25),
    ("read_f8be", ">d", -1.25e100),
    ("read_f4le", "<f", 123.25),
    ("read_f8le", "<d", -1.25e100),
]


@pytest.mark.parametrize("method,fmt,value", NUMERIC_CASES)
def test_numeric_reads_match_upstream(upstream, method, fmt, value):
    data = struct.pack(fmt, value)
    got = getattr(mojo.KaitaiStream(io.BytesIO(data)), method)()
    expected = getattr(upstream.KaitaiStream(io.BytesIO(data)), method)()
    assert got == expected


@pytest.mark.parametrize("method,fmt,value", [
    (name.replace("read_", "write_"), fmt, value) for name, fmt, value in NUMERIC_CASES
])
def test_numeric_writes_match_upstream(upstream, method, fmt, value):
    size = struct.calcsize(fmt)
    ours_io = io.BytesIO(bytes(size))
    ref_io = io.BytesIO(bytes(size))
    getattr(mojo.KaitaiStream(ours_io), method)(value)
    getattr(upstream.KaitaiStream(ref_io), method)(value)
    assert ours_io.getvalue() == ref_io.getvalue()


@pytest.mark.parametrize("endian", ["be", "le"])
def test_random_bit_reads_match_upstream(upstream, endian):
    rng = random.Random(90210)
    for _ in range(40):
        data = rng.randbytes(128)
        widths = [rng.randrange(1, 49) for _ in range(25)]
        ours = mojo.KaitaiStream(io.BytesIO(data))
        reference = upstream.KaitaiStream(io.BytesIO(data))
        for width in widths:
            assert getattr(ours, f"read_bits_int_{endian}")(width) == getattr(
                reference, f"read_bits_int_{endian}"
            )(width)


@pytest.mark.parametrize("endian", ["be", "le"])
def test_random_bit_writes_match_upstream(upstream, endian):
    rng = random.Random(1776)
    for _ in range(30):
        operations = [
            (width := rng.randrange(1, 49), rng.randrange(1 << width))
            for _ in range(20)
        ]
        ours_io = io.BytesIO(bytes(256))
        ref_io = io.BytesIO(bytes(256))
        ours = mojo.KaitaiStream(ours_io)
        reference = upstream.KaitaiStream(ref_io)
        for width, value in operations:
            getattr(ours, f"write_bits_int_{endian}")(width, value)
            getattr(reference, f"write_bits_int_{endian}")(width, value)
        ours.write_align_to_byte()
        reference.write_align_to_byte()
        assert ours_io.getvalue() == ref_io.getvalue()


@pytest.mark.parametrize(
    "size", [0, 1, 2, 7, 63, 64, 65, 127, 128, 129, 4097, 1_000_003]
)
def test_xor_one_matches_upstream(upstream, size):
    data = random.Random(size).randbytes(size)
    for key in (0, 1, 0x5A, 0xFF):
        assert mojo.KaitaiStream.process_xor_one(
            data, key
        ) == upstream.KaitaiStream.process_xor_one(data, key)


@pytest.mark.parametrize("key", [b"", b"x", b"Kaitai", bytes(range(251))])
def test_xor_many_matches_upstream(upstream, key):
    data = random.Random(11).randbytes(100_003)
    assert mojo.KaitaiStream.process_xor_many(
        data, key
    ) == upstream.KaitaiStream.process_xor_many(data, key)


@pytest.mark.parametrize("amount", [0, 1, 4, 7, 8, 9, 16])
def test_rotate_left_matches_upstream(upstream, amount):
    data = random.Random(12).randbytes(100_003)
    assert mojo.KaitaiStream.process_rotate_left(
        data, amount, 1
    ) == upstream.KaitaiStream.process_rotate_left(data, amount, 1)


def test_contiguous_numpy_input_is_zero_copy_across_ffi(monkeypatch):
    base = np.arange(68, dtype=np.uint8)
    data = base[1:]
    data.flags.writeable = False
    captured = {}

    class FakeLibrary:
        @staticmethod
        def mks_xor_one(source_addr, size, key, result_addr):
            captured["source_addr"] = source_addr
            source = (ctypes.c_uint8 * size).from_address(source_addr)
            result = (ctypes.c_uint8 * size).from_address(result_addr)
            for index in range(size):
                result[index] = source[index] ^ key

    monkeypatch.setattr(mojo, "lib", lambda: FakeLibrary())
    result = mojo.KaitaiStream.process_xor_one(data, 0x5A)

    assert captured["source_addr"] == data.ctypes.data
    assert result == bytes(value ^ 0x5A for value in data)
    assert isinstance(result, bytes)


def test_writable_contiguous_numpy_input_stays_alive_during_ffi(monkeypatch):
    data = np.arange(65, dtype=np.uint8)
    captured = {}

    class FakeLibrary:
        @staticmethod
        def mks_xor_one(source_addr, size, key, result_addr):
            captured["source_addr"] = source_addr
            source = (ctypes.c_uint8 * size).from_address(source_addr)
            result = (ctypes.c_uint8 * size).from_address(result_addr)
            for index in range(size):
                result[index] = source[index] ^ key

    monkeypatch.setattr(mojo, "lib", lambda: FakeLibrary())
    result = mojo.KaitaiStream.process_xor_one(data, 0xA5)

    assert captured["source_addr"] == data.ctypes.data
    assert result == bytes(value ^ 0xA5 for value in data)


def test_noncontiguous_byte_buffer_is_copied_safely():
    data = np.arange(200, dtype=np.uint8)[::2]
    assert mojo.KaitaiStream.process_xor_one(data, 0x5A) == bytes(
        value ^ 0x5A for value in data
    )


@pytest.mark.parametrize("dtype", [np.uint16, np.int32, np.float64])
@pytest.mark.parametrize("method,args", [
    ("process_xor_one", (1,)),
    ("process_xor_many", (b"key",)),
    ("process_xor_many", (np.arange(3, dtype=np.uint16),)),
    ("process_rotate_left", (1, 1)),
])
def test_non_byte_numpy_dtype_is_rejected_instead_of_narrowed(
    dtype, method, args
):
    data = np.arange(8, dtype=dtype)
    with pytest.raises(TypeError, match="itemsize 1"):
        getattr(mojo.KaitaiStream, method)(data, *args)


def test_signed_byte_numpy_dtype_is_rejected():
    with pytest.raises(TypeError, match="unsigned-byte"):
        mojo.KaitaiStream.process_xor_one(np.arange(8, dtype=np.int8), 1)


def test_byte_iterables_remain_supported(upstream):
    data = [1, 2, 3, 255]
    assert mojo.KaitaiStream.process_xor_one(
        data, 0x5A
    ) == upstream.KaitaiStream.process_xor_one(data, 0x5A)


def test_large_failed_read_matches_upstream_position(upstream):
    requested = 8 * 1024 * 1024
    data = b"short"
    ours = mojo.KaitaiStream(io.BytesIO(data))
    reference = upstream.KaitaiStream(io.BytesIO(data))
    for stream, module in ((ours, mojo), (reference, upstream)):
        with pytest.raises(module.EndOfStreamError):
            stream.read_bytes(requested)
    assert ours.pos() == reference.pos() == 0


def test_huge_rotate_amount_fails_before_ffi(upstream):
    data = bytes(range(16))
    amount = 1 << 100
    for module in (mojo, upstream):
        with pytest.raises(OverflowError):
            module.KaitaiStream.process_rotate_left(data, amount, 1)


@pytest.mark.parametrize(
    "method,args",
    [
        ("bytes_strip_right", (b"abc\x00\x00", 0)),
        ("bytes_terminate", (b"abc\x00tail", 0, False)),
        ("bytes_terminate", (b"abc\x00tail", 0, True)),
        ("bytes_terminate_multi", (b"ab\x00\x00cd", b"\x00\x00", False)),
        ("bytes_terminate_multi", (b"a\x00\x00b\x00", b"\x00\x00", True)),
    ],
)
def test_byte_array_helpers_match_upstream(upstream, method, args):
    assert getattr(mojo.KaitaiStream, method)(*args) == getattr(
        upstream.KaitaiStream, method
    )(*args)


@pytest.mark.parametrize(
    "multi,include,consume,eos_error",
    [
        (False, False, False, False),
        (False, True, True, True),
        (True, False, True, False),
        (True, True, False, True),
    ],
)
def test_terminated_reads_match_upstream(
    upstream, multi, include, consume, eos_error
):
    term = b"\x00\xff" if multi else 0
    data = b"ab\x00\xfftail" if multi else b"abc\x00tail"
    method = "read_bytes_term_multi" if multi else "read_bytes_term"
    ours = mojo.KaitaiStream(io.BytesIO(data))
    reference = upstream.KaitaiStream(io.BytesIO(data))
    args = (term, include, consume, eos_error)
    assert getattr(ours, method)(*args) == getattr(reference, method)(*args)
    assert ours.pos() == reference.pos()


def test_eof_error_attributes_match_upstream(upstream):
    for module in (mojo, upstream):
        with pytest.raises(module.EndOfStreamError) as caught:
            module.KaitaiStream(io.BytesIO(b"abc")).read_bytes(4)
        assert caught.value.bytes_needed == 4
        assert caught.value.bytes_available == 3
        assert str(caught.value) == "requested 4 bytes, but only 3 bytes available"


def test_position_seek_size_and_eof_match_upstream(upstream):
    for module in (mojo, upstream):
        stream = module.KaitaiStream(io.BytesIO(b"abcdef"))
        assert (stream.pos(), stream.size(), stream.is_eof()) == (0, 6, False)
        stream.seek(6)
        assert stream.is_eof()
        with pytest.raises(module.InvalidArgumentError):
            stream.seek(-1)


def test_construction_context_and_full_reads(tmp_path):
    path = tmp_path / "sample.bin"
    path.write_bytes(b"abcdef")
    with mojo.KaitaiStruct.from_file(path) as parsed:
        assert parsed._io.read_bytes(2) == b"ab"
        assert parsed._io.read_bytes_full() == b"cdef"
    assert parsed._io._io.closed

    with mojo.KaitaiStream(io.BytesIO(b"xyz")) as stream:
        assert stream.read_bytes_full() == b"xyz"
    assert stream._io.closed

    assert mojo.KaitaiStruct.from_bytes(b"a")._io.read_u1() == ord("a")
    assert mojo.KaitaiStruct.from_io(io.BytesIO(b"b"))._io.read_u1() == ord("b")


def test_read_write_dirty_state_and_child_write_back():
    class Writable(mojo.ReadWriteKaitaiStruct):
        def _fetch_instances(self):
            self.fetched = True

        def _write__seq(self, io=None):
            super()._write__seq(io)

    value = Writable(mojo.KaitaiStream(io.BytesIO(b"\0")))
    with pytest.raises(mojo.ConsistencyNotCheckedError):
        value._write()
    value._dirty = False
    value._write()
    assert value.fetched

    parent_io = io.BytesIO(b"\0\0")
    parent = mojo.KaitaiStream(parent_io)
    child = mojo.KaitaiStream(io.BytesIO(b"\0"))
    child.write_back_handler = mojo.KaitaiStream.WriteBackHandler(
        1, lambda target: target.write_u1(0xA5)
    )
    parent.add_child_stream(child)
    parent.write_back_child_streams()
    assert parent_io.getvalue() == b"\0\xa5"
    assert parent.child_streams == []


def test_remaining_byte_array_helpers():
    data = b"\x05\x02\x09"
    assert mojo.KaitaiStream.int_from_byte(7) == 7
    assert mojo.KaitaiStream.byte_from_int(7) == b"\x07"
    assert mojo.KaitaiStream.byte_array_index(data, 1) == 2
    assert mojo.KaitaiStream.byte_array_min(data) == 2
    assert mojo.KaitaiStream.byte_array_max(data) == 9
    assert mojo.KaitaiStream.byte_array_index_of(data, 9) == 2
    stream = mojo.KaitaiStream(io.BytesIO(data))
    stream.seek(1)
    assert stream.to_byte_array() == data
    assert stream.pos() == 1


def test_write_bytes_limit_matches_upstream(upstream):
    ours_io = io.BytesIO(bytes(12))
    ref_io = io.BytesIO(bytes(12))
    mojo.KaitaiStream(ours_io).write_bytes_limit(b"abc", 8, 0, 0x20)
    upstream.KaitaiStream(ref_io).write_bytes_limit(b"abc", 8, 0, 0x20)
    assert ours_io.getvalue() == ref_io.getvalue()


def test_resolve_enum_matches_upstream(upstream):
    class Kind(enum.IntEnum):
        A = 1

    for value in (1, 99):
        assert mojo.KaitaiStream.resolve_enum(
            Kind, value
        ) == upstream.KaitaiStream.resolve_enum(Kind, value)


def test_validation_errors_match_upstream(upstream):
    for name, args in [
        ("ValidationNotEqualError", (1, 2, None, "/seq/0")),
        ("ValidationLessThanError", (1, 0, None, "/seq/0")),
        ("ValidationGreaterThanError", (2, 3, None, "/seq/0")),
        ("ValidationNotAnyOfError", (3, None, "/seq/0")),
        ("ValidationNotInEnumError", (3, None, "/seq/0")),
        ("ValidationExprError", (3, None, "/seq/0")),
    ]:
        ours = getattr(mojo, name)(*args)
        reference = getattr(upstream, name)(*args)
        assert str(ours) == str(reference)
        assert ours.src_path == reference.src_path


def test_public_exception_hierarchy_and_metadata():
    assert issubclass(mojo.InvalidArgumentError, ValueError)
    assert issubclass(mojo.EndOfStreamError, EOFError)
    assert issubclass(mojo.NoTerminatorFoundError, mojo.EndOfStreamError)
    assert issubclass(mojo.ValidationFailedError, mojo.KaitaiStructError)
    undecided = mojo.UndecidedEndiannessError("/type")
    assert undecided.src_path == "/type"
    consistency = mojo.ConsistencyError("field", 1, 2)
    assert (consistency.id, consistency.expected, consistency.actual) == (
        "field",
        1,
        2,
    )
