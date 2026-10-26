"""核心功能的离线测试（不需要启动 Multisim）。运行：python -m pytest tests -q"""
from pathlib import Path

import pytest

from multisim_cli import builder, check, codec, netlist, xmlio
from multisim_cli.config import CONFIG
from multisim_cli.parts import Library

SAMPLES = Path(CONFIG["samples_dir"])
needs_samples = pytest.mark.skipif(not SAMPLES.exists(), reason="没有 Multisim 示例")
needs_library = pytest.mark.skipif(not (Library().path / "index.json").exists(), reason="元件库未建立")


@needs_samples
@pytest.mark.parametrize("name", ["LowPassFilter.ms14", "Up-DownCounter.ms14", "PowerSupply.ms14"])
def test_codec_roundtrip(name):
    raw = (SAMPLES / name).read_bytes()
    xml = codec.decode(raw)
    assert xml.startswith(b"<?xml")
    assert codec.decode(codec.encode(xml)) == xml


@needs_samples
def test_xml_roundtrip_all_samples():
    """解析再输出必须与 Multisim 写的 XML 逐字节相同（只去掉个别示例里非法的 NUL）。"""
    for f in sorted(SAMPLES.rglob("*.ms14")):
        text = codec.decode(f.read_bytes()).decode("utf-8").replace("\x00", "")
        assert xmlio.to_text(xmlio.parse_text(text)) == text, f.name


def test_implode_edge_cases():
    for data in (b"", b"a", b"ab", b"abc" * 3000, bytes(range(256)) * 20):
        assert codec.explode(codec.implode(data)) == data


def test_netlist_parse():
    insts = netlist.parse("""* demo
V1 vin 0 DC 0 AC 1 SIN(0 1 1k)
R1 vin vout 4.7k
C1 vout GND 0.1u
XU1 inp inn vp vn out LM324AJ
U2 LM555CN GND=0 TRI=t OUT=o
XSC1 vin vout 0 nc1 0 nc2 OSCILLOSCOPE
XFG1 a 0 b FGEN WAVE=SQUARE FREQ=2k
.end
""")
    by = {i.refdes: i for i in insts}
    assert by["V1"].part == "AC_VOLTAGE" and by["V1"].source["freq"] == 1000 and by["V1"].source["ac"] == (1.0, 0.0)
    assert by["R1"].part == "RESISTOR_VIRTUAL" and by["R1"].nets == ["vin", "vout"]
    assert by["C1"].nets == ["vout", "0"]
    assert by["XU1"].part == "LM324AJ" and len(by["XU1"].nets) == 5
    assert by["U2"].named == {"GND": "0", "TRI": "t", "OUT": "o"}
    assert by["XSC1"].part == "SCOPE"
    assert by["XFG1"].params == {"WAVE": "SQUARE", "FREQ": "2k"}
    assert netlist.parse_value("1meg") == 1e6 and netlist.parse_value("10mV") == 0.01
    assert netlist.parse_value("1MHz") == 1e6 and netlist.parse_value("2.2M") == 2.2e6


def test_strings():
    assert xmlio.uni("1kΩ") == "&UNI1k_uc103a9"
    assert xmlio.text("&UNI1_uc100b5F") == "1µF"
    assert xmlio.text(xmlio.uni("示波器输入")) == "示波器输入"
    assert builder.eng(0.0001) == "100µ" and builder.eng(4700) == "4.7k"


RC = {
    "netlist": "V1 vin 0 DC 0 AC 1 SIN(0 1 1k)\nR1 vin vout 1k\nC1 vout 0 0.1u\nXSC1 vin vout 0 nc1 0 nc2 OSCILLOSCOPE\n",
    "layout": {"V1": [100, 260], "R1": [250, 180], "C1": [400, 260, 90], "XSC1": [560, 140],
               "GND": [[100, 350], [400, 350], [610, 230]]},
    "instruments": {"XSC1": {"timebase": "500us", "a_scale": "1V", "b_scale": "1V", "trigger": "none"}},
}


@needs_library
def test_build_and_check(tmp_path):
    spec = dict(RC, output=str(tmp_path / "rc.ms14"))
    r = builder.build(spec)
    report = check.check_file(r["ms14"], r["expected"])
    assert report["ok"], report["problems"]
    assert report["nets"]["vout"] == ["C1.1", "R1.2", "XSC1.2"]
    info = check.inspect(r["ms14"])
    assert {c["refdes"] for c in info["components"]} == {"V1", "R1", "C1", "XSC1"}
    assert info["instruments"]["XSC1"]["NI_EWB_TIMEBASE_SCALE_VALUE"] == "0.0005"


@needs_library
def test_check_catches_miswire(tmp_path):
    """把一根 vout 导线改接到 vin 的引脚上，自检必须报出来。"""
    spec = dict(RC, output=str(tmp_path / "bad.ms14"))
    r = builder.build(spec)
    root = xmlio.load(r["ms14"])
    c = check.Circuit(root)
    vin_pin = next(k for k, it in c.items.items() if it.get("Class") == "CODPinComp"
                   and any(w[0].get("Connect1") == k or w[0].get("Connect2") == k for w in c.wires.values()
                           if c.nodes[w[0].get("Node")][0].get("LocalName") == "&ASCvin"))
    wire = next(w for w in c.wires.values() if c.nodes[w[0].get("Node")][0].get("LocalName") == "&ASCvout")
    wire[0].set("Connect1", vin_pin)
    xmlio.save(root, r["ms14"])
    assert not check.check_file(r["ms14"])["ok"]
