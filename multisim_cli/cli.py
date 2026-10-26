"""multisim-cli 命令行入口。所有命令把结果以 JSON 打印到标准输出。"""
import argparse
import json
import sys
from pathlib import Path


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def _floats(text, n=None):
    from .netlist import parse_value
    vals = [parse_value(t) for t in text.split(",") if t.strip()]
    if n and len(vals) not in n:
        raise SystemExit(f"参数 {text!r} 的个数不对")
    return vals


def cmd_parts(a):
    from . import parts
    lib = parts.Library()
    if a.action == "init":
        r = parts.init_library(lib)
        _out({"library": str(lib.path), "added": len(r["added"]), "failed": r["failed"]})
    elif a.action == "list":
        rows = [f"{n}  [{i['family']}]  {' '.join(i['pins'])}" for n, i in lib.search(a.arg or "")]
        _out({"count": len(rows), "parts": rows})
    elif a.action == "show":
        key = lib.resolve(a.arg or "")
        if key is None:
            raise SystemExit(f"元件库里没有 {a.arg!r}（可试 `parts fetch {a.arg}`）")
        pin_xy, body = _pin_xy(lib, key)
        _out({"name": key, **lib.index[key], "pin_xy": pin_xy, "body": body})
    elif a.action == "harvest":
        r = parts.harvest_path(Path(a.arg), lib, force=a.force)
        _out(r)
    elif a.action == "fetch":
        _out(parts.fetch(a.arg, lib, group=a.group, family=a.family))
    elif a.action == "dbindex":
        _out(parts.build_db_index(lib))


def _pin_xy(lib, key):
    """0° 放置时各引脚相对引脚中心的坐标、引出方向，以及符号本体范围，规划布局用。"""
    from . import builder, check, parts, xmlio
    part = lib.load(key)
    items = part.find("Items")
    sym = items.find("Item[@Class='CIITSymbolComp']")
    ports = [i for i in items if i.get("Class") == "CiPort"]
    builder._rotate(sym, builder._base_angle(sym, ports, lib.index[key]["pins"]))
    pins = builder.pin_info(sym)
    if not pins:
        return {}, ""
    name_of = {p.get("CiID"): xmlio.unasc(p[0].get("LocalName")) for p in ports}
    xs, ys = [p["x"] for p in pins.values()], [p["y"] for p in pins.values()]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    words = {(-1, 0): "向左", (1, 0): "向右", (0, -1): "向上", (0, 1): "向下"}
    meaning = parts.INSTRUMENT_PIN_NAMES.get(key, {})
    pin_xy = {}
    for pid, p in pins.items():
        name = name_of.get(pid, pid)
        label = f"{name}（{meaning[name]}）" if name in meaning else name
        pin_xy[label] = f"({p['x'] - cx:g}, {p['y'] - cy:g}) {words.get(p['direction'], '')}"
    box = check.symbol_bbox(sym)
    body = f"x {box[0] - cx:+.0f}..{box[2] - cx:+.0f}, y {box[1] - cy:+.0f}..{box[3] - cy:+.0f}（不含名称标签）" if box else ""
    return pin_xy, body


def cmd_build(a):
    from . import builder, check, com
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    r = builder.build(spec)
    report = check.check_file(r["ms14"], r.get("expected"))
    out = {"ms14": r["ms14"], "sim_time": r["sim_time"], "warnings": r["warnings"] + report["warnings"],
           "problems": report["problems"], "placements": {p["refdes"]: p["box"] for p in r["placements"]}}
    if a.preview:
        com.call({"op": "image", "ms14": r["ms14"], "out": str(Path(a.preview).resolve())})
        out["preview"] = str(Path(a.preview).resolve())
    _out(out)
    if report["problems"]:
        sys.exit(2)


def cmd_preview(a):
    from . import com
    _out(com.call({"op": "image", "ms14": str(Path(a.ms14).resolve()), "out": str(Path(a.png).resolve())}))


def cmd_check(a):
    from . import check
    r = check.check_file(a.ms14)
    _out(r)
    if r["problems"]:
        sys.exit(2)


def cmd_inspect(a):
    from . import check
    _out(check.inspect(a.ms14))


def cmd_sim(a):
    from . import sim
    nets = [n for n in (a.nets or "").split(",") if n]
    ac = None
    if a.ac:
        v = _floats(a.ac, (2, 3))
        ac = (v[0], v[1], int(v[2]) if len(v) > 2 else 100)
    tran = None
    if a.tran:
        v = _floats(a.tran, (1, 2))
        tran = (v[0], v[1] if len(v) > 1 else None)
    if not (a.op or ac or tran):
        raise SystemExit("至少选一种分析：--op / --ac / --tran")
    _out(sim.run(a.ms14, nets, op=a.op, ac=ac, tran=tran, ac_out=a.out, ac_in=getattr(a, "in"),
                 at=_floats(a.at) if a.at else (), csv_dir=a.csv))


def cmd_spice(a):
    from . import sim
    r = sim.spice(Path(a.cir).read_text(encoding="utf-8"), a.cmd)
    vectors = r.pop("vectors", None)
    if vectors:
        r["vectors"] = {k: {"points": len(v), "first": v[0] if not isinstance(v[0], complex) else str(v[0]),
                            "last": v[-1] if not isinstance(v[-1], complex) else str(v[-1])} for k, v in vectors.items()}
    _out(r)


def cmd_capture(a):
    from . import capture
    _out(capture.capture(a.ms14, a.outdir, wait=a.wait, timeout=a.timeout, keep_open=a.keep_open))


def cmd_sim_time(a):
    from . import builder
    from .netlist import parse_value
    builder.set_sim_time(a.ms14, parse_value(a.seconds))
    _out({"ms14": a.ms14, "sim_time": parse_value(a.seconds)})


def cmd_codec(a):
    from . import codec
    data = Path(a.src).read_bytes()
    Path(a.dst).write_bytes(codec.decode(data) if a.command == "decode" else codec.encode(data))
    _out({"written": a.dst})


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="multisim-cli", description="用文字控制 NI Multisim 14：生成电路、预览、仿真取数、真实截图")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("parts", help="元件库：init 建库 / list 查找 / show 详情 / harvest 采集 / fetch 从数据库取")
    s.add_argument("action", choices=["init", "list", "show", "harvest", "fetch", "dbindex"])
    s.add_argument("arg", nargs="?")
    s.add_argument("--force", action="store_true", help="harvest 时覆盖同名元件")
    s.add_argument("--group", help="fetch：数据库分组，如 Mixed")
    s.add_argument("--family", help="fetch：元件族，如 TIMER")
    s.set_defaults(func=cmd_parts)

    s = sub.add_parser("build", help="按 spec.json 生成 .ms14 并自检")
    s.add_argument("spec")
    s.add_argument("--preview", help="同时导出预览图 PNG")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("preview", help="用 Multisim 导出电路图 PNG")
    s.add_argument("ms14")
    s.add_argument("png")
    s.set_defaults(func=cmd_preview)

    s = sub.add_parser("check", help="自检：连线是否与网络一致、元件重叠、导线交叉")
    s.add_argument("ms14")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("inspect", help="读出电路里的元件、网络和仪器设置")
    s.add_argument("ms14")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("sim", help="对 .ms14 做直流工作点 / 交流扫描 / 瞬态分析")
    s.add_argument("ms14")
    s.add_argument("--nets", help="要测的网络，逗号分隔")
    s.add_argument("--op", action="store_true", help="直流工作点")
    s.add_argument("--ac", help="交流扫描：起始,终止[,每十倍频点数]，如 10,100k,100")
    s.add_argument("--out", help="交流：输出网络")
    s.add_argument("--in", help="交流：输入网络（给出时按 out/in 求传递函数）")
    s.add_argument("--at", help="交流：要读数的频率，逗号分隔")
    s.add_argument("--tran", help="瞬态：时长[,采样率]，如 10m")
    s.add_argument("--csv", help="把数据点写成 CSV 的目录")
    s.set_defaults(func=cmd_sim)

    s = sub.add_parser("spice", help="用 Multisim 的命令行引擎运行任意 SPICE 网表")
    s.add_argument("cir")
    s.add_argument("--cmd", action="append", required=True, help='分析命令，如 "tran 1u 10m"，可写多个')
    s.set_defaults(func=cmd_spice)

    s = sub.add_parser("capture", help="打开 Multisim 运行仿真，截取电路、仪器面板和整屏")
    s.add_argument("ms14")
    s.add_argument("outdir")
    s.add_argument("--wait", type=float, default=1.5, help="画面静止多久算仿真结束（秒）")
    s.add_argument("--timeout", type=float, default=90, help="最长等待（秒）")
    s.add_argument("--keep-open", action="store_true", help="截完不关闭 Multisim")
    s.set_defaults(func=cmd_capture)

    s = sub.add_parser("set-sim-time", help="修改交互式仿真结束时间")
    s.add_argument("ms14")
    s.add_argument("seconds")
    s.set_defaults(func=cmd_sim_time)

    for name in ("decode", "encode"):
        s = sub.add_parser(name, help=".ms14 ↔ XML（调试用）")
        s.add_argument("src")
        s.add_argument("dst")
        s.set_defaults(func=cmd_codec)

    a = p.parse_args(argv)
    try:
        a.func(a)
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
        _out({"error": f"{type(exc).__name__}: {exc}"})
        sys.exit(1)


if __name__ == "__main__":
    main()
