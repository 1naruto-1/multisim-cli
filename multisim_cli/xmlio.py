"""Multisim XML 读写与常用小工具。

- 属性值里的换行是 SPICE 模型的一部分，解析前转成字符引用，写回时还原（移植自 multisim-mcp native_xml.py，MIT）。
- 字符串属性带类型前缀：&ASC 为 ASCII，空串不带前缀。
"""
import copy
import itertools
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from . import codec

_ATTR = re.compile(r'''(=\s*)(["'])(.*?)\2''', re.DOTALL)


def _preserve(match):
    value = match[3].replace("\r", "&#13;").replace("\n", "&#10;").replace("\t", "&#9;")
    return match[1] + match[2] + value + match[2]


def parse_text(text: str) -> ET.Element:
    # 少数官方示例的 base64 属性末尾带 NUL，XML 不允许，去掉不影响内容
    text = text.replace("\x00", "")
    return ET.fromstring(_ATTR.sub(_preserve, text))


def _esc_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


def to_text(root: ET.Element) -> str:
    """按 Multisim 自己的格式输出：每个标签独占一行、无缩进、属性里的换行保持原样。"""
    out = ['<?xml version="1.0" encoding="ASCII"?>\n']

    def emit(el):
        attrs = "".join(f' {k}="{_esc_attr(v)}"' for k, v in el.attrib.items())
        text = (el.text or "").strip()
        if len(el) == 0 and not text:
            out.append(f"<{el.tag}{attrs}/>\n")
            return
        out.append(f"<{el.tag}{attrs}>")
        if text:
            out.append(text.replace("&", "&amp;").replace("<", "&lt;"))
        else:
            out.append("\n")
        for child in el:
            emit(child)
        out.append(f"</{el.tag}>\n")

    emit(root)
    return "".join(out)


def read(path) -> str:
    """按原样读文本（不做换行转换：属性里的 \r\n 是 SPICE 模型内容）。"""
    return Path(path).read_bytes().decode("utf-8")


def write(path, text: str) -> None:
    Path(path).write_bytes(text.encode("utf-8"))


def load(path) -> ET.Element:
    """读 .ms14 或解码后的 .xml，返回根元素。"""
    data = Path(path).read_bytes()
    if data.startswith(codec.MAGIC):
        data = codec.decode(data)
    return parse_text(data.decode("utf-8", errors="replace"))


def save(root: ET.Element, path) -> None:
    """按扩展名写 .ms14（压缩）或 .xml。"""
    data = to_text(root).encode("utf-8")
    path = Path(path)
    path.write_bytes(codec.encode(data) if path.suffix.lower() == ".ms14" else data)


def asc(value) -> str:
    return "&ASC" + str(value) if value not in (None, "") else ""


def unasc(value) -> str:
    value = value or ""
    return value[4:] if value.startswith("&ASC") else value


_UC = re.compile(r"_uc(\d)")


def decode_uc(text: str) -> str:
    """Multisim 的非 ASCII 转义：_uc<n><n 个 4 位十六进制>，如 _uc103a9 = Ω、_uc26e2958c = 示波。"""
    out, i = [], 0
    for m in _UC.finditer(text):
        if m.start() < i:
            continue
        n = int(m.group(1))
        hexes = text[m.end():m.end() + 4 * n]
        if n == 0 or len(hexes) < 4 * n or any(c not in "0123456789abcdefABCDEF" for c in hexes):
            continue
        out.append(text[i:m.start()])
        out.append("".join(chr(int(hexes[k:k + 4], 16)) for k in range(0, 4 * n, 4)))
        i = m.end() + 4 * n
    out.append(text[i:])
    return "".join(out)


def text(value: str) -> str:
    """属性字符串 → 普通文本（&ASC / &UNI 都能解）。"""
    value = value or ""
    if value.startswith("&UNI"):
        return decode_uc(value[4:])
    return unasc(value)


def uni(value: str) -> str:
    """普通文本 → 属性字符串：纯 ASCII 用 &ASC，否则用 &UNI 并把连续的非 ASCII 字符按 9 个一组转义。"""
    if all(ord(ch) < 128 for ch in value):
        return asc(value)
    out, run = [], []

    def flush():
        while run:
            chunk = run[:9]
            del run[:9]
            out.append(f"_uc{len(chunk)}" + "".join(f"{ord(ch):04x}" for ch in chunk))
    for ch in value:
        if ord(ch) < 128:
            flush()
            out.append(ch)
        else:
            run.append(ch)
    flush()
    return "&UNI" + "".join(out)


def clear(element: ET.Element) -> None:
    for child in list(element):
        element.remove(child)


def find_tag(root: ET.Element, tag: str):
    return next(root.iter(tag), None)


class IdAllocator:
    """新对象 ID：从一个不与模板冲突的大数开始递增（Multisim 只要求文件内唯一）。"""

    def __init__(self, start: int = 900_000_000):
        self._next = itertools.count(start)

    def __call__(self) -> str:
        return str(next(self._next))


ID_ATTRS = ("ID", "CiID")
# 引用其它对象 ID 的属性（重映射时一起改）
REF_ATTRS = ("SymCompID", "CiComponent", "Component", "Connect1", "Connect2", "Node", "NodeText",
             "Model", "CiProbeExtComp", "Circuit", "Scope", "PortID")
GUID_ATTRS = ("Guid", "InstanceID")


def remap(items, ids: IdAllocator) -> dict:
    """给一组子树（同一个元件的元素、端口、模型、符号）换新 ID，组内相互引用一起改；返回 旧ID→新ID。

    组外引用（端口所连节点、所属电路）也会被换成新号，调用方随后重新设置。
    """
    mapping = {}
    for item in items:
        for el in item.iter():
            for attr in ID_ATTRS:
                old = el.get(attr)
                if old and old not in mapping:
                    mapping[old] = ids()
    for item in items:
        for el in item.iter():
            for attr in ID_ATTRS + REF_ATTRS:
                old = el.get(attr)
                if old in mapping:
                    el.set(attr, mapping[old])
            for attr in GUID_ATTRS:
                if el.get(attr):
                    el.set(attr, "{" + str(uuid.uuid4()).upper() + "}")
    return mapping


def deepcopy(element: ET.Element) -> ET.Element:
    return copy.deepcopy(element)
