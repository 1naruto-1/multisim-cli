"""解析电路网表（SPICE 写法 + 少量扩展），得到要放置的元件实例。

支持的行：
    R/C/L   R1 a b 1k                       电阻 / 电容 / 电感
    V       V1 a 0 DC 12 | DC 0 AC 1 SIN(0 1 1k) | PULSE(0 5 0 1u 1u 1m 2m)
    I       I1 a 0 DC 1m
    D/Q/M/J D1 a k 1N4148    Q1 c b e 2N3904    M1 d g s b 2N7000
    X/U     XU1 n1 n2 ... LM324AJ           按 `parts show` 的引脚顺序
            U1 LM555CN GND=0 TRI=n1 ...     按引脚名连接（引脚多的芯片更清楚）
    仪器    XSC1 A+ B+ A- T+ B- T- OSCILLOSCOPE
            XFG1 + COM - FGEN WAVE=SQUARE FREQ=1k AMPLITUDE=2 OFFSET=0
            XBP1 IN+ IN- OUT+ OUT- BODE
            XMM1 + - MULTIMETER
注释用 * 开头，.end 及其它点命令忽略；+ 开头的行接在上一行后面。
网络名不区分大小写，0 和 gnd 都是地。
"""
import re
from dataclasses import dataclass, field

SUFFIX = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "": 1.0, "m": 1e-3, "u": 1e-6, "µ": 1e-6,
          "n": 1e-9, "p": 1e-12, "f": 1e-15}
_NUM = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(meg|[tgkmuµnpf]?)", re.I)

# 仪器：网表关键字 → 元件库名称、位置写法的引脚顺序（端口名）
INSTRUMENTS = {
    "XSC": ({"OSCILLOSCOPE", "SCOPE", "OSC"}, "SCOPE"),
    "XFG": ({"FGEN", "FUNCTION_GENERATOR", "XFG"}, "FGEN"),
    "XBP": ({"BODE", "BODE_PLOTTER", "XBP"}, "BODE"),
    "XMM": ({"MULTIMETER", "MULMETER", "DMM"}, "MULMETER"),
}
NODE_COUNT = {"R": 2, "C": 2, "L": 2, "V": 2, "I": 2, "D": 2, "Q": 3, "J": 3, "M": 4}


def parse_value(token) -> float:
    """数值：4.7k、100n、1meg、10mV（单位字母忽略）。

    与 Multisim 界面一致区分大小写：M 是兆（1MHz、1MΩ），m 是毫；meg 不分大小写都是兆。
    """
    if isinstance(token, (int, float)):
        return float(token)
    m = _NUM.match(str(token).strip())
    if not m:
        raise ValueError(f"无法识别的数值 {token!r}")
    suffix = m.group(2)
    return float(m.group(1)) * (1e6 if suffix == "M" else SUFFIX[suffix.lower()])


def net_name(token: str) -> str:
    t = token.strip()
    return "0" if t.lower() in ("0", "gnd") else t.lower()


@dataclass
class Instance:
    refdes: str
    part: str                       # 元件库名称
    nets: list = field(default_factory=list)       # 位置写法
    named: dict = field(default_factory=dict)      # 引脚名写法 {引脚: 网络}
    value: str | None = None        # R/C/L 数值
    source: dict | None = None      # 电源参数 {"type": "dc"/"sin"/"pulse", ...}
    params: dict = field(default_factory=dict)     # 仪器参数，如 XFG 的 WAVE/FREQ
    line: str = ""


def _logical_lines(text: str):
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith(("*", ";")):
            continue
        if s.startswith("+") and lines:
            lines[-1] += " " + s[1:].strip()
        else:
            lines.append(s)
    return lines


def _source(rest: str, refdes: str) -> dict:
    """解析电压/电流源的 DC / AC / SIN / PULSE 部分。"""
    text = rest.strip()
    out = {"type": "dc", "dc": 0.0, "ac": None}
    m = re.search(r"(?i)\bsin\s*\(([^)]*)\)", text)
    if m:
        args = [parse_value(a) for a in m.group(1).replace(",", " ").split()]
        if len(args) < 3:
            raise ValueError(f"{refdes}: SIN 至少要 (偏置 峰值 频率)")
        out.update(type="sin", offset=args[0], amplitude=args[1], freq=args[2],
                   delay=args[3] if len(args) > 3 else 0.0, damping=args[4] if len(args) > 4 else 0.0,
                   phase=args[5] if len(args) > 5 else 0.0)
        text = text[:m.start()] + text[m.end():]
    m = re.search(r"(?i)\bpulse\s*\(([^)]*)\)", text)
    if m:
        args = [parse_value(a) for a in m.group(1).replace(",", " ").split()]
        if len(args) != 7:
            raise ValueError(f"{refdes}: PULSE 需要 7 个参数 (低 高 延时 上升 下降 脉宽 周期)")
        out.update(type="pulse", pulse=args)
        text = text[:m.start()] + text[m.end():]
    m = re.search(r"(?i)\bac\s+(\S+)(?:\s+([+-]?[\d.]+))?", text)
    if m:
        out["ac"] = (parse_value(m.group(1)), float(m.group(2)) if m.group(2) else 0.0)
        text = text[:m.start()] + text[m.end():]
    m = re.search(r"(?i)\bdc\s+(\S+)", text)
    if m:
        out["dc"] = parse_value(m.group(1))
        text = text[:m.start()] + text[m.end():]
    leftover = text.split()
    if leftover and out["type"] == "dc" and len(leftover) == 1:
        out["dc"] = parse_value(leftover[0])      # V1 a 0 12
    elif leftover:
        raise ValueError(f"{refdes}: 无法识别的电源参数 {' '.join(leftover)!r}")
    return out


def parse(text: str) -> list[Instance]:
    out, seen = [], set()
    for line in _logical_lines(text):
        if line.startswith("."):
            continue
        tokens = line.split()
        ref = tokens[0]
        key = ref.upper()
        if key in seen:
            raise ValueError(f"位号重复：{ref}")
        seen.add(key)
        inst = Instance(refdes=ref, part="", line=line)
        prefix3 = key[:3]
        if prefix3 in INSTRUMENTS:
            keywords, part = INSTRUMENTS[prefix3]
            idx = next((i for i, t in enumerate(tokens) if t.upper() in keywords), None)
            if idx is None:
                raise ValueError(f"{ref}: 仪器行要以 {sorted(keywords)[0]} 结尾，例如 {line} {sorted(keywords)[0]}")
            inst.part = part
            inst.nets = [net_name(t) for t in tokens[1:idx]]
            for t in tokens[idx + 1:]:
                k, _, v = t.partition("=")
                inst.params[k.upper()] = v
        elif key[0] in "RCL" and not key.startswith("LED"):
            if len(tokens) < 4:
                raise ValueError(f"{ref}: 写成 {ref} 节点1 节点2 数值")
            inst.part = {"R": "RESISTOR_VIRTUAL", "C": "CAPACITOR_VIRTUAL", "L": "INDUCTOR_VIRTUAL"}[key[0]]
            inst.nets = [net_name(t) for t in tokens[1:3]]
            inst.value = tokens[3]
            parse_value(inst.value)
            if len(tokens) > 4:
                inst.part = tokens[4]          # 指定库元件，例如电解电容
        elif key[0] in "VI":
            inst.nets = [net_name(t) for t in tokens[1:3]]
            inst.source = _source(" ".join(tokens[3:]), ref)
            kind = inst.source["type"]
            if key[0] == "V":
                inst.part = {"dc": "DC_POWER", "sin": "AC_VOLTAGE", "pulse": "PULSE_VOLTAGE"}[kind]
            else:
                if kind != "dc":
                    raise ValueError(f"{ref}: 电流源目前只支持 DC")
                inst.part = "DC_CURRENT"
        elif key[0] in "DQJM":
            n = NODE_COUNT[key[0]]
            if len(tokens) != n + 2:
                raise ValueError(f"{ref}: 写成 {ref} {' '.join(['节点'] * n)} 型号")
            inst.nets = [net_name(t) for t in tokens[1:n + 1]]
            inst.part = tokens[n + 1]
        elif key[0] in "XU" or key.startswith("LED"):
            body = tokens[1:]
            if any("=" in t for t in body):
                names = [t for t in body if "=" not in t]
                if len(names) != 1:
                    raise ValueError(f"{ref}: 引脚名写法应为 {ref} 型号 引脚=网络 ...")
                inst.part = names[0]
                for t in body:
                    if "=" in t:
                        pin, _, net = t.partition("=")
                        inst.named[pin] = net_name(net)
            else:
                if len(body) < 2:
                    raise ValueError(f"{ref}: 写成 {ref} 节点... 型号")
                inst.part = body[-1]
                inst.nets = [net_name(t) for t in body[:-1]]
        else:
            raise ValueError(f"不支持的元件行：{line}")
        out.append(inst)
    return out
