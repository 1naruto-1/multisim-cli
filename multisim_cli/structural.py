"""结构模板：空白电路壳、导线、外部引脚、结点、网络、网络名、电压探针。

做法移植自 multisim-mcp tools/extract_native_component_templates.py（MIT）：
从官方示例里各取一个现成对象当模板；空白电路壳用本机 Multisim 通过 COM 新建并保存，
保证文件格式与本机版本一致。
"""
from pathlib import Path

from . import xmlio

RLC_SAMPLE = "LabVIEW Multisim API Toolkit/RLC Values/RLC Values.ms14"
PROBE_SAMPLE = "Analog/SpeechFilter.ms14"
SIMSTATE_SAMPLE = "Non-InvertingOpAmp.ms14"     # 取它的仿真设置（含交互式仿真结束时间 TSTOP）


def _first(root, cls, pred):
    for item in root.iter("Item"):
        if item.get("Class") == cls and len(item) and pred(item[0]):
            return xmlio.deepcopy(item)
    raise ValueError(f"示例里找不到 {cls} 结构模板")


def _wire(modifier):
    def pred(w):
        m = w.find("./ElectricalObject/ModifierInfo/Element")
        return m is not None and m.get("NetModifier") == modifier
    return pred


def _pin(mobility, connect_class, minimum=1):
    def pred(p):
        conns = p.findall("./ConnectList/Item")
        return (p.get("Mobility") == mobility and len(conns) >= minimum
                and all(c.get("Class") == connect_class for c in conns))
    return pred


def shell_from_blank(blank: Path) -> "xmlio.ET.Element":
    """清空空白电路里的容器，得到电路壳。"""
    root = xmlio.load(blank)
    for diagram in root.iter("CIITDiagram"):
        elements = diagram.find("./Elements")
        composite = diagram.find("./Components/CODComposite")
        if elements is None or composite is None:
            continue
        circuit_item = next((c for c in elements if c.get("Class") == "CiCircuit"), None)
        if circuit_item is None:
            continue
        for name in ("Objects", "ReferencedComponents"):
            if composite.find(name) is not None:
                xmlio.clear(composite.find(name))
        for child in list(elements):
            if child is not circuit_item:
                elements.remove(child)
        for name in ("Nodes", "Components", "ProbeExts"):
            node = circuit_item.find(f"./CiCircuit/{name}")
            if node is not None:
                xmlio.clear(node)
        break
    for tag in ("InstrumentsData", "CIRToInfoMap", "TriggerSet"):
        for node in root.iter(tag):
            xmlio.clear(node)
    return root


def extract(samples: Path, out: Path, blank: Path | None = None) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    rlc = xmlio.load(samples / RLC_SAMPLE)
    templates = {
        "node_v0.xml": _first(rlc, "CiNode", lambda n: n.get("LocalName") == "&ASC0"),
        "node_named.xml": _first(rlc, "CiNode", lambda n: n.get("LocalName") not in (None, "", "&ASC0")),
        "nodetext.xml": _first(rlc, "CODNodeTextComp", lambda n: True),
        "wire.xml": _first(rlc, "CIITLinkComp", _wire("&ASCNI_EWB_NET_AUTONAMED")),
        "wire_named.xml": _first(rlc, "CIITLinkComp", _wire("&ASCNI_EWB_NET_NAME")),
        "extpin.xml": _first(rlc, "CODPinComp", _pin("1", "CIITPinConnectorComp")),
        "junction_member.xml": _first(rlc, "CODPinComp", _pin("1", "CODPinComp")),
        "junction_owner.xml": _first(rlc, "CODPinComp", _pin("0", "CODPinComp", 2)),
    }
    probe_root = xmlio.load(samples / PROBE_SAMPLE)
    element = next(i for i in probe_root.iter("Item") if i.find("CiProbeExtComp") is not None)
    symbol = next(i for i in probe_root.iter("Item") if i.find("CIITProbeExtComponent") is not None
                  and i.find("CIITProbeExtComponent").get("CiProbeExtComp") == element.get("CiID"))
    package = symbol.find("CIITProbeExtComponent").get("FileDataPackageID")
    state = next(n for n in probe_root.iter("CSourceSymbolCollectNode") if n.get("CompLongName") == package)
    templates.update({"probe_element.xml": xmlio.deepcopy(element), "probe_symbol.xml": xmlio.deepcopy(symbol),
                      "probe_instrument.xml": xmlio.deepcopy(state)})
    if blank is not None:
        templates["shell.xml"] = shell_from_blank(blank)
    for name, element in templates.items():
        xmlio.write(out / name, xmlio.to_text(element))
    state = next(xmlio.load(samples / SIMSTATE_SAMPLE).iter("CIITDiagram")).get("SimState")
    xmlio.write(out / "simstate.txt", state)
    return sorted(templates) + ["simstate.txt"]
