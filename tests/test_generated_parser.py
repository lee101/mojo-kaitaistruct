import struct

import kaitaistruct


class DemoPacket(kaitaistruct.KaitaiStruct):
    def __init__(self, io, _parent=None, _root=None):
        super().__init__(io)
        self._parent = _parent
        self._root = _root or self
        self._read()

    def _read(self):
        self.magic = self._io.read_bytes(4)
        if self.magic != b"MKS1":
            raise kaitaistruct.ValidationNotEqualError(
                b"MKS1", self.magic, self._io, "/seq/0"
            )
        self.version = self._io.read_u2le()
        self.flags = self._io.read_bits_int_be(3)
        self.kind = self._io.read_bits_int_be(5)
        length = self._io.read_u4be()
        key = self._io.read_u1()
        self.payload = kaitaistruct.KaitaiStream.process_xor_one(
            self._io.read_bytes(length), key
        )


def test_generated_style_parser_runs_with_drop_in_runtime():
    payload = b"a realistic binary payload" * 2000
    key = 0xA7
    encoded = kaitaistruct.KaitaiStream.process_xor_one(payload, key)
    packet = (
        b"MKS1"
        + struct.pack("<H", 11)
        + bytes([(5 << 5) | 17])
        + struct.pack(">I", len(encoded))
        + bytes([key])
        + encoded
    )
    parsed = DemoPacket.from_bytes(packet)
    assert parsed.version == 11
    assert (parsed.flags, parsed.kind) == (5, 17)
    assert parsed.payload == payload


def test_generated_style_validation_error_has_source_path():
    packet = b"BAD!" + bytes(20)
    try:
        DemoPacket.from_bytes(packet)
    except kaitaistruct.ValidationNotEqualError as error:
        assert error.src_path == "/seq/0"
        assert "/seq/0: at pos 4: validation failed" in str(error)
    else:
        raise AssertionError("invalid magic was accepted")
