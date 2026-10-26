"""元件模板库：从 .ms14 采集元件，按名称查询。

一个元件模板 = 原文件里属于它的全部对象：CiComponent、各 CiPort、引用的 CiModel、
原理图符号，以及（虚拟仪器的）面板状态和（多单元器件的）RefDes 记录。
生成电路时整组复制、换新 ID，所以任何在某个 .ms14 里出现过的元件都能用。
"""
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from . import xmlio
from .config import CONFIG, SKILL_DIR, library_dir

STRUCT = "_struct"
# 示波器端口 1/4 是 A 通道 +/-，2/5 是 B 通道，3/6 是外触发；网表写法 A+ B+ A- T+ B- T-
INSTRUMENT_PINS = {"SCOPE": ["1", "2", "4", "3", "5", "6"]}
# 仪器端口的含义（parts show 里标出来）
INSTRUMENT_PIN_NAMES = {
    "SCOPE": {"1": "A+", "4": "A-", "2": "B+", "5": "B-", "3": "外触发+", "6": "外触发-"},
    "FGEN": {"1": "+", "2": "COM", "3": "-"},
    "BODE": {"1": "IN+", "2": "IN-", "3": "OUT+", "4": "OUT-"},
    "MULMETER": {"1": "+", "2": "-"},
}
# 基本元件优先采用 multisim-mcp 验证过的那几个示例实例（示例、位号）
PREFERRED = [
    ("LabVIEW Multisim API Toolkit/RLC Values/RLC Values.ms14", "R1"),
    ("LowPassFilter.ms14", "C9"),
    ("LowPassFilter.ms14", "V1"),
    ("Non-InvertingOpAmp.ms14", "V1"),
    ("Analyses/Worst Case - Speech Filter.ms14", "V5"),
    ("LowPassFilter.ms14", "0"),
    ("Analyses/Monte Carlo - RLC Circuit.ms14", "L1"),
    ("Analyses/Batched Analyses - CMOS Common Source Amplifier.ms14", "Iref"),
    ("Analog/ClassBPushPullAmp.ms14", "D1"),
    ("Analog/ClassBPushPullAmp.ms14", "Q1"),
    ("Analog/ClassBPushPullAmp.ms14", "Q2"),
    ("Analyses/DC Sweep - CMOS Inverter.ms14", "Q1"),
    ("Analyses/DC Sweep - CMOS Inverter.ms14", "Q2"),
    ("LowPassFilter.ms14", "U4"),
    ("Non-InvertingOpAmp.ms14", "U3"),
    ("LowPassFilter.ms14", "XSC1"),
    ("Non-InvertingOpAmp.ms14", "XFG1"),
]


def _s(value):
    return xmlio.unasc(value)


def _strings(comp):
    return [[_s(i.get("Value")) for i in c.find("strings")] for c in comp.iter("CiaCollString")]


def identity(comp):
    """(名称, 族, 类别)。数据库元件取元件名；虚拟仪器取仪器类型。"""
    colls = _strings(comp)
    c0 = (colls[0] if colls else []) + [""] * 4
    c1 = (colls[1] if len(colls) > 1 else []) + [""] * 6
    if c1[0] == "91":                      # 页内连接器/网络标签
        return None
    if c1[0] == "40":
        return c1[3] or c1[1].upper(), "INSTRUMENT", "instrument"
    if not c0[1]:
        return None
    kind = "ground" if c0[1] == "GROUND" else "component"
    return c0[1], c0[0], kind


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.+\-]", "_", name)


def spice_template(comp) -> str:
    t = next(comp.iter("CiaSpiceTmpltExprt"), None)
    return _s(t.get("String")) if t is not None else ""


def spice_pin_order(template: str, names: list[str]) -> list[str]:
    """按 SPICE 模板里 %t<引脚名> 出现的顺序排列引脚；没出现的接在后面。"""
    order = []
    by_len = sorted(names, key=len, reverse=True)
    for m in re.finditer(r"%t", template):
        rest = template[m.end():]
        hit = next((n for n in by_len if rest.startswith(n)), None)
        if hit and hit not in order:
            order.append(hit)
    return order + [n for n in names if n not in order]


class Harvester:
    """从一个解码后的电路里取出元件模板。"""

    def __init__(self, root: ET.Element, source: str):
        self.root = root
        self.source = source
        self.items = {}
        for it in root.iter("Item"):
            if len(it):
                key = it.get("CiID") or it.get("ID")
                if key:
                    self.items[key] = it
        self.symbol_of = {}
        for it in root.iter("Item"):
            if it.get("Class") in ("CIITSymbolComp", "CIITAnimationSymbolComp") and len(it):
                sym = it[0]
                if sym.get("CiComponent"):
                    # 样例里的 CIITSymbolComp 优先于动画层副本
                    if sym.get("CiComponent") not in self.symbol_of or it.get("Class") == "CIITSymbolComp":
                        self.symbol_of[sym.get("CiComponent")] = it
        self.instruments = list(root.iter("CSourceSymbolCollectNode"))
        self.refdes_infos = list(root.iter("CIRToInfoMapItem"))

    def components(self):
        for it in self.root.iter("Item"):
            if it.get("Class") == "CiComponent" and it.find("CiComponent") is not None:
                yield it

    def extract(self, comp_item):
        """返回 Part 元素；无法独立复用（没有符号）时返回 None。"""
        comp = comp_item.find("CiComponent")
        ident = identity(comp)
        if ident is None:
            return None
        name, family, kind = ident
        ref = _s(comp.get("LocalName"))
        sym_item = self.items.get(comp.get("SymCompID") or "")
        if sym_item is None or sym_item.get("Class") not in ("CIITSymbolComp", "CIITAnimationSymbolComp"):
            sym_item = self.symbol_of.get(comp_item.get("CiID"))
        if sym_item is None:
            return None
        sym_item = xmlio.deepcopy(sym_item)
        for node in sym_item.iter():
            if node.tag == "CIITAnimationSymbolComp":
                node.tag = "CIITSymbolComp"
            if node.get("Class") == "CIITAnimationSymbolComp":
                node.set("Class", "CIITSymbolComp")
        sym = sym_item[0]
        for key, value in (("M00", "1."), ("M01", "0."), ("M10", "0."), ("M11", "1."), ("M20", "0."), ("M21", "0.")):
            sym.set("Transformer-" + key, value)

        ports = []
        for ref_item in comp.find("Ports") if comp.find("Ports") is not None else []:
            port = self.items.get(ref_item.get("CiID"))
            if port is not None and port.find("CiPort") is not None:
                ports.append(port)
        visible = {p.get("PortID") for p in visible_pins(sym)}
        hidden = []
        for port in ports:
            if port.get("CiID") not in visible:
                nodes = port.find("CiPort/Nodes")
                node = self.items.get(nodes[0].get("CiID")) if nodes is not None and len(nodes) else None
                net = _s(node[0].get("LocalName")) if node is not None else ""
                hidden.append((_s(port[0].get("LocalName")), net))

        models, seen = [], set()
        for owner in [comp] + [p[0] for p in ports]:
            mid = owner.get("Model")
            model = self.items.get(mid or "")
            if model is not None and model.get("Class") == "CiModel" and mid not in seen:
                seen.add(mid)
                models.append(model)

        part = ET.Element("Part", {"Name": name, "Family": family, "Kind": kind,
                                   "Source": self.source, "SourceRef": ref})
        group = ET.SubElement(part, "Items")
        for it in [comp_item, *ports, *models, sym_item]:
            group.append(xmlio.deepcopy(it))
        if kind == "instrument":
            marker = f"&ASC{ref}#".upper()
            state = next((n for n in self.instruments if (n.get("CompLongName") or "").upper().startswith(marker)), None)
            if state is not None:
                ET.SubElement(part, "Instrument").append(xmlio.deepcopy(state))
        section = _s(sym.get("IRSection"))
        info = next((i for i in self.refdes_infos if _s(i.get("CIRKey")) == ref + section), None)
        if info is not None and section:
            ET.SubElement(part, "RefDesInfo").append(xmlio.deepcopy(info))
        for net_name, net in hidden:
            ET.SubElement(part, "HiddenPin", {"Name": net_name, "Net": net})
        return part


def visible_pins(sym):
    """符号上画出来的引脚（HiddenPins 下的是隐藏的电源脚）。"""
    return sym.findall("./Objects/Item[@Class='CIITPinSymbolComp']/CIITPinSymbolComp")


# ---------------------------------------------------------------------------
# 模板库
# ---------------------------------------------------------------------------
class Library:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or library_dir())
        self.index_path = self.path / "index.json"
        self.index = json.loads(self.index_path.read_text(encoding="utf-8")) if self.index_path.exists() else {}
        self._lower = {k.lower(): k for k in self.index}

    def save_index(self):
        self.path.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.index, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        self._lower = {k.lower(): k for k in self.index}

    def resolve(self, name: str) -> str | None:
        return self._lower.get(name.lower())

    def load(self, name: str) -> ET.Element:
        key = self.resolve(name)
        if key is None:
            raise KeyError(f"元件库里没有 {name!r}；用 `multisim-cli parts list {name[:4]}` 查找，"
                           f"或在 Multisim 里放一个该元件、保存后执行 `multisim-cli parts harvest 文件.ms14`")
        return xmlio.parse_text(xmlio.read(self.path / self.index[key]["file"]))

    def struct(self, name: str) -> ET.Element:
        """结构模板：空白电路壳、导线、外部引脚、结点等。"""
        return xmlio.parse_text(xmlio.read(self.path / STRUCT / name))

    def add(self, part: ET.Element, force: bool = False) -> bool:
        name = part.get("Name")
        if self.resolve(name) and not force:
            return False
        if self.resolve(name):
            name = self.resolve(name)
        info = describe(part)
        rel = f"{safe_name(name)}.xml"
        self.path.mkdir(parents=True, exist_ok=True)
        xmlio.write(self.path / rel, xmlio.to_text(part))
        info["file"] = rel
        self.index[name] = info
        self._lower[name.lower()] = name
        return True

    def search(self, query: str = ""):
        q = query.lower()
        for name, info in sorted(self.index.items()):
            if not q or q in name.lower() or q in info.get("family", "").lower():
                yield name, info


def describe(part: ET.Element) -> dict:
    items = part.find("Items")
    comp = items.find("Item/CiComponent")
    ports = {it.get("CiID"): _s(it[0].get("LocalName")) for it in items if it.get("Class") == "CiPort"}
    sym = items.find("Item[@Class='CIITSymbolComp']/CIITSymbolComp")
    visible = [ports[p.get("PortID")] for p in visible_pins(sym) if p.get("PortID") in ports]
    template = spice_template(comp)
    if part.get("Kind") == "instrument":       # 仪器按网表写法的端口顺序
        order = INSTRUMENT_PINS.get(part.get("Name"))
        visible.sort(key=lambda n: order.index(n) if order and n in order else (int(n) if n.isdigit() else 999))
    colls = _strings(comp)
    prefix = re.sub(r"%d.*", "", (colls[1][4] if len(colls) > 1 and len(colls[1]) > 4 else "")) or "U"
    return {
        "family": part.get("Family"),
        "kind": part.get("Kind"),
        "prefix": prefix,
        "pins": visible if part.get("Kind") == "instrument" else spice_pin_order(template, visible),
        "hidden_pins": {h.get("Name"): h.get("Net") for h in part.findall("HiddenPin")},
        "sections": int(sym.get("NumSections") or 1),
        "template": template.strip()[:160],
        "source": f"{part.get('Source')}:{part.get('SourceRef')}",
    }


def harvest_path(target: Path, lib: Library, force: bool = False, label_root: Path | None = None) -> dict:
    """采集一个 .ms14 或整个目录；返回 {新增: [...], 跳过: n, 失败: [...]}。"""
    files = sorted(target.rglob("*.ms14")) if target.is_dir() else [target]
    added, skipped, failed = [], 0, []
    # 目录采集时先单独取首选实例（每项只取指定位号），再全量采集
    jobs = [(target / rel, {ref}) for rel, ref in PREFERRED if (target / rel).is_file()] if target.is_dir() else []
    jobs += [(f, None) for f in files]
    cache = {}
    for f, only in jobs:
        try:
            root = cache.get(f) or xmlio.load(f)
        except Exception as exc:          # 个别文件损坏不影响其它
            failed.append(f"{f.name}: {exc}")
            continue
        if only:
            cache[f] = root
        label = f.relative_to(label_root or (target if target.is_dir() else f.parent)).as_posix()
        h = Harvester(root, label)
        comps = [it for it in h.components() if only is None or _s(it[0].get("LocalName")) in only]
        for comp_item in comps:
            try:
                part = h.extract(comp_item)
            except Exception as exc:
                failed.append(f"{label}:{_s(comp_item[0].get('LocalName'))}: {exc}")
                continue
            if part is None:
                continue
            if lib.add(part, force=force and target.is_file()):
                added.append(part.get("Name"))
            else:
                skipped += 1
    lib.save_index()
    return {"added": added, "skipped": skipped, "failed": failed}


def init_library(lib: Library) -> dict:
    """首次建库：结构模板（空白电路壳由本机 Multisim 新建）+ NI 官方示例里的全部元件。"""
    from . import com, structural
    samples = Path(CONFIG["samples_dir"])
    struct_dir = lib.path / STRUCT
    struct_dir.mkdir(parents=True, exist_ok=True)
    blank = struct_dir / "blank.ms14"
    com.call({"op": "blank", "out": str(blank)})
    structural.extract(samples, struct_dir, blank)
    blank.unlink()
    return harvest_path(samples, lib, label_root=samples)


# ---------------------------------------------------------------------------
# 从 Multisim 数据库取元件：在载体电路里用 COM ReplaceComponent 换成目标元件，再采集
# ---------------------------------------------------------------------------
# 载体必须与目标同类，否则 ReplaceComponent 会让进程崩溃：模拟器件用三极管，数字器件用 7404N
CARRIERS = [("Q1", "Q1 a b c 2N3904"), ("U1", "U1 7404N 1A=a 1Y=b")]


def build_db_index(lib: Library) -> dict:
    """读主元件库（Jet 数据库），生成 {元件名: [组, 族]}。需要 Node.js 和 electronics-workbench-decoder。"""
    out = lib.path / "db_index.json"
    r = subprocess.run(["node", str(SKILL_DIR / "tools" / "dbindex.js"), CONFIG["master_db"], str(out),
                        CONFIG["node_decoder"]], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"读取主元件库失败：{r.stderr.strip()[-500:]}")
    return {"components": int(r.stdout.strip() or 0), "index": str(out)}


def db_lookup(lib: Library, name: str):
    path = lib.path / "db_index.json"
    if not path.exists():
        build_db_index(lib)
    index = json.loads(path.read_text(encoding="utf-8"))
    hit = index.get(name) or next((v for k, v in index.items() if k.lower() == name.lower()), None)
    if hit is None:
        close = [k for k in index if name.lower() in k.lower()][:15]
        raise KeyError(f"Multisim 数据库里没有 {name!r}；相近的有 {close}")
    return hit


def fetch(name: str, lib: Library, group=None, family=None) -> dict:
    from . import builder, com
    if lib.resolve(name):
        return {"name": lib.resolve(name), "already": True}
    if not (group and family):
        group, family = db_lookup(lib, name)
    work = Path(CONFIG["work_dir"]) / "fetch"
    work.mkdir(parents=True, exist_ok=True)
    tried = []
    for ref, line in CARRIERS:
        carrier = work / f"carrier_{ref}.ms14"
        builder.build({"netlist": line, "output": str(carrier), "layout": {ref: [300, 300]}}, lib)
        out = work / f"{safe_name(name)}.ms14"
        try:
            com.call({"op": "replace", "ms14": str(carrier), "ref": ref, "group": group,
                      "family": family, "name": name, "out": str(out)}, timeout=180)
        except com.ComError as exc:
            tried.append(f"{ref}: {exc}")
            continue
        root = xmlio.load(out)
        h = Harvester(root, f"Multisim 数据库 {group}/{family}")
        for comp_item in h.components():
            part = h.extract(comp_item)
            if part is not None and part.get("Name").lower() == name.lower():
                lib.add(part)
                lib.save_index()
                return {"name": part.get("Name"), "group": group, "family": family, **describe(part)}
        tried.append(f"{ref}: 替换后文件里没有 {name}")
    raise RuntimeError(f"没能取到 {name}（{group}/{family}）：{tried}")
