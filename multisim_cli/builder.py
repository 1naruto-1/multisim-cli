"""按电路规格（spec.json）生成可编辑的 Multisim .ms14。

spec 字段：
    netlist      网表（写法见 netlist.py）
    output       输出 .ms14 路径；文件名只能用英文、数字、下划线、短横线（仪器预设靠文件名匹配）
    layout       {"R1": [x, y] 或 [x, y, 角度], "GND": [[x, y], ...]}，坐标是元件引脚中心；
                 角度 0/90/180/270（屏幕顺时针）：两端元件 0° 时第一个节点在左，90° 时在上
    instruments  仪器面板预设 {"XSC1": {"timebase": "500us", ...}}
    sim_time     交互式仿真结束时间，如 "20ms"；有示波器时默认 20 屏
    net_names    是否显示网络名，默认 false
    colors       导线颜色 {"vout": "blue"}；示波器 B 通道默认蓝色
    sheet        图纸尺寸 [宽, 高]（英寸）；默认按电路范围收紧
"""
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import netlist as nl
from . import xmlio
from .parts import Library
from .router import route_all

# Multisim 颜色是 0x00BBGGRR
COLORS = {"red": 255, "blue": 16711680, "green": 32768, "magenta": 16711935, "orange": 33023, "black": 0}
MATRICES = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}
GRID = 9

SCOPE_KEYS = {
    "timebase": "NI_EWB_TIMEBASE_SCALE_VALUE",          # s/div
    "x_offset": "NI_EWB_TIMEBASE_POSITION_VALUE",       # div
    "a_scale": "NI_EWB_A_CHANNEL_SCALE_VALUE",          # V/div
    "b_scale": "NI_EWB_B_CHANNEL_SCALE_VALUE",
    "a_offset": "NI_EWB_A_CHANNEL_POSITION_VALUE",      # div
    "b_offset": "NI_EWB_B_CHANNEL_POSITION_VALUE",
    "trigger_level": "NI_EWB_TRIGGERBASE_TRIGGERLEVEL", # V
}
SCOPE_ENUMS = {
    "a_coupling": ("NI_EWB_A_CHANNEL_MODE", {"ac": "0", "gnd": "1", "dc": "2"}),
    "b_coupling": ("NI_EWB_B_CHANNEL_MODE", {"ac": "0", "gnd": "1", "dc": "2"}),
    "trigger": ("NI_EWB_TRIGGERBASE_TRIGGER_MODE", {"none": "0", "normal": "1", "single": "2", "auto": "3"}),
    "trigger_edge": ("NI_EWB_TRIGGERBASE_TRIGGER_EDGE", {"rising": "0", "falling": "1"}),
    "trigger_source": ("NI_EWB_TRIGGERBASE_SOURCE_MODE", {"a": "0", "b": "1", "ext": "2"}),
}
BODE_KEYS = {
    "f_start": "NI_EWB_HORZ_INITIAL_VALUE", "f_stop": "NI_EWB_HORZ_FINAL_VALUE",
    "db_min": "NI_EWB_VERT_INITIAL_LOG_VALUE", "db_max": "NI_EWB_VERT_FINAL_LOG_VALUE",
    "lin_min": "NI_EWB_VERT_INITIAL_LIN_VALUE", "lin_max": "NI_EWB_VERT_FINAL_LIN_VALUE",
    "phase_min": "NI_EWB_VERT_INITIAL_PHASE_VALUE", "phase_max": "NI_EWB_VERT_FINAL_PHASE_VALUE",
    "points": "NI_EWB_RESOLUTION_POINTS",
}
BODE_ENUMS = {
    "mode": ("NI_EWB_BODE_CONTROL_MODE", {"magnitude": "0", "phase": "1"}),
    "vertical": ("NI_EWB_VERTICAL_MODE", {"log": "0", "lin": "1"}),
    "horizontal": ("NI_EWB_HORIZONTAL_MODE", {"log": "0", "lin": "1"}),
}
FGEN_WAVES = {"SINE": "0", "TRIANGLE": "1", "SQUARE": "2"}     # 面板按钮顺序


# ---------------------------------------------------------------------------
# 数值与标签
# ---------------------------------------------------------------------------
def number(value) -> float:
    """仪器设置里的数值，允许 "500us"、"1V"、"1.59kHz"。"""
    if isinstance(value, (int, float)):
        return float(value)
    return nl.parse_value(re.sub(r"(?i)(v|s|hz|db|deg|div)$", "", str(value).strip()))


def eng(value: float, micro="µ") -> str:
    """工程记数：0.0001 → 100µ，4700 → 4.7k。"""
    if value == 0:
        return "0"
    exp = int(math.floor(math.log10(abs(value)) / 3)) * 3
    exp = max(-15, min(12, exp))
    mant = value / 10 ** exp
    text = f"{mant:.4g}"
    prefix = {-15: "f", -12: "p", -9: "n", -6: micro, -3: "m", 0: "", 3: "k", 6: "M", 9: "G", 12: "T"}[exp]
    return text + prefix


uni = xmlio.uni


def _set_param(comp, index: int, value: float, display: str | None = None):
    plist = comp.find(".//CiaParamList")
    doubles, params = plist.findall("./doubles/Item"), plist.findall("./parameters/Item")
    if index >= min(len(doubles), len(params)):
        raise ValueError(f"参数表里没有 #{index}")
    doubles[index].set("Value", format(value, ".17g"))
    # 符号上的数值标签由 Multisim 按这个显示字符串重新生成，用工程记数（1m 而不是 0.001）
    params[index].set("Value", xmlio.asc(display if display is not None else eng(value, "u")))


def _set_cstring(comp, index: int, text: str):
    items = comp.findall("./Attributes/Item")
    if index < len(items) and items[index].find("CiaCString") is not None:
        items[index].find("CiaCString").set("String", text)


def apply_value(inst: nl.Instance, comp, kind: str) -> str | None:
    """把网表里的数值写进元件参数，返回符号上显示的数值标签（已编码）。"""
    if inst.value is not None and kind in ("R", "C", "L"):
        v = nl.parse_value(inst.value)
        _set_param(comp, 1, v)
        unit = {"R": "Ω", "C": "F", "L": "H"}[kind]
        label = uni(eng(v) + unit)
        _set_cstring(comp, 41, xmlio.asc(eng(v, "u")))
        _set_cstring(comp, 42, label)
        return label
    src = inst.source
    if not src:
        return None
    ac = src.get("ac") or (0.0, 0.0)
    if inst.part == "AC_VOLTAGE":
        for idx, val in ((1, src["amplitude"]), (3, src["offset"]), (5, src["freq"]), (7, src["delay"]),
                         (9, src["damping"]), (11, src["phase"]), (13, ac[0]), (15, ac[1])):
            _set_param(comp, idx, val)
        return uni(f"{eng(src['amplitude'])}Vpk \n{eng(src['freq'])}Hz \n{src['phase']:g}° ")
    if inst.part == "PULSE_VOLTAGE":
        for k, val in enumerate(src["pulse"]):
            _set_param(comp, 1 + 2 * k, val)
        _set_param(comp, 15, ac[0])
        _set_param(comp, 17, ac[1])
        v1, v2, _, _, _, pw, per = src["pulse"]
        return xmlio.asc(f"{eng(v1, 'u')}V {eng(v2, 'u')}V \n{eng(pw, 'u')}s {eng(per, 'u')}s ")
    if inst.part in ("DC_POWER", "DC_CURRENT"):
        _set_param(comp, 1, src["dc"])
        if inst.part == "DC_POWER":
            _set_param(comp, 3, ac[0])
            _set_param(comp, 5, ac[1])
        return xmlio.asc(f"{eng(src['dc'], 'u')}{'V' if inst.part == 'DC_POWER' else 'A'} ")
    return None


# ---------------------------------------------------------------------------
# 几何
# ---------------------------------------------------------------------------
def _transform(el, x, y, translation=True):
    g = lambda k, d: float(el.get("Transformer-" + k, d))
    return (x * g("M00", "1") + y * g("M10", "0") + (g("M20", "0") if translation else 0),
            x * g("M01", "0") + y * g("M11", "1") + (g("M21", "0") if translation else 0))


def pin_info(sym_item) -> dict:
    """{端口ID: 引脚中心（已含符号旋转、不含平移）、引出方向、连接器ID}（移植自 multisim-mcp）。"""
    sym = sym_item[0]
    info = {}
    for pin_item in sym_item.findall("./CIITSymbolComp/Objects/Item[@Class='CIITPinSymbolComp']"):
        pin = pin_item[0]
        conn_item = pin_item.find("./CIITPinSymbolComp/Objects/Item[@Class='CIITPinConnectorComp']")
        conn = conn_item[0]
        px, py = _transform(conn, float(conn.get("ptCenterX")), float(conn.get("ptCenterY")))
        px, py = _transform(pin, px, py)
        px, py = _transform(sym, px, py, translation=False)
        px = round(px / GRID) * GRID if abs(px - round(px / GRID) * GRID) < .001 else px
        py = round(py / GRID) * GRID if abs(py - round(py / GRID) * GRID) < .001 else py
        direction = None
        for lead in pin.findall("./Objects/Item/CODLineComp"):
            ends = []
            for n in (0, 1):
                lx, ly = _transform(lead, float(lead.get(f"pt{n}X")), float(lead.get(f"pt{n}Y")))
                lx, ly = _transform(pin, lx, ly)
                ends.append(_transform(sym, lx, ly, translation=False))
            for anchor, inside in (ends, ends[::-1]):
                if abs(anchor[0] - px) < .01 and abs(anchor[1] - py) < .01:
                    vx, vy = px - inside[0], py - inside[1]
                    if abs(vx) > .01 and abs(vy) < .01:
                        direction = (1 if vx > 0 else -1, 0)
                    elif abs(vy) > .01 and abs(vx) < .01:
                        direction = (0, 1 if vy > 0 else -1)
        info[pin.get("PortID")] = {"x": px, "y": py, "direction": direction, "connector": conn_item.get("ID")}
    return info


def _rotate(sym_item, angle: int, relabel=False):
    """设置符号旋转矩阵，并把标签摆正（文字保持水平）。

    旋转过的符号总是重摆标签；relabel=True 时 0° 也重摆（两端元件统一成上名下值，
    不用样例里五花八门的原始位置）。
    """
    if angle not in MATRICES:
        raise ValueError(f"旋转角度只能是 0/90/180/270，收到 {angle}")
    sym = sym_item[0]
    a, b, c, d = MATRICES[angle]
    for key, v in zip(("M00", "M01", "M10", "M11"), (a, b, c, d)):
        sym.set("Transformer-" + key, str(v))
    if angle == 0 and not relabel:
        return
    pins = pin_info(sym_item)
    if not pins:
        return
    xs, ys = [p["x"] for p in pins.values()], [p["y"] for p in pins.values()]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    if max(ys) - min(ys) > max(xs) - min(xs):   # 竖放：标签放右侧、左对齐
        offsets, align = {"CIITSymTextCompName": (18, -9), "CIITSymTextCompValue": (18, 9)}, "0"
    else:                                         # 横放：名字在上、数值在下，居中
        offsets, align = {"CIITSymTextCompName": (0, -24), "CIITSymTextCompValue": (0, 15)}, "1"
    for tag, (ox, oy) in offsets.items():
        label = sym.find("./Objects/Item/" + tag)
        if label is None:
            continue
        px, py = cx + ox, cy + oy
        for key, v in {"M00": a, "M01": c, "M10": b, "M11": d, "M20": a * px + b * py, "M21": c * px + d * py}.items():
            label.set("Transformer-" + key, f"{v:g}")
        label.set("HorizontalAlign", align)


def _label_boxes(sym_item, refdes):
    """元件名和数值标签在图纸上占的矩形（按字号估算），布线时当作障碍。"""
    sym = sym_item[0]
    out = []
    for tag in ("CIITSymTextCompName", "CIITSymTextCompValue"):
        label = sym.find("./Objects/Item/" + tag)
        lines = xmlio.text(label.get("Output") or "").strip().splitlines() if label is not None else []
        if not any(line.strip() for line in lines) or label.get("Hidden") == "1":
            continue
        x, y = _transform(sym, *_transform(label, 0, 0))
        size = abs(float(label.get("Font-Height") or 13))
        size = size if size > 9 else 16                      # 正值字号按点数给出，约 16 像素
        w, h = max(len(line.rstrip()) for line in lines) * size * 0.6, len(lines) * size * 1.15
        if label.get("HorizontalAlign") == "1":
            x -= w / 2
        # 向外取整到网格，绕开标签的导线也落在网格上
        gx0, gy0 = math.floor((x - 2) / GRID) * GRID, math.floor((y - 2) / GRID) * GRID
        gx1, gy1 = math.ceil((x + w + 2) / GRID) * GRID, math.ceil((y + h + 2) / GRID) * GRID
        out.append({"refdes": f"{refdes}:{tag}", "owner": refdes, "x": gx0, "y": gy0, "width": gx1 - gx0, "height": gy1 - gy0})
    return out


def _horizontal(sym_item) -> bool:
    """两端元件的原始符号是否横放（电阻电容电感二极管）；电源类原本就是竖放的。"""
    pins = list(pin_info(sym_item).values())
    return len(pins) == 2 and abs(pins[0]["y"] - pins[1]["y"]) < 1


def _base_angle(sym_item, ports, pin_order) -> int:
    """两端元件统一成 0° 时第一个节点在左（电容等原生符号的 1 号端在右边，要先转 180°）。"""
    if len(pin_order) != 2:
        return 0
    pins = pin_info(sym_item)
    name_of = {p.get("CiID"): xmlio.unasc(p[0].get("LocalName")) for p in ports}
    at = {name_of.get(pid): p for pid, p in pins.items()}
    a, b = at.get(pin_order[0]), at.get(pin_order[1])
    if a and b and abs(a["y"] - b["y"]) < 1 and a["x"] > b["x"]:
        return 180
    return 0


def _snap(value: float) -> float:
    return round(value / GRID) * GRID


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------
def _find_main(root):
    for diagram in root.iter("CIITDiagram"):
        elements = diagram.find("./Elements")
        composite = diagram.find("./Components/CODComposite")
        if elements is None or composite is None:
            continue
        circuit = next((c for c in elements if c.get("Class") == "CiCircuit"), None)
        if circuit is not None:
            return diagram, composite, elements, circuit
    raise ValueError("电路壳里没有主电路图")


def _refdes_info(refdes, file_path, stem, template=None):
    """RefDes 记录：多单元器件复制采集到的那条（含引脚顺序），其它按 multisim-mcp 的格式新建。"""
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", refdes)
    if not m:
        return None
    prefix, num = m.groups()
    text = f"&ASC!0!0!0{refdes}!0{file_path}!01!0{stem}!0"
    if template is not None:
        item = xmlio.deepcopy(template)
        section = xmlio.unasc(item.find("RefDesInfo").get("IRSection"))
        item.set("CIRKey", xmlio.asc(refdes + section))
        info = item.find("RefDesInfo")
        info.set("IRPrefix", xmlio.asc(prefix))
        info.set("IRNumber", num)
        data = info.find("RefDesInfoData")
        data.set("RefDes", text)
        data.set("RefDesStrSize", str(len(text)))
        data.set("Prefix", xmlio.asc(prefix))
        data.set("Number", num)
        return item
    item = ET.Element("CIRToInfoMapItem", {"CIRKey": xmlio.asc(refdes)})
    info = ET.SubElement(item, "RefDesInfo", {"Class": "CIITHierRefDesInfo", "IRPrefix": xmlio.asc(prefix),
                                              "IRNumber": num, "Locked": "0", "IRSection": "", "IRSectionID": "-1",
                                              "SpiceTemplate": ""})
    data = ET.SubElement(info, "RefDesInfoData", {
        "Class": "CIITHierRefDes", "RefDesBufSize": "260", "RefDesStrSize": str(len(text)), "RefDesCount": "2",
        "RefDes": text, "SectionBufSize": "0", "SectionStrSize": "0", "Section": "&ASC(null)",
        "Prefix": xmlio.asc(prefix), "Number": num})
    ET.SubElement(data, "RefDesData")
    for tag in ("PinOrderCIR", "PinOrderIR", "PinNumbersCIR", "PinNumbersIR", "SharedPins"):
        ET.SubElement(info, tag)
    return item


def _set_key(node, key, value):
    for el in node.iter("Element"):
        if el.get("Key") == "&ASC" + key:
            el.find("CDataElement").set("Data", value)
            return
    raise KeyError(f"仪器状态里没有 {key}")


def apply_instrument(node, ref: str, settings: dict, params: dict):
    up = ref.upper()
    _set_key(node, "NI_EWB_SHOW_WINDOW_STATUS", "1" if settings.get("show", not up.startswith("XFG")) else "0")
    keys, enums = ({}, {})
    if up.startswith("XSC"):
        keys, enums = SCOPE_KEYS, SCOPE_ENUMS
    elif up.startswith("XBP"):
        keys, enums = BODE_KEYS, BODE_ENUMS
    for name, value in settings.items():
        if name == "show":
            continue
        if name in keys:
            _set_key(node, keys[name], str(int(value)) if name == "points" else f"{number(value):g}")
        elif name in enums:
            key, table = enums[name]
            _set_key(node, key, table[str(value).lower()])
        else:
            raise ValueError(f"{ref}: 不支持的仪器参数 {name}")
    if up.startswith("XFG"):
        wave = params.get("WAVE", "SINE").upper()
        if wave not in FGEN_WAVES:
            raise ValueError(f"{ref}: WAVE 只能是 SINE/TRIANGLE/SQUARE")
        values = {"NI_EWB_WAVE_MODE": FGEN_WAVES[wave],
                  "NI_EWB_FREQUENCY_VALUE": params.get("FREQ", "1k"),
                  "NI_EWB_AMPLITUDE_VALUE": params.get("AMPLITUDE", "1"),
                  "NI_EWB_OFFSET_VALUE": params.get("OFFSET", "0"),
                  "NI_EWB_DEPUTY_CYCLE_VALUE": params.get("DUTY", "50"),
                  "NI_EWB_RISETIMESETTING": params.get("RISE", "10n")}
        for key, value in values.items():
            _set_key(node, key, value if key == "NI_EWB_WAVE_MODE" else f"{number(value):g}")


class Builder:
    def __init__(self, lib: Library, out: Path):
        self.lib, self.out, self.stem = lib, out, out.stem
        self.root = lib.struct("shell.xml")
        for el in self.root.iter():                 # 电路壳是用名为 blank 的文件新建的
            for k, v in el.attrib.items():
                if v == "&ASCblank" or "#1/blank:" in v:
                    el.set(k, v.replace("blank", self.stem))
        self.diagram, composite, self.elements, self.circuit_item = _find_main(self.root)
        self.objects = composite.find("Objects")
        self.refs = composite.find("ReferencedComponents")
        self.circuit = self.circuit_item.find("CiCircuit")
        self.ids = xmlio.IdAllocator()
        xmlio.remap([self.circuit_item], self.ids)
        self.nodes, self.connections = {}, {}
        self.comp_items, self.port_items, self.model_items = [], [], []
        self.boxes, self.labels, self.placements, self.warnings = [], [], [], []
        self.model_seq = {}
        self.instrument_states, self.refdes_items = [], []
        self.expected = {}                           # {网络: {位号.引脚}}，自检时与文件对比

    # -- 网络 --------------------------------------------------------------
    def node(self, net: str):
        if net not in self.nodes:
            item = self.lib.struct("node_v0.xml" if net == "0" else "node_named.xml")
            xmlio.remap([item], self.ids)
            item[0].set("LocalName", xmlio.asc(net))
            xmlio.clear(item[0].find("Ports"))
            self.nodes[net] = item
        return self.nodes[net]

    def _attach(self, port_item, net):
        nodes = port_item[0].find("Nodes")
        xmlio.clear(nodes)
        if net is None:
            return
        node = self.node(net)
        nodes.append(ET.Element("Item", {"CiID": node.get("CiID")}))
        node[0].find("Ports").append(ET.Element("Item", {"CiID": port_item.get("CiID")}))

    # -- 元件 --------------------------------------------------------------
    def place(self, refdes, part_name, nets: dict, center, angle=0, inst=None, ground=False):
        """nets: {端口名: 网络}；center: 引脚中心的目标坐标。"""
        part = self.lib.load(part_name)
        pin_order = self.lib.index[self.lib.resolve(part_name)]["pins"]
        items = [xmlio.deepcopy(i) for i in part.find("Items")]
        xmlio.remap(items, self.ids)
        comp_item = next(i for i in items if i.get("Class") == "CiComponent")
        ports = [i for i in items if i.get("Class") == "CiPort"]
        models = [i for i in items if i.get("Class") == "CiModel"]
        sym_item = next(i for i in items if i.get("Class") == "CIITSymbolComp")
        comp, sym = comp_item[0], sym_item[0]
        comp.set("LocalName", xmlio.asc("0" if ground else refdes))
        sym.set("CiComponent", comp_item.get("CiID"))
        if comp.get("SymCompID") not in (None, "", "0"):
            comp.set("SymCompID", sym_item.get("ID"))
        for model in models:                         # 每个实例一份模型，按 Multisim 的 名称__族__序号 命名
            m = model[0]
            m.set("Scope", self.circuit_item.get("CiID"))
            parts = xmlio.unasc(m.get("LocalName")).split("__")
            if len(parts) >= 3:
                key = tuple(parts[:2])
                self.model_seq[key] = self.model_seq.get(key, 0) + 1
                new = f"{parts[0]}__{parts[1]}__{self.model_seq[key]}"
                m.set("LocalName", xmlio.asc(new))
                card = m.find("Attributes/Item/CiaCString")
                if card is not None:
                    card.set("String", re.sub(r"(?i)^(&ASC\s*\.(?:SUBCKT|MODEL)\s+)\S+", lambda mm: mm.group(1) + new,
                                              card.get("String", ""), count=1))

        kind = refdes[0].upper()
        value_label = apply_value(inst, comp, kind) if inst is not None else None
        name_label = sym.find("./Objects/Item/CIITSymTextCompName")
        if name_label is not None:
            section = xmlio.unasc(sym.get("IRSection"))
            name_label.set("Output", "" if ground else xmlio.asc(refdes + section))
        value_item = sym.find("./Objects/Item/CIITSymTextCompValue")
        if value_label and value_item is not None:
            value_item.set("Output", value_label)

        for tag in ("CIITSymTextCompName", "CIITSymTextCompValue"):
            label = sym.find("./Objects/Item/" + tag)
            if label is not None and float(label.get("Font-Height") or -13) > 0:
                label.set("Font-Height", "-13")      # 个别样例用正数字号，显示得特别大
        base = _base_angle(sym_item, ports, pin_order)
        _rotate(sym_item, (angle + base) % 360, relabel=len(pin_order) == 2 and base in (0, 180) and _horizontal(sym_item))
        pins = pin_info(sym_item)
        if pins:
            xs = [p["x"] for p in pins.values()]
            ys = [p["y"] for p in pins.values()]
            first = next(iter(pins.values()))
            tx = _snap(center[0] - (min(xs) + max(xs)) / 2)
            ty = _snap(center[1] - (min(ys) + max(ys)) / 2)
            tx -= (tx + first["x"]) % GRID if (tx + first["x"]) % GRID <= GRID / 2 else (tx + first["x"]) % GRID - GRID
            ty -= (ty + first["y"]) % GRID if (ty + first["y"]) % GRID <= GRID / 2 else (ty + first["y"]) % GRID - GRID
        else:
            tx, ty = _snap(center[0]), _snap(center[1])
        sym.set("Transformer-M20", f"{tx:g}")
        sym.set("Transformer-M21", f"{ty:g}")

        name_of = {p.get("CiID"): xmlio.unasc(p[0].get("LocalName")) for p in ports}
        hidden = {h.get("Name"): h.get("Net") for h in part.findall("HiddenPin")}
        pts = []
        for port in ports:
            pname = name_of[port.get("CiID")]
            net = nets.get(pname, hidden.get(pname))
            self._attach(port, net)
            if net is not None and not ground:
                self.expected.setdefault(net, set()).add(f"{refdes}.{pname}")
            pin = pins.get(port.get("CiID"))
            if pin is None or net is None:
                continue
            conn = {"x": tx + pin["x"], "y": ty + pin["y"], "connector": pin["connector"], "symbol": sym_item,
                    "refdes": refdes, "direction": pin["direction"], "ground": ground}
            self.connections.setdefault(net, []).append(conn)
            pts.append((conn["x"], conn["y"]))
        if not pts:
            pts = [(tx + p["x"], ty + p["y"]) for p in pins.values()] or [(tx, ty)]
        # 布线障碍框 = 引脚范围外扩两格：导线离元件本体留出空隙，且边界和逃逸点都落在 9 的网格上
        x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
        box = {"refdes": refdes, "x": x0 - 18, "y": y0 - 18,
               "width": max(p[0] for p in pts) - x0 + 36, "height": max(p[1] for p in pts) - y0 + 36}
        self.boxes.append(box)
        self.labels.extend(_label_boxes(sym_item, refdes))
        self.placements.append({"refdes": refdes, "part": part_name, "center": [center[0], center[1]], "angle": angle,
                                "box": [box["x"], box["y"], box["width"], box["height"]]})

        self.objects.append(sym_item)
        self.refs.append(ET.Element("Item", {"ID": sym_item.get("ID"), "Class": "CIITSymbolComp"}))
        self.comp_items.append(comp_item)
        self.port_items.extend(ports)
        self.model_items.extend(models)
        state = part.find("Instrument/CSourceSymbolCollectNode")
        if state is not None:
            state = xmlio.deepcopy(state)
            state.set("CompLongName", f"&ASC{refdes}#1/{self.stem}:")
            self.instrument_states.append((refdes, state))
        if not ground:
            info = _refdes_info(refdes, str(self.out), self.stem, part.find("RefDesInfo/CIRToInfoMapItem"))
            if info is not None:
                self.refdes_items.append(info)
        return pins

    # -- 布线 --------------------------------------------------------------
    def wire_all(self, colors: dict, net_names: bool):
        for net, conns in self.connections.items():
            if len(conns) < 2:
                continue
            for c in conns:
                pin_item = self.lib.struct("extpin.xml")
                xmlio.remap([pin_item], self.ids)
                p = pin_item[0]
                p.set("CenterX", f"{c['x']:g}")
                p.set("CenterY", f"{c['y']:g}")
                p.find("ConnectList/Item").set("ID", c["connector"])
                self.refs.append(pin_item)
                c["extpin"] = pin_item.get("ID")
                for conn_item in c["symbol"].iter("Item"):
                    if conn_item.get("Class") == "CIITPinConnectorComp" and conn_item.get("ID") == c["connector"]:
                        clist = conn_item[0].find("ConnectList")
                        if clist is None:
                            clist = ET.SubElement(conn_item[0], "ConnectList")
                        xmlio.clear(clist)       # Class 不能省，否则 Multisim 认不出引脚连到了哪根线
                        clist.append(ET.Element("Item", {"ID": c["extpin"], "Class": "CODPinComp"}))
        islands, fallbacks = route_all(self.connections, self.boxes, self.labels)
        if fallbacks:
            self.warnings.append(f"{fallbacks} 段导线没找到无障碍路径，用了简单折线，请看预览图调整布局")
        self.wires = {}
        net_wire_items = {}
        for net, conns, edges, junctions in islands:
            members = {}
            for k, (jx, jy) in enumerate(junctions):
                count = sum((e["a"] == ("j", k)) + (e["b"] == ("j", k)) for e in edges)
                owner_item = self.lib.struct("junction_owner.xml")
                xmlio.remap([owner_item], self.ids)
                owner = owner_item[0]
                owner.set("Transformer-M20", f"{jx:g}")
                owner.set("Transformer-M21", f"{jy:g}")
                owner.set("CenterX", "0")          # 模板残留的中心偏移会把结点圆点画到别处
                owner.set("CenterY", "0")
                xmlio.clear(owner.find("ConnectList"))
                members[k] = []
                for _ in range(count):
                    member_item = self.lib.struct("junction_member.xml")
                    xmlio.remap([member_item], self.ids)
                    member_item[0].set("CenterX", f"{jx:g}")
                    member_item[0].set("CenterY", f"{jy:g}")
                    member_item[0].find("ConnectList/Item").set("ID", owner_item.get("ID"))
                    self.refs.append(member_item)
                    owner.find("ConnectList").append(ET.Element("Item", {"ID": member_item.get("ID"), "Class": "CODPinComp"}))
                    members[k].append(member_item.get("ID"))
                self.objects.append(owner_item)

            def endpoint(end):
                kind, idx = end
                return conns[idx]["extpin"] if kind == "pin" else members[idx].pop()

            node_id = self.node(net).get("CiID")
            for e in edges:
                wire_item = self.lib.struct("wire_named.xml")
                xmlio.remap([wire_item], self.ids)
                w = wire_item[0]
                w.set("Connect1", endpoint(e["a"]))
                w.set("Connect2", endpoint(e["b"]))
                w.set("Node", node_id)
                points = w.find("Points")
                xmlio.clear(points)
                for px, py in e["path"]:
                    points.append(ET.Element("Item", {"X": f"{px:g}", "Y": f"{py:g}"}))
                mod = w.find("./ElectricalObject/ModifierInfo/Element")
                if net == "0":
                    mod.set("NetModifier", "&ASCNI_EWB_NET_AUTONAMED")
                    mod.find("Item").set("Value", "")
                else:
                    mod.set("NetModifier", "&ASCNI_EWB_NET_NAME")
                    mod.find("Item").set("Value", xmlio.asc(net))
                if net in colors:                    # 示波器波形颜色跟随所接导线
                    w.set("PenColor", str(colors[net]))
                    w.set("Pen-Color", str(colors[net]))
                    w.set("LockedPenColor", "1")
                self.objects.append(wire_item)
                self.refs.append(ET.Element("Item", {"ID": wire_item.get("ID"), "Class": "CIITLinkComp"}))
                net_wire_items.setdefault(net, []).append(wire_item)
                self.wires.setdefault(net, []).append(e["path"])
        for net, items in net_wire_items.items():   # 每个网络一个网络名标签
            text_item = self.lib.struct("nodetext.xml")
            xmlio.remap([text_item], self.ids)
            t = text_item[0]
            segs = [s for path in self.wires[net] for s in zip(path, path[1:]) if s[0][1] == s[1][1]]
            if segs:
                a, b = max(segs, key=lambda s: abs(s[0][0] - s[1][0]))
                t.set("Transformer-M20", f"{(a[0] + b[0]) / 2:g}")
                t.set("Transformer-M21", f"{a[1] - 12:g}")
            if not net_names:
                t.set("Hidden", "1")
            links = t.find("Links")
            xmlio.clear(links)
            for wire_item in items:
                wire_item[0].set("NodeText", text_item.get("ID"))
                links.append(ET.Element("Item", {"ID": wire_item.get("ID"), "Class": "CIITLinkComp"}))
            self.objects.append(text_item)
            self.refs.append(ET.Element("Item", {"ID": text_item.get("ID"), "Class": "CODNodeTextComp"}))

    # -- 收尾 --------------------------------------------------------------
    def finish(self, instruments: dict, params_of: dict, sim_time, sheet):
        xmlio.clear(self.elements)                   # 电路对象要排在元件、端口、网络之后
        for item in self.comp_items + self.port_items + list(self.nodes.values()):
            self.elements.append(item)
        self.elements.append(self.circuit_item)
        for item in self.model_items:                # Multisim 把模型放在 Elements 最后
            self.elements.append(item)
        models = next(self.root.iter("Models"), None)
        if models is not None:
            for item in self.model_items:
                models.append(ET.Element("Item", {"CiID": item.get("CiID")}))
        for item in self.nodes.values():
            self.circuit.find("Nodes").append(ET.Element("Item", {"CiID": item.get("CiID")}))
        for item in self.comp_items:
            self.circuit.find("Components").append(ET.Element("Item", {"CiID": item.get("CiID")}))
        mapping = xmlio.find_tag(self.root, "CIRToInfoMap")
        if mapping is not None:
            for item in self.refdes_items:
                mapping.append(item)
        data = xmlio.find_tag(self.root, "InstrumentsData")
        for ref, state in self.instrument_states:
            apply_instrument(state, ref, instruments.get(ref, {}), params_of.get(ref, {}))
            data.append(state)
        unknown = set(instruments) - {ref for ref, _ in self.instrument_states}
        if unknown:
            raise ValueError(f"instruments 里的 {sorted(unknown)} 不是电路中的仪器")
        cid = self.circuit_item.get("CiID")
        for el in self.root.iter():
            if "Circuit" in el.attrib:
                el.set("Circuit", cid)
        self._sheet(sheet)
        if sim_time:
            state = xmlio.read(self.lib.path / "_struct" / "simstate.txt")
            self.diagram.set("SimState", state.replace("TSTOP:double{1e+030}", f"TSTOP:double{{{sim_time:g}}}"))

    def _sheet(self, sheet):
        if sheet:
            width, height = round(sheet[0] * 96), round(sheet[1] * 96)
        else:                                        # 收紧到电路范围，按 F7 时电路能占满窗口
            xs, ys = [0.0], [0.0]
            for x, y, w, h in (p["box"] for p in self.placements):
                xs.append(x + w + 96)                # 右侧留出标签位置
                ys.append(y + h + 36)
            for paths in getattr(self, "wires", {}).values():
                for path in paths:
                    xs.extend(p[0] for p in path)
                    ys.extend(p[1] for p in path)
            width = max(480, int((max(xs) + 72) // 48 + 1) * 48)
            height = max(384, int((max(ys) + 72) // 48 + 1) * 48)
        values = {"Sheet Width": width, "Sheet Height": height,
                  "Sheet Width In Inch": width / 96, "Sheet Height In Inch": height / 96}
        for setting in self.diagram.findall("./CircPrefs/CIITCircuitPrefs/Settings/Element"):
            key = xmlio.unasc(setting.get("Key", ""))
            if key in values and setting.find("Item") is not None:
                setting.find("Item").set("Value", f"&ASC{values[key]:g}")
        self.diagram.set("PageWidth", f"{width / 96:g}")
        self.diagram.set("PageHeight", f"{height / 96:g}")
        hw, hh = round(width / 96 * 25.4 / 2 * 1e6), round(height / 96 * 25.4 / 2 * 1e6)
        for s in self.root.iter("DesignSheet"):
            s.set("WorkAreaLeft", str(-hw))
            s.set("WorkAreaRight", str(hw))
            s.set("WorkAreaBottom", str(-hh))
            s.set("WorkAreaTop", str(hh))


def _layout_key(layout: dict, refdes: str):
    for key in (refdes, refdes.upper(), "X" + refdes, refdes.lstrip("Xx")):
        if key in layout:
            return layout[key]
    return None


def build(spec: dict, lib: Library | None = None) -> dict:
    lib = lib or Library()
    out = Path(spec["output"]).resolve()
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", out.stem) or out.suffix.lower() != ".ms14":
        raise ValueError("output 必须是 .ms14，文件名只能含英文、数字、下划线、短横线")
    instances = nl.parse(spec["netlist"])
    layout = spec.get("layout") or {}
    b = Builder(lib, out)
    params_of = {}
    auto = 0
    for inst in instances:
        key = lib.resolve(inst.part)
        if key is None:
            raise KeyError(f"{inst.refdes}: 元件库里没有 {inst.part!r}（`multisim-cli parts list` 查找，"
                           f"`multisim-cli parts fetch {inst.part}` 从 Multisim 数据库取）")
        info = lib.index[key]
        refdes = inst.refdes
        if refdes[0] in "Xx" and info["kind"] != "instrument" and len(refdes) > 1 and refdes[1].isalpha():
            refdes = refdes[1:]                      # XU1 → U1（Multisim 的位号习惯）
        pins = info["pins"]
        if inst.named:
            bad = [p for p in inst.named if p not in pins and p not in info["hidden_pins"]]
            if bad:
                raise ValueError(f"{inst.refdes}: {key} 没有引脚 {bad}；可用引脚 {pins + list(info['hidden_pins'])}")
            nets = dict(inst.named)
        else:
            if len(inst.nets) > len(pins):
                raise ValueError(f"{inst.refdes}: {key} 只有 {len(pins)} 个引脚 {pins}，网表给了 {len(inst.nets)} 个节点")
            nets = dict(zip(pins, inst.nets))
        pos = _layout_key(layout, inst.refdes) or _layout_key(layout, refdes)
        if pos is None:                              # 没给坐标的元件按网格排开
            pos = [150 + (auto % 5) * 180, 150 + (auto // 5) * 180]
            auto += 1
            b.warnings.append(f"{inst.refdes} 没有给坐标，已自动放置")
        b.place(refdes, key, nets, (float(pos[0]), float(pos[1])), int(pos[2]) % 360 if len(pos) > 2 else 0, inst=inst)
        params_of[refdes] = inst.params
    grounded = any("0" in (list(i.named.values()) + i.nets) for i in instances)
    grounds = layout.get("GND") or []
    if grounded and not grounds:
        raise ValueError("电路接了地（节点 0），layout 里要给接地符号位置 \"GND\": [[x, y], ...]")
    for k, g in enumerate(grounds, start=1):
        b.place(f"GND{k}", "GROUND", {"1": "0"}, (float(g[0]), float(g[1])), ground=True)

    colors = {}
    for inst in instances:                           # 示波器 B 通道导线默认蓝色
        if inst.part == "SCOPE" and len(inst.nets) > 1 and inst.nets[1] != "0":
            colors[inst.nets[1]] = COLORS["blue"]
    for net, color in (spec.get("colors") or {}).items():
        colors[nl.net_name(net)] = COLORS.get(str(color).lower(), color)
    b.wire_all(colors, bool(spec.get("net_names")))

    instruments = spec.get("instruments") or {}
    sim_time = number(spec["sim_time"]) if spec.get("sim_time") else None
    sweep = None
    scopes = [i for i in instances if i.part == "SCOPE"]
    if scopes:
        sweep = 10 * number((instruments.get(scopes[0].refdes) or {}).get("timebase", 1e-3))
        sim_time = sim_time or 20 * sweep
    b.finish(instruments, params_of, sim_time, spec.get("sheet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    xmlio.save(b.root, out)
    return {"ms14": str(out), "sim_time": sim_time, "sweep": sweep, "placements": b.placements,
            "wires": b.wires, "boxes": b.boxes, "warnings": b.warnings, "expected": b.expected}


def set_sim_time(ms14, sim_time: float):
    """只改交互式仿真结束时间（capture 对齐示波器扫描时用）。"""
    root = xmlio.load(ms14)
    diagram = next(root.iter("CIITDiagram"))
    head, sep, tail = (diagram.get("SimState") or "").partition("DEFAULT_INST_SETTINGS")
    if not sep:
        raise ValueError("文件里没有交互式仿真设置（生成时没写 sim_time）")
    tail = re.sub(r"TSTOP:double\{[^}]*\}", f"TSTOP:double{{{sim_time:g}}}", tail, count=1)
    diagram.set("SimState", head + sep + tail)
    xmlio.save(root, ms14)
