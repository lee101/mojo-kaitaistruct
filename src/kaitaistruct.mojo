from std.sys import simd_width_of

comptime BPtr = UnsafePointer[UInt8, AnyOrigin[mut=True]]
comptime W = simd_width_of[DType.float64]() * 8


@export("mks_xor_one")
def mks_xor_one(
    src_addr: Int, n: Int, key: Int, dst_addr: Int
) abi("C"):
    var src = BPtr(unsafe_from_address=src_addr)
    var dst = BPtr(unsafe_from_address=dst_addr)
    var key_vec = SIMD[DType.uint8, W](UInt8(key))
    var i = 0
    while i + W <= n:
        dst.store(i, src.load[width=W](i) ^ key_vec)
        i += W
    while i < n:
        dst[i] = src[i] ^ UInt8(key)
        i += 1


@export("mks_xor_many")
def mks_xor_many(
    src_addr: Int, n: Int, key_addr: Int, key_n: Int, dst_addr: Int
) abi("C"):
    var src = BPtr(unsafe_from_address=src_addr)
    var key = BPtr(unsafe_from_address=key_addr)
    var dst = BPtr(unsafe_from_address=dst_addr)
    for i in range(n):
        dst[i] = src[i] ^ key[i % key_n]


@export("mks_rotate_left")
def mks_rotate_left(
    src_addr: Int, n: Int, amount: Int, dst_addr: Int
) abi("C"):
    var src = BPtr(unsafe_from_address=src_addr)
    var dst = BPtr(unsafe_from_address=dst_addr)
    var anti = (8 - (amount % 8)) % 8
    for i in range(n):
        var value = Int(src[i])
        dst[i] = UInt8(((value << amount) & 0xff) | (value >> anti))
