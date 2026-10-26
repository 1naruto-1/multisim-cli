"""Multisim .ms14 容器编解码。

格式：36 字节魔数 + u64 原始总长 + 若干分段（u32 原长、u32 压缩长、PKWare DCL Implode 数据），
每段原长 900000。Multisim 自己用 ASCII 字面量编码、4 KB 字典（头两字节 1, 6），这里也一样。
码表取自 PKWare DCL 格式（与 StormLib / node-pkware 相同）。
"""
import struct

MAGIC = b"MSMCompressedElectronicsWorkbenchXML"
SECTION = 900_000
WINDOW = 4096          # 字典 6 位低位 → 最远距离 4096
MAX_LEN = 518          # 519 是结束码

CH_BITS_ASC = (
    11, 12, 12, 12, 12, 12, 12, 12, 12, 8, 7, 12, 12, 7, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 13, 12, 12, 12, 12, 12, 4, 10, 8, 12, 10, 12, 10, 8, 7, 7, 8, 9, 7, 6, 7, 8, 7, 6,
    7, 7, 7, 7, 8, 7, 7, 8, 8, 12, 11, 7, 9, 11, 12, 6, 7, 6, 6, 5, 7, 8, 8, 6, 11, 9, 6, 7, 6,
    6, 7, 11, 6, 6, 6, 7, 9, 8, 9, 9, 11, 8, 11, 9, 12, 8, 12, 5, 6, 6, 6, 5, 6, 6, 6, 5, 11, 7,
    5, 6, 5, 5, 6, 10, 5, 5, 5, 5, 8, 7, 8, 8, 10, 11, 11, 12, 12, 12, 13, 13, 13, 13, 13, 13,
    13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13,
    13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 13, 12,
    13, 13, 13, 12, 13, 13, 13, 12, 13, 13, 13, 13, 12, 13, 13, 13, 12, 12, 12, 13, 13, 13, 13,
    13, 13, 13, 13, 13, 13, 13,
)
CH_CODE_ASC = (
    1168, 4064, 2016, 3040, 992, 3552, 1504, 2528, 480, 184, 98, 3808, 1760, 34, 2784, 736,
    3296, 1248, 2272, 224, 3936, 1888, 2912, 864, 3424, 1376, 4672, 2400, 352, 3680, 1632, 2656,
    15, 592, 56, 608, 80, 3168, 912, 216, 66, 2, 88, 432, 124, 41, 60, 152, 92, 9, 28, 108, 44,
    76, 24, 12, 116, 232, 104, 1120, 144, 52, 176, 1808, 2144, 49, 84, 17, 33, 23, 20, 168, 40,
    1, 784, 304, 62, 100, 30, 46, 36, 1296, 14, 54, 22, 68, 48, 200, 464, 208, 272, 72, 1552,
    336, 96, 136, 4000, 7, 38, 6, 58, 27, 26, 42, 10, 11, 528, 4, 19, 50, 3, 29, 18, 400, 13,
    21, 5, 25, 8, 120, 240, 112, 656, 1040, 16, 1952, 2976, 928, 576, 7232, 3136, 5184, 1088,
    6208, 2112, 4160, 64, 8064, 3968, 6016, 1920, 7040, 2944, 4992, 896, 7552, 3456, 5504, 1408,
    6528, 2432, 4480, 384, 7808, 3712, 5760, 1664, 6784, 2688, 4736, 640, 7296, 3200, 5248,
    1152, 6272, 2176, 4224, 128, 7936, 3840, 5888, 1792, 6912, 2816, 4864, 3488, 1440, 2464,
    416, 3744, 1696, 2720, 672, 3232, 1184, 2208, 160, 3872, 1824, 2848, 800, 3360, 1312, 2336,
    288, 3616, 1568, 2592, 544, 3104, 1056, 2080, 32, 4032, 1984, 3008, 960, 3520, 1472, 2496,
    448, 3776, 1728, 2752, 704, 3264, 1216, 2240, 192, 3904, 1856, 2880, 832, 768, 3392, 7424,
    3328, 5376, 1344, 1280, 6400, 2304, 2368, 4352, 256, 7680, 3584, 320, 5632, 1536, 6656,
    3648, 1600, 2624, 2560, 4608, 512, 7168, 3072, 5120, 1024, 6144, 2048, 4096, 0,
)
LEN_BITS = (3, 2, 3, 3, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 7, 7)
LEN_CODE = (5, 3, 1, 6, 10, 2, 12, 20, 4, 24, 8, 48, 16, 32, 64, 0)
EX_LEN_BITS = (0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8)
LEN_BASE = (0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 14, 22, 38, 70, 134, 262)
DIST_BITS = (
    2, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8,
    8, 8,
)
DIST_CODE = (
    3, 13, 5, 25, 9, 17, 1, 62, 30, 46, 14, 54, 22, 38, 6, 58, 26, 42, 10, 50, 18, 34, 66, 2,
    124, 60, 92, 28, 108, 44, 76, 12, 116, 52, 84, 20, 100, 36, 68, 4, 120, 56, 88, 24, 104, 40,
    72, 8, 240, 112, 176, 48, 208, 80, 144, 16, 224, 96, 160, 32, 192, 64, 128, 0,
)


def _lut(codes, bits, width):
    """码字按低位先出的顺序排列，用 width 位窗口直接查表得到 (符号, 码长)。"""
    table = [None] * (1 << width)
    for sym, (code, n) in enumerate(zip(codes, bits)):
        for fill in range(1 << (width - n)):
            table[code | (fill << n)] = (sym, n)
    return table


_LIT = _lut(CH_CODE_ASC, CH_BITS_ASC, 13)
_LEN = _lut(LEN_CODE, LEN_BITS, 7)
_DIST = _lut(DIST_CODE, DIST_BITS, 8)

# 长度 2..519 → (码字, 位数)：1 位标志 + 长度码 + 额外位
_LEN_TOKEN = {}
for _i in range(16):
    for _extra in range(1 << EX_LEN_BITS[_i]):
        _LEN_TOKEN[LEN_BASE[_i] + _extra + 2] = (
            (_extra << (LEN_BITS[_i] + 1)) | (LEN_CODE[_i] << 1) | 1, LEN_BITS[_i] + EX_LEN_BITS[_i] + 1)
_LIT_TOKEN = [(CH_CODE_ASC[b] << 1, CH_BITS_ASC[b] + 1) for b in range(256)]


def explode(block: bytes) -> bytes:
    """解压一个 DCL Implode 数据块。"""
    ascii_lit, dict_bits = block[0], block[1]
    if ascii_lit > 1 or not 4 <= dict_bits <= 6:
        raise ValueError("不是 DCL Implode 数据")
    buf = bytes(block[2:]) + b"\0" * 8
    out = bytearray()
    pos = 0
    while True:
        v = int.from_bytes(buf[pos >> 3:(pos >> 3) + 4], "little") >> (pos & 7)
        if not v & 1:                                   # 字面量
            if ascii_lit:
                sym, n = _LIT[(v >> 1) & 0x1FFF]
                out.append(sym)
                pos += 1 + n
            else:
                out.append((v >> 1) & 0xFF)
                pos += 9
            continue
        i, n = _LEN[(v >> 1) & 0x7F]
        e = EX_LEN_BITS[i]
        length = LEN_BASE[i] + ((v >> (1 + n)) & ((1 << e) - 1)) + 2
        pos += 1 + n + e
        if length == 519:
            break
        v = int.from_bytes(buf[pos >> 3:(pos >> 3) + 4], "little") >> (pos & 7)
        hi, n = _DIST[v & 0xFF]
        low_bits = 2 if length == 2 else dict_bits
        dist = ((hi << low_bits) | ((v >> n) & ((1 << low_bits) - 1))) + 1
        pos += n + low_bits
        start = len(out) - dist
        if start < 0:
            raise ValueError("压缩数据损坏：距离越界")
        if dist >= length:
            out += out[start:start + length]
        else:                                           # 重叠复制
            for k in range(length):
                out.append(out[start + k])
    return bytes(out)


def _match_len(data, a, b, limit):
    """data[a:] 与 data[b:] 的公共前缀长度（不超过 limit），倍增加二分，用切片比较提速。"""
    lo, step = 0, 16
    while lo < limit:
        hi = min(lo + step, limit)
        if data[a + lo:a + hi] == data[b + lo:b + hi]:
            lo, step = hi, step * 2
            continue
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if data[a + lo:a + mid] == data[b + lo:b + mid]:
                lo = mid
            else:
                hi = mid
        break
    return lo


def implode(data: bytes) -> bytes:
    """贪心 LZ77 压缩成 DCL Implode（ASCII 字面量、4 KB 字典）。"""
    out = bytearray(b"\x01\x06")
    acc = nacc = 0

    def put(code, n):
        nonlocal acc, nacc
        acc |= code << nacc
        nacc += n
        while nacc >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nacc -= 8

    last = {}                       # 3 字节前缀 → 最近出现位置
    n = len(data)
    i = 0
    while i < n:
        length = 0
        if i + 3 <= n:
            key = data[i:i + 3]
            c = last.get(key)
            last[key] = i
            if c is not None and i - c <= WINDOW:
                length = _match_len(data, c, i, min(MAX_LEN, n - i))
        if length >= 3:
            put(*_LEN_TOKEN[length])
            d = i - c - 1
            put(DIST_CODE[d >> 6], DIST_BITS[d >> 6])
            put(d & 0x3F, 6)
            for j in range(i + 1, min(i + length, n - 2)):
                last[data[j:j + 3]] = j
            i += length
        else:
            put(*_LIT_TOKEN[data[i]])
            i += 1
    put(*_LEN_TOKEN[519])
    if nacc:
        out.append(acc & 0xFF)
    return bytes(out)


def decode(data: bytes) -> bytes:
    """.ms14 文件内容 → XML 字节。"""
    if not data.startswith(MAGIC):
        raise ValueError("不是 Multisim 压缩文件（缺少 MSMCompressedElectronicsWorkbenchXML 头）")
    total = struct.unpack_from("<Q", data, len(MAGIC))[0]
    pos = len(MAGIC) + 8
    parts, size = [], 0
    while size < total:
        raw_len, comp_len = struct.unpack_from("<II", data, pos)
        pos += 8
        part = explode(data[pos:pos + comp_len])
        if len(part) != raw_len:
            raise ValueError(f"分段长度不符：应为 {raw_len}，得到 {len(part)}")
        parts.append(part)
        size += raw_len
        pos += comp_len
    return b"".join(parts)


def encode(xml: bytes) -> bytes:
    """XML 字节 → .ms14 文件内容。"""
    chunks = [MAGIC, struct.pack("<Q", len(xml))]
    for k in range(0, len(xml), SECTION):
        part = xml[k:k + SECTION]
        comp = implode(part)
        chunks += [struct.pack("<II", len(part), len(comp)), comp]
    return b"".join(chunks)
