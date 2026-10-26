"""仿真取数：直接对 .ms14 做 Multisim 自己的分析，或者运行任意 SPICE 文本。

对 .ms14：复制一份、在要测的网络上放电压探针（探针 = Multisim 的输出变量），
再通过 COM 做直流工作点 / 交流扫描 / 瞬态分析。网表由 Multisim 自己生成，任何元件都能算。
探针做法移植自 multisim-mcp schematic_builder._add_probes（MIT）。
"""
import cmath
import csv
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from . import com, xmlio
from .config import CONFIG
from .parts import Library, identity


# ---------------------------------------------------------------------------
# 探针
# ---------------------------------------------------------------------------
def _blocked(point, paths):
    px, py = point
    for path in paths:
        for (sx, sy), (ex, ey) in zip(path, path[1:]):
            if abs((px - sx) * (ey - sy) - (py - sy) * (ex - sx)) > 1e-6:
                continue
            if min(sx, ex) - 1e-6 <= px <= max(sx, ex) + 1e-6 and min(sy, ey) - 1e-6 <= py <= max(sy, ey) + 1e-6:
                return True
    return False


def _probe_point(paths, others, pins):
    """探针放在元件引脚处的导线端点上。

    Multisim 14 会丢掉放在结点、仪器端子上的探针，也会丢掉放在接电容/电感导线中段的探针。
    """
    ends = [q for p in paths for q in (p[0], p[-1]) if q in pins and not _blocked(q, others)]
    if ends:
        return max(ends, key=lambda q: (q[0], -q[1]))
    for path in paths:                           # 没有合适的引脚端点时退而求其次
        for a, b in zip(path, path[1:]):
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) >= 24 and not _blocked(mid, others):
                return mid
    return None


def net_wires(root):
    """返回 ({网络名: [导线折线, ...]}, 元件引脚坐标集合)；仪器端子和结点不算元件引脚。"""
    names = {it.get("CiID"): xmlio.unasc(it[0].get("LocalName")).lower()
             for it in root.iter("Item") if it.get("Class") == "CiNode" and len(it)}
    out = {}
    for it in root.iter("Item"):
        if it.get("Class") != "CIITLinkComp" or not len(it):
            continue
        w = it[0]
        net = names.get(w.get("Node"))
        if net is None:
            continue
        pts = [(float(p.get("X")), float(p.get("Y"))) for p in w.find("Points")]
        out.setdefault(net, []).append(pts)
    instruments = {it.get("CiID") for it in root.iter("Item") if it.get("Class") == "CiComponent" and len(it)
                   and (identity(it[0]) or ("", "", ""))[2] == "instrument"}
    owner = {}                                   # 连接器 ID → 是否属于仪器
    for it in root.iter("Item"):
        if it.get("Class") == "CIITSymbolComp" and len(it):
            is_inst = it[0].get("CiComponent") in instruments
            for c in it.iter("Item"):
                if c.get("Class") == "CIITPinConnectorComp" and len(c):
                    owner[c.get("ID")] = is_inst
    pins = set()
    for it in root.iter("Item"):
        if it.get("Class") == "CODPinComp" and len(it):
            link = it[0].find("ConnectList/Item")
            if link is not None and link.get("Class") == "CIITPinConnectorComp" and owner.get(link.get("ID")) is False:
                pins.add((float(it[0].get("CenterX")), float(it[0].get("CenterY"))))
    return out, pins


def add_probes(root, nets, lib: Library, ms14_path: str) -> dict:
    """在指定网络上放电压探针，返回 {网络: 输出变量名}。"""
    wires, pins = net_wires(root)
    diagram = next(d for d in root.iter("CIITDiagram") if d.find("./Elements") is not None
                   and any(c.get("Class") == "CiCircuit" for c in d.find("./Elements")))
    composite = diagram.find("./Components/CODComposite")
    elements = diagram.find("./Elements")
    circuit_item = next(c for c in elements if c.get("Class") == "CiCircuit")
    objects = composite.find("Objects")
    probe_exts = circuit_item[0].find("ProbeExts")
    data = xmlio.find_tag(root, "InstrumentsData")
    container = xmlio.find_tag(root, "RefDesInfoContainer")
    cir_map = container.find("CIRToInfoMap")
    prefix_map = xmlio.find_tag(root, "RefDesPrefixUsageMap")
    triggers = xmlio.find_tag(root, "TotalProbeTriggers")
    trigger_set = triggers.find("TriggerSet")
    if trigger_set is None:
        trigger_set = ET.SubElement(triggers, "TriggerSet")
    circuit_name = Path(ms14_path).stem
    tree_instance = container.get("CIR") or f"&ASC#1/{circuit_name}:"
    ids = xmlio.IdAllocator(950_000_000)
    next_id = int(triggers.get("NextProbeID") or 1)
    outputs = {}
    for index, net in enumerate(nets, start=1):
        net = net.lower()
        if net not in wires:
            raise ValueError(f"网络 {net!r} 上没有导线，无法放探针；现有网络 {sorted(wires)}")
        point = _probe_point(wires[net], [p for n, ps in wires.items() if n != net for p in ps], pins)
        if point is None:
            raise ValueError(f"网络 {net!r} 上找不到放探针的位置")
        refdes = f"PR{index}"
        element = lib.struct("probe_element.xml")
        symbol = lib.struct("probe_symbol.xml")
        state = lib.struct("probe_instrument.xml")
        xmlio.remap([element, symbol], ids)
        el, sy = element[0], symbol[0]
        el.set("LocalName", xmlio.asc(refdes))
        el.set("SymCompID", symbol.get("ID"))
        el.set("Circuit", circuit_item.get("CiID"))
        sy.set("CiProbeExtComp", element.get("CiID"))
        sy.set("Transformer-M20", f"{point[0]:g}")
        sy.set("Transformer-M21", f"{point[1]:g}")
        sy.set("UniqueID", str(next_id))
        sy.set("ShowInfo", "0")
        sy.set("Hidden", "0")
        package = f"&ASCX_MSCLI_{symbol.get('ID')}"
        sy.set("FileDataPackageID", package)
        state.set("CompLongName", package)
        for node in list(symbol.iter()) + list(state.iter()):
            if node.get("Key") == "&ASCNI_EWB_COMPHANDLE_EXT" and node.find("CDataElement") is not None:
                node.find("CDataElement").set("Data", symbol.get("ID"))
        objects.append(symbol)
        elements.append(element)
        probe_exts.append(ET.Element("Item", {"CiID": element.get("CiID")}))
        data.append(state)
        text = f"&ASC!0!0!0{refdes}!0{ms14_path}!01!0{circuit_name}!0"
        item = ET.SubElement(cir_map, "CIRToInfoMapItem", {"CIRKey": xmlio.asc(refdes)})
        info = ET.SubElement(item, "RefDesInfo", {"Class": "CIITHierRefDesInfo", "IRPrefix": "&ASCOutProbe",
                                                  "IRNumber": str(-1 if index == 1 else index - 1), "Locked": "0",
                                                  "IRSection": "", "IRSectionID": "0", "SpiceTemplate": ""})
        rd = ET.SubElement(info, "RefDesInfoData", {
            "Class": "CIITHierRefDes", "RefDesBufSize": "260", "RefDesStrSize": str(len(text)), "RefDesCount": "2",
            "RefDes": text, "SectionBufSize": "0", "SectionStrSize": "0", "Section": "&ASC(null)",
            "Prefix": "&ASCPR", "Number": str(index)})
        ET.SubElement(rd, "RefDesData")
        for tag in ("PinOrderCIR", "PinOrderIR", "PinNumbersCIR", "PinNumbersIR", "SharedPins"):
            ET.SubElement(info, tag)
        trig = ET.SubElement(trigger_set, "InstProbeTriggers", {"TreeInstance": tree_instance, "ProbeID": str(next_id)})
        pt = ET.SubElement(ET.SubElement(trig, "Triggers"), "ProbeTriggers", {"Class": "&ASCCProbeTriggers", "NumTriggers": "0"})
        ET.SubElement(pt, "Triggers")
        next_id += 1
        outputs[net] = f"V(OutProbe{'' if index == 1 else index - 1})"
    triggers.set("NextProbeID", str(next_id))
    usage = ET.SubElement(prefix_map, "RefDesPrefixUsage", {"RefDes": "&ASCOUTPROBE", "Class": "CIITRefDesPrefixUsage",
                                                            "Prefix": "&ASCOutProbe", "NextNumber": str(len(nets))})
    inner = ET.SubElement(usage, "RefDesPrefixUsage")
    for k in range(len(nets)):
        ET.SubElement(inner, "NumbersUsedVTwo", {"NumberUsed": str(k)})
    ET.SubElement(usage, "MultisectionUsage")
    return outputs


# ---------------------------------------------------------------------------
# 结果整理
# ---------------------------------------------------------------------------
def _interp_log(f, y, x):
    """对数频率轴上线性插值。"""
    lx = math.log10(x)
    for k in range(len(f) - 1):
        a, b = math.log10(f[k]), math.log10(f[k + 1])
        if a <= lx <= b:
            t = 0 if b == a else (lx - a) / (b - a)
            return y[k] + t * (y[k + 1] - y[k])
    return y[0] if x < f[0] else y[-1]


def _unwrap(phases):
    out, offset = [], 0.0
    for k, p in enumerate(phases):
        if k:
            d = p + offset - out[-1]
            offset -= 360 * round(d / 360)
        out.append(p + offset)
    return out


def ac_summary(freq, h, at=()):
    """幅频/相频要点：峰值（谐振频率）、-3 dB 频率及其相位、指定频率处的增益和相位。"""
    mag = [abs(v) for v in h]
    phase = _unwrap([math.degrees(cmath.phase(v)) for v in h])
    k = max(range(len(mag)), key=mag.__getitem__)
    level = mag[k] / math.sqrt(2)
    cutoffs = []
    for i in range(len(mag) - 1):
        if (mag[i] - level) * (mag[i + 1] - level) < 0:
            t = (level - mag[i]) / (mag[i + 1] - mag[i])
            fc = 10 ** (math.log10(freq[i]) + t * (math.log10(freq[i + 1]) - math.log10(freq[i])))
            cutoffs.append(fc)

    def point(x):
        g = _interp_log(freq, mag, x)
        return {"f": x, "gain": g, "dB": 20 * math.log10(g) if g > 0 else None, "phase_deg": _interp_log(freq, phase, x)}

    return {"peak": {"f": freq[k], "gain": mag[k], "dB": 20 * math.log10(mag[k]) if mag[k] > 0 else None},
            "cutoff": [point(x) for x in cutoffs], "at": [point(x) for x in at]}


def tran_summary(t, v):
    n = len(v)
    mean = sum(v) / n
    rms = math.sqrt(sum(x * x for x in v) / n)
    crossings = [t[i] for i in range(n - 1) if (v[i] - mean) < 0 <= (v[i + 1] - mean)]
    period = (crossings[-1] - crossings[0]) / (len(crossings) - 1) if len(crossings) > 2 else None
    return {"max": max(v), "min": min(v), "pp": max(v) - min(v), "mean": mean, "rms": rms,
            "period": period, "freq": 1 / period if period else None}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def run(ms14: str, nets: list, op=False, ac=None, tran=None, ac_out=None, ac_in=None, at=(), csv_dir=None,
        lib: Library | None = None) -> dict:
    """ac = (起始频率, 终止频率, 每十倍频点数)；tran = (时长, 采样率或 None)。"""
    lib = lib or Library()
    nets = [n.lower() for n in dict.fromkeys(list(nets) + [n for n in (ac_out, ac_in) if n])]
    if not nets:
        raise ValueError("至少指定一个要测的网络")
    root = xmlio.load(ms14)
    work = Path(CONFIG["work_dir"]) / "probe"
    work.mkdir(parents=True, exist_ok=True)
    probe_file = work / Path(ms14).name          # 文件名（电路名）必须不变，探针和仪器记录靠它匹配
    outputs = add_probes(root, [n for n in nets if n != "0"], lib, str(probe_file))
    xmlio.save(root, probe_file)
    analyses = []
    if op:
        analyses.append({"type": "op", "outputs": list(outputs.values())})
    if ac:
        analyses.append({"type": "ac", "start": ac[0], "stop": ac[1], "points": ac[2], "outputs": list(outputs.values())})
    if tran:
        analyses.append({"type": "tran", "time": tran[0], "rate": tran[1], "outputs": list(outputs.values())})
    raw = com.call({"op": "analyze", "ms14": str(probe_file), "analyses": analyses})
    by_output = {v: k for k, v in outputs.items()}
    result = {"outputs": outputs}
    for a in raw["results"]:
        if a["missing"]:
            result.setdefault("warnings", []).append(f"{a['type']} 分析没拿到 {a['missing']}：{a['last_error']}")
        data = {by_output[k]: rows for k, rows in a["data"].items()}
        if a["type"] == "op":
            result["op"] = {net: rows[1][0] if len(rows) > 1 else rows[0][0] for net, rows in data.items()}
        elif a["type"] == "ac" and data:
            freq = data[next(iter(data))][0]
            cplx = {net: [complex(r, i) for r, i in zip(rows[1], rows[2])] for net, rows in data.items()}
            out = {"points": len(freq)}
            if ac_out and ac_out.lower() in cplx:
                num = cplx[ac_out.lower()]
                den = cplx.get((ac_in or "").lower()) or [1] * len(num)
                if any(abs(v) == 0 for v in den) or all(abs(v) == 0 for v in num):
                    result.setdefault("warnings", []).append(
                        "交流分析结果全为 0：信号源要带交流幅值（网表写 AC 1，例如 DC 0 AC 1 SIN(...)）")
                else:
                    out.update(ac_summary(freq, [a_ / b_ for a_, b_ in zip(num, den)], at))
            result["ac"] = out
            if csv_dir:
                _write_csv(Path(csv_dir) / "ac.csv", ["freq"] + [f"{n}_{p}" for n in cplx for p in ("mag", "phase_deg")],
                           [[f] + [x for n in cplx for x in (abs(cplx[n][k]), math.degrees(cmath.phase(cplx[n][k])))]
                            for k, f in enumerate(freq)])
        elif a["type"] == "tran":
            result["tran"] = {net: tran_summary(rows[0], rows[1]) for net, rows in data.items()}
            if csv_dir and data:
                t = data[next(iter(data))][0]
                _write_csv(Path(csv_dir) / "tran.csv", ["time"] + list(data),
                           [[t[k]] + [data[n][1][k] for n in data] for k in range(len(t))])
    return result


def _write_csv(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def spice(netlist_text: str, commands: list) -> dict:
    """任意 SPICE 文本交给 Multisim 的命令行仿真引擎（DoCommandLine）。"""
    work = Path(CONFIG["work_dir"]) / "spice"
    if " " in str(work):
        raise ValueError("work_dir 不能含空格（Multisim 命令行引擎的限制）")
    res = com.call({"op": "spice", "netlist": netlist_text, "commands": commands, "work": str(work)})
    if res.get("raw"):
        res["vectors"] = read_raw(res["raw"])
    return res


def read_raw(path) -> dict:
    """读 ASCII SPICE raw 文件：{变量名: [值...]}，复数值返回 complex。"""
    lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    i = next(k for k, line in enumerate(lines) if line.startswith("Variables:")) + 1
    names = []
    while not lines[i].startswith("Values:"):
        names.append(lines[i].split("\t")[-1].split()[0] if "\t" in lines[i] else lines[i].split()[1])
        i += 1
    values = []
    for line in lines[i + 1:]:
        tok = line.split()
        if not tok:
            continue
        v = tok[-1]
        if "," in v:
            re_, im = v.split(",")
            values.append(complex(float(re_), float(im)))
        else:
            values.append(float(v))
    n = len(names)
    return {name.lower(): values[k::n] for k, name in enumerate(names)}
