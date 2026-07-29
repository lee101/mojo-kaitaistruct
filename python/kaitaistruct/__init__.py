from __future__ import annotations

import ctypes
import itertools
import struct
import warnings
from io import BytesIO, SEEK_CUR, SEEK_END, open

from ._lib import lib, source_buffer, writable_bytes

__version__ = "0.11"
API_VERSION = (0, 11)
PY2 = False


class KaitaiStruct:
    def __init__(self, io):
        self._io = io

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        self.close()

    def close(self):
        self._io.close()

    @classmethod
    def from_file(cls, filename):
        stream = open(filename, "rb")
        try:
            return cls(KaitaiStream(stream))
        except Exception:
            stream.close()
            raise

    @classmethod
    def from_bytes(cls, buf):
        return cls(KaitaiStream(BytesIO(buf)))

    @classmethod
    def from_io(cls, io):
        return cls(KaitaiStream(io))


class ReadWriteKaitaiStruct(KaitaiStruct):
    def __init__(self, io):
        super().__init__(io)
        self._dirty = True

    def _fetch_instances(self):
        raise NotImplementedError()

    def _write(self, io=None):
        self._write__seq(io)
        self._fetch_instances()
        self._io.write_back_child_streams()

    def _write__seq(self, io):
        if io is not None:
            self._io = io
        if self._dirty:
            raise ConsistencyNotCheckedError(
                "consistency not checked: _check() has not been called since the last modification of the object"
            )

    def __setattr__(self, key, value):
        parent_setattr = super().__setattr__
        if not key.startswith("_") or key in {"_parent", "_root"} or key.startswith("_unnamed"):
            parent_setattr("_dirty", True)
        parent_setattr(key, value)


class KaitaiStream:
    packer_s1 = struct.Struct("b")
    packer_s2be = struct.Struct(">h")
    packer_s4be = struct.Struct(">i")
    packer_s8be = struct.Struct(">q")
    packer_s2le = struct.Struct("<h")
    packer_s4le = struct.Struct("<i")
    packer_s8le = struct.Struct("<q")
    packer_u1 = struct.Struct("B")
    packer_u2be = struct.Struct(">H")
    packer_u4be = struct.Struct(">I")
    packer_u8be = struct.Struct(">Q")
    packer_u2le = struct.Struct("<H")
    packer_u4le = struct.Struct("<I")
    packer_u8le = struct.Struct("<Q")
    packer_f4be = struct.Struct(">f")
    packer_f8be = struct.Struct(">d")
    packer_f4le = struct.Struct("<f")
    packer_f8le = struct.Struct("<d")

    def __init__(self, io):
        self._io = io
        self.align_to_byte()
        self.bits_le = False
        self.bits_write_mode = False
        self.write_back_handler = None
        self.child_streams = []
        try:
            self._size = self.size()
        except (OSError, IOError, ValueError):
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        self.close()

    def close(self):
        try:
            if self.bits_write_mode:
                self.write_align_to_byte()
            else:
                self.align_to_byte()
        finally:
            self._io.close()

    def is_eof(self):
        if not self.bits_write_mode and self.bits_left > 0:
            return False
        return self._io.tell() >= self.size()

    def seek(self, n):
        if n < 0:
            raise InvalidArgumentError("cannot seek to invalid position %d" % n)
        if self.bits_write_mode:
            self.write_align_to_byte()
        else:
            self.align_to_byte()
        self._io.seek(n)

    def pos(self):
        return self._io.tell() + (1 if self.bits_write_mode and self.bits_left > 0 else 0)

    def size(self):
        current = self._io.tell()
        full_size = self._io.seek(0, SEEK_END)
        if full_size is None:
            full_size = self._io.tell()
        self._io.seek(current)
        return full_size

    def read_s1(self):
        return self.packer_s1.unpack(self.read_bytes(1))[0]

    def read_s2be(self):
        return self.packer_s2be.unpack(self.read_bytes(2))[0]

    def read_s4be(self):
        return self.packer_s4be.unpack(self.read_bytes(4))[0]

    def read_s8be(self):
        return self.packer_s8be.unpack(self.read_bytes(8))[0]

    def read_s2le(self):
        return self.packer_s2le.unpack(self.read_bytes(2))[0]

    def read_s4le(self):
        return self.packer_s4le.unpack(self.read_bytes(4))[0]

    def read_s8le(self):
        return self.packer_s8le.unpack(self.read_bytes(8))[0]

    def read_u1(self):
        return self.packer_u1.unpack(self.read_bytes(1))[0]

    def read_u2be(self):
        return self.packer_u2be.unpack(self.read_bytes(2))[0]

    def read_u4be(self):
        return self.packer_u4be.unpack(self.read_bytes(4))[0]

    def read_u8be(self):
        return self.packer_u8be.unpack(self.read_bytes(8))[0]

    def read_u2le(self):
        return self.packer_u2le.unpack(self.read_bytes(2))[0]

    def read_u4le(self):
        return self.packer_u4le.unpack(self.read_bytes(4))[0]

    def read_u8le(self):
        return self.packer_u8le.unpack(self.read_bytes(8))[0]

    def read_f4be(self):
        return self.packer_f4be.unpack(self.read_bytes(4))[0]

    def read_f8be(self):
        return self.packer_f8be.unpack(self.read_bytes(8))[0]

    def read_f4le(self):
        return self.packer_f4le.unpack(self.read_bytes(4))[0]

    def read_f8le(self):
        return self.packer_f8le.unpack(self.read_bytes(8))[0]

    def align_to_byte(self):
        self.bits_left = 0
        self.bits = 0

    def read_bits_int_be(self, n):
        self.bits_write_mode = False
        result = 0
        bits_needed = n - self.bits_left
        self.bits_left = -bits_needed % 8
        if bits_needed > 0:
            bytes_needed = ((bits_needed - 1) // 8) + 1
            buf = self._read_bytes_not_aligned(bytes_needed)
            for byte in buf:
                result = result << 8 | byte
            new_bits = result
            result = result >> self.bits_left | self.bits << bits_needed
            self.bits = new_bits
        else:
            result = self.bits >> -bits_needed
        self.bits &= (1 << self.bits_left) - 1
        return result

    def read_bits_int(self, n):
        warnings.warn(
            "read_bits_int() is deprecated since 0.9, use read_bits_int_be() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.read_bits_int_be(n)

    def read_bits_int_le(self, n):
        self.bits_write_mode = False
        result = 0
        bits_needed = n - self.bits_left
        if bits_needed > 0:
            bytes_needed = ((bits_needed - 1) // 8) + 1
            buf = self._read_bytes_not_aligned(bytes_needed)
            for i, byte in enumerate(buf):
                result |= byte << (i * 8)
            new_bits = result >> bits_needed
            result = result << self.bits_left | self.bits
            self.bits = new_bits
        else:
            result = self.bits
            self.bits >>= n
        self.bits_left = -bits_needed % 8
        return result & ((1 << n) - 1)

    def read_bytes(self, n):
        self.align_to_byte()
        return self._read_bytes_not_aligned(n)

    def _read_bytes_not_aligned(self, n):
        if n < 0:
            raise InvalidArgumentError("requested invalid %d amount of bytes" % n)
        satisfiable = True
        if (
            n >= 8 * 1024 * 1024
            and callable(getattr(self._io, "seekable", None))
            and self._io.seekable()
        ):
            available = self.size() - self.pos()
            satisfiable = n <= available
        if satisfiable:
            result = self._io.read(n)
            available = len(result)
            satisfiable = n <= available
        if not satisfiable:
            raise EndOfStreamError(
                "requested %d bytes, but only %d bytes available" % (n, available),
                n,
                available,
            )
        return result

    def read_bytes_full(self):
        self.align_to_byte()
        return self._io.read()

    def read_bytes_term(self, term, include_term, consume_term, eos_error):
        self.align_to_byte()
        term_byte = self.byte_from_int(term)
        result = bytearray()
        while True:
            current = self._io.read(1)
            if not current:
                if eos_error:
                    raise NoTerminatorFoundError(term_byte, 0)
                return bytes(result)
            if current == term_byte:
                if include_term:
                    result += current
                if not consume_term:
                    self._io.seek(-1, SEEK_CUR)
                return bytes(result)
            result += current

    def read_bytes_term_multi(self, term, include_term, consume_term, eos_error):
        self.align_to_byte()
        unit_size = len(term)
        result = bytearray()
        while True:
            current = self._io.read(unit_size)
            if len(current) < unit_size:
                if eos_error:
                    raise NoTerminatorFoundError(term, len(current))
                result += current
                return bytes(result)
            if current == term:
                if include_term:
                    result += current
                if not consume_term:
                    self._io.seek(-unit_size, SEEK_CUR)
                return bytes(result)
            result += current

    def ensure_fixed_contents(self, expected):
        warnings.warn(
            "ensure_fixed_contents() is deprecated since 0.9, explicitly raise ValidationNotEqualError from an if statement instead",
            DeprecationWarning,
            stacklevel=2,
        )
        actual = self._io.read(len(expected))
        if actual != expected:
            raise Exception(
                "unexpected fixed contents: got %r, was waiting for %r" % (actual, expected)
            )
        return actual

    @staticmethod
    def bytes_strip_right(data, pad_byte):
        return data.rstrip(KaitaiStream.byte_from_int(pad_byte))

    @staticmethod
    def bytes_terminate(data, term, include_term):
        index = KaitaiStream.byte_array_index_of(data, term)
        if index == -1:
            return data[:]
        return data[: index + (1 if include_term else 0)]

    @staticmethod
    def bytes_terminate_multi(data, term, include_term):
        unit_size = len(term)
        index = data.find(term)
        while True:
            if index == -1:
                return data[:]
            mod = index % unit_size
            if mod == 0:
                return data[: index + (unit_size if include_term else 0)]
            index = data.find(term, index + (unit_size - mod))

    def _ensure_bytes_left_to_write(self, n, pos):
        try:
            full_size = self._size
        except AttributeError:
            raise ValueError("writing to non-seekable streams is not supported")
        available = full_size - pos
        if n > available:
            raise EndOfStreamError(
                "requested to write %d bytes, but only %d bytes left in the stream"
                % (n, available),
                n,
                available,
            )

    def write_s1(self, v):
        self.write_bytes(self.packer_s1.pack(v))

    def write_s2be(self, v):
        self.write_bytes(self.packer_s2be.pack(v))

    def write_s4be(self, v):
        self.write_bytes(self.packer_s4be.pack(v))

    def write_s8be(self, v):
        self.write_bytes(self.packer_s8be.pack(v))

    def write_s2le(self, v):
        self.write_bytes(self.packer_s2le.pack(v))

    def write_s4le(self, v):
        self.write_bytes(self.packer_s4le.pack(v))

    def write_s8le(self, v):
        self.write_bytes(self.packer_s8le.pack(v))

    def write_u1(self, v):
        self.write_bytes(self.packer_u1.pack(v))

    def write_u2be(self, v):
        self.write_bytes(self.packer_u2be.pack(v))

    def write_u4be(self, v):
        self.write_bytes(self.packer_u4be.pack(v))

    def write_u8be(self, v):
        self.write_bytes(self.packer_u8be.pack(v))

    def write_u2le(self, v):
        self.write_bytes(self.packer_u2le.pack(v))

    def write_u4le(self, v):
        self.write_bytes(self.packer_u4le.pack(v))

    def write_u8le(self, v):
        self.write_bytes(self.packer_u8le.pack(v))

    def write_f4be(self, v):
        self.write_bytes(self.packer_f4be.pack(v))

    def write_f8be(self, v):
        self.write_bytes(self.packer_f8be.pack(v))

    def write_f4le(self, v):
        self.write_bytes(self.packer_f4le.pack(v))

    def write_f8le(self, v):
        self.write_bytes(self.packer_f8le.pack(v))

    def write_align_to_byte(self):
        if self.bits_left > 0:
            value = self.bits
            if not self.bits_le:
                value <<= 8 - self.bits_left
            self.align_to_byte()
            self._write_bytes_not_aligned(self.byte_from_int(value))

    def write_bits_int_be(self, n, val):
        self.bits_le = False
        self.bits_write_mode = True
        val &= (1 << n) - 1
        bits_to_write = self.bits_left + n
        bytes_needed = ((bits_to_write - 1) // 8) + 1
        self._ensure_bytes_left_to_write(
            bytes_needed - (1 if self.bits_left > 0 else 0), self.pos()
        )
        bytes_to_write = bits_to_write // 8
        self.bits_left = bits_to_write % 8
        if bytes_to_write > 0:
            buf = bytearray(bytes_to_write)
            new_bits = val & ((1 << self.bits_left) - 1)
            val = val >> self.bits_left | self.bits << (n - self.bits_left)
            self.bits = new_bits
            for i in range(bytes_to_write - 1, -1, -1):
                buf[i] = val & 0xFF
                val >>= 8
            self._write_bytes_not_aligned(buf)
        else:
            self.bits = self.bits << n | val

    def write_bits_int_le(self, n, val):
        self.bits_le = True
        self.bits_write_mode = True
        bits_to_write = self.bits_left + n
        bytes_needed = ((bits_to_write - 1) // 8) + 1
        self._ensure_bytes_left_to_write(
            bytes_needed - (1 if self.bits_left > 0 else 0), self.pos()
        )
        bytes_to_write = bits_to_write // 8
        old_bits_left = self.bits_left
        self.bits_left = bits_to_write % 8
        if bytes_to_write > 0:
            buf = bytearray(bytes_to_write)
            new_bits = val >> (n - self.bits_left)
            val = val << old_bits_left | self.bits
            self.bits = new_bits
            for i in range(bytes_to_write):
                buf[i] = val & 0xFF
                val >>= 8
            self._write_bytes_not_aligned(buf)
        else:
            self.bits |= val << old_bits_left
        self.bits &= (1 << self.bits_left) - 1

    def write_bytes(self, buf):
        self.write_align_to_byte()
        self._write_bytes_not_aligned(buf)

    def _write_bytes_not_aligned(self, buf):
        self._ensure_bytes_left_to_write(len(buf), self._io.tell())
        self._io.write(buf)

    def write_bytes_limit(self, buf, size, term, pad_byte):
        n = len(buf)
        assert n <= size, "writing %d bytes, but %d bytes were given" % (size, n)
        self.write_bytes(buf)
        if n < size:
            self.write_u1(term)
            self.write_bytes(self.byte_from_int(pad_byte) * (size - n - 1))

    @staticmethod
    def process_xor_one(data, key):
        if not isinstance(key, int) or not 0 <= key <= 0xFF:
            return bytes(value ^ key for value in data)
        source, size, source_addr = source_buffer(data)
        if not size:
            return b""
        result, result_addr = writable_bytes(size)
        lib().mks_xor_one(source_addr, size, key, result_addr)
        return result

    @staticmethod
    def process_xor_many(data, key):
        source, size, source_addr = source_buffer(data)
        key_buf, key_size, key_addr = source_buffer(key)
        if not size or not key_size:
            return b""
        result, result_addr = writable_bytes(size)
        lib().mks_xor_many(
            source_addr, size, key_addr, key_size, result_addr
        )
        return result

    @staticmethod
    def process_rotate_left(data, amount, group_size):
        if group_size != 1:
            raise NotImplementedError("unable to rotate group of %d bytes yet" % group_size)
        if amount < 0:
            raise ValueError("negative shift count")
        if amount > (1 << 63) - 1:
            raise OverflowError("rotation amount does not fit the Mojo Int ABI")
        source, size, source_addr = source_buffer(data)
        if not size:
            return b""
        result, result_addr = writable_bytes(size)
        lib().mks_rotate_left(source_addr, size, amount, result_addr)
        return result

    @staticmethod
    def int_from_byte(v):
        return v

    @staticmethod
    def byte_from_int(i):
        return bytes((i,))

    @staticmethod
    def byte_array_index(data, i):
        return data[i]

    @staticmethod
    def byte_array_min(b):
        return min(b)

    @staticmethod
    def byte_array_max(b):
        return max(b)

    @staticmethod
    def byte_array_index_of(data, b):
        return data.find(KaitaiStream.byte_from_int(b))

    @staticmethod
    def resolve_enum(enum_obj, value):
        try:
            return enum_obj(value)
        except ValueError:
            return value

    def to_byte_array(self):
        position = self.pos()
        self.seek(0)
        result = self.read_bytes_full()
        self.seek(position)
        return result

    class WriteBackHandler:
        def __init__(self, pos, handler):
            self.pos = pos
            self.handler = handler

        def write_back(self, parent):
            parent.seek(self.pos)
            self.handler(parent)

    def add_child_stream(self, child):
        self.child_streams.append(child)

    def write_back_child_streams(self, parent=None):
        position = self.pos()
        for child in self.child_streams:
            child.write_back_child_streams(self)
        del self.child_streams[:]
        self.seek(position)
        if parent is not None:
            self._write_back(parent)

    def _write_back(self, parent):
        self.write_back_handler.write_back(parent)


class KaitaiStructError(Exception):
    def __init__(self, msg, src_path):
        super().__init__(("" if src_path is None else src_path + ": ") + msg)
        self.src_path = src_path


class InvalidArgumentError(KaitaiStructError, ValueError):
    def __init__(self, msg):
        super().__init__(msg, None)


class EndOfStreamError(KaitaiStructError, EOFError):
    def __init__(self, msg, bytes_needed, bytes_available):
        super().__init__(msg, None)
        self.bytes_needed = bytes_needed
        self.bytes_available = bytes_available


class NoTerminatorFoundError(EndOfStreamError):
    def __init__(self, term, bytes_available):
        super().__init__(
            "end of stream reached, but no terminator %r found" % term,
            len(term),
            bytes_available,
        )
        self.term = term


class UndecidedEndiannessError(KaitaiStructError):
    def __init__(self, src_path):
        super().__init__("unable to decide on endianness for a type", src_path)


class ValidationFailedError(KaitaiStructError):
    def __init__(self, msg, io, src_path):
        super().__init__(
            ("" if io is None else "at pos %d: " % io.pos())
            + "validation failed: "
            + msg,
            src_path,
        )
        self.io = io


class ValidationNotEqualError(ValidationFailedError):
    def __init__(self, expected, actual, io, src_path):
        super().__init__(
            "not equal, expected %s, but got %s" % (repr(expected), repr(actual)),
            io,
            src_path,
        )
        self.expected = expected
        self.actual = actual


class ValidationLessThanError(ValidationFailedError):
    def __init__(self, min_bound, actual, io, src_path):
        super().__init__(
            "not in range, min %s, but got %s" % (repr(min_bound), repr(actual)),
            io,
            src_path,
        )
        self.min = min_bound
        self.actual = actual


class ValidationGreaterThanError(ValidationFailedError):
    def __init__(self, max_bound, actual, io, src_path):
        super().__init__(
            "not in range, max %s, but got %s" % (repr(max_bound), repr(actual)),
            io,
            src_path,
        )
        self.max = max_bound
        self.actual = actual


class ValidationNotAnyOfError(ValidationFailedError):
    def __init__(self, actual, io, src_path):
        super().__init__("not any of the list, got %s" % repr(actual), io, src_path)
        self.actual = actual


class ValidationNotInEnumError(ValidationFailedError):
    def __init__(self, actual, io, src_path):
        super().__init__("not in the enum, got %s" % repr(actual), io, src_path)
        self.actual = actual


class ValidationExprError(ValidationFailedError):
    def __init__(self, actual, io, src_path):
        super().__init__("not matching the expression, got %s" % repr(actual), io, src_path)
        self.actual = actual


class ConsistencyError(Exception):
    def __init__(self, attr_id, expected, actual):
        super().__init__(
            "Check failed: %s, expected: %s, actual: %s"
            % (attr_id, repr(expected), repr(actual))
        )
        self.id = attr_id
        self.expected = expected
        self.actual = actual


class ConsistencyNotCheckedError(Exception):
    pass
