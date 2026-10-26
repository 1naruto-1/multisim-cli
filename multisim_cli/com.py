"""Multisim 官方自动化接口（COM）。

Multisim 14 的 COM 服务只能被 32 位进程调用，所以每个任务都起一个 32 位 Python 子进程执行：
连接（自动启动一个隐藏的 Multisim 实例，约 3 秒）→ 干活 → 断开（实例随之退出）。
个别接口会让进程直接崩溃（ReportNetlist、不兼容的 ReplaceComponent），崩溃时清理残留实例，
不影响下一次调用。用法：com.call({"op": "image", "ms14": ..., "out": ...})。
"""
import json
import os
import subprocess
import sys
import time

PROG_ID = "MultisimInterface.MultisimApp"


class ComError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 调用端（任意 Python）
# ---------------------------------------------------------------------------
def _multisim_pids() -> set:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq multisim.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="ignore").stdout
    return {line.split('","')[1] for line in out.splitlines() if line.startswith('"multisim.exe"')}


def call(job: dict, timeout: float = 300) -> dict:
    from .config import CONFIG, SKILL_DIR
    before = _multisim_pids()
    try:
        proc = subprocess.run([CONFIG["python32"], "-X", "utf8", "-m", "multisim_cli.com"],
                              input=json.dumps(job), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=str(SKILL_DIR))
    except subprocess.TimeoutExpired:
        proc = None
    lines = proc.stdout.strip().splitlines() if proc else []
    result = None
    if lines:
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError:
            pass
    if result is None or result.get("error"):
        # 进程异常退出时它启动的 Multisim 不会自己关
        for pid in _multisim_pids() - before:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
        if result is None:
            detail = "超时" if proc is None else f"进程退出码 {proc.returncode}，{(proc.stderr or '').strip()[-400:]}"
            raise ComError(f"Multisim COM 任务 {job.get('op')} 失败（{detail}）")
        raise ComError(result["error"])
    return result


# ---------------------------------------------------------------------------
# 执行端（32 位 Python + pywin32）
# ---------------------------------------------------------------------------
def _repair_gen_py():
    """pywin32 的 gen_py 缓存写到一半时会让后续调用全部失败；它只是缓存，坏了就删。"""
    import shutil
    from win32com import client
    root = client.gencache.GetGeneratePath()
    if os.path.isdir(root) and any(name.endswith(".temp") for _, _, files in os.walk(root) for name in files):
        shutil.rmtree(root, ignore_errors=True)


def _connect():
    import pythoncom
    from win32com import client
    pythoncom.CoInitialize()
    _repair_gen_py()
    try:
        app = client.gencache.EnsureDispatch(PROG_ID)
    except Exception:
        app = client.dynamic.Dispatch(PROG_ID)
    if not app.IsConnected:
        app.Connect()
    return app


def _open(app, path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):          # 路径不存在时 OpenFile 会直接崩溃
        raise FileNotFoundError(path)
    return app.OpenFile(path)


def _bstrs(values):
    import pythoncom
    from win32com import client
    return client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_BSTR, list(values))


def _clean(text):
    # Multisim 把 UTF-8 字节当成 BSTR 码位返回
    try:
        return str(text).encode("latin-1").decode("utf-8", errors="replace")
    except Exception:
        return str(text)


def _collect(circuit, names, timeout):
    import pythoncom
    deadline = time.time() + timeout
    pending = list(names)
    while pending and time.time() < deadline:
        pending = [n for n in pending if not circuit.OutputReady(n)]
        if pending:
            time.sleep(0.05)
    data = {}
    for n in names:
        if n not in pending:
            rows, _ = circuit.GetOutputData(n, pythoncom.Missing, pythoncom.Missing)
            data[n] = [list(r) for r in rows]
    return data, pending


def _analyze(app, job):
    c = _open(app, job["ms14"])
    outputs = list(c.EnumOutputs(0) or ())
    results = []
    for a in job.get("analyses", []):
        wanted = a.get("outputs") or outputs
        names = [n for n in wanted if n in outputs]      # 不存在的输出名会让分析直接报错
        if not names:
            raise RuntimeError(f"电路里没有可读的输出（探针没被 Multisim 识别），现有输出 {outputs}")
        if a["type"] == "op":
            c.DoDCOperatingPoint(_bstrs(names))
        elif a["type"] == "ac":
            sweep = {"dec": 0, "oct": 1, "lin": 2}[a.get("sweep", "dec")]
            c.DoACSweep(sweep, int(a.get("points", 100)), float(a["start"]), float(a["stop"]), _bstrs(names))
        elif a["type"] == "tran":
            if c.SimulationState:
                c.StopSimulation()
            rate = float(a.get("rate") or 2000 / float(a["time"]))
            for n in names:
                try:
                    c.ClearOutputRequest(n)
                except Exception:
                    pass
                c.SetOutputRequest(n, 2, rate, int(float(a["time"]) * rate) + 1, False)
            c.RunSimulation(float(a["time"]), False)
        data, pending = _collect(c, names, float(a.get("timeout", 120)))
        if a["type"] == "tran" and c.SimulationState:
            c.StopSimulation()
        pending += [n for n in wanted if n not in outputs]
        results.append({"type": a["type"], "data": data, "missing": pending,
                        "last_error": _clean(c.LastErrorMessage) if pending else ""})
    return {"outputs": outputs, "results": results}


def _spice(app, job):
    work = job["work"]
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "circuit.cir"), "w", encoding="utf-8") as f:
        f.write(job["netlist"])
    raw = os.path.join(work, "result.raw")
    if os.path.exists(raw):
        os.remove(raw)
    with open(os.path.join(work, "run.txt"), "w", encoding="utf-8") as f:      # 路径必须写绝对路径
        f.write(f"source {os.path.join(work, 'circuit.cir')}\n" + "\n".join(job["commands"]) + f"\nwrite {raw}\n")
    c = app.NewFile()
    log = os.path.join(work, "run.log")
    c.DoCommandLine(os.path.join(work, "run.txt"), log)
    deadline = time.time() + float(job.get("timeout", 120))
    while c.SimulationState and time.time() < deadline:
        time.sleep(0.2)
    text = open(log, encoding="utf-8", errors="replace").read() if os.path.exists(log) else ""
    from .xmlio import decode_uc
    text = decode_uc(text)
    return {"raw": raw if os.path.exists(raw) else None, "log": text[-4000:], "last_error": _clean(c.LastErrorMessage)}


def _replace(app, job):
    c = _open(app, job["ms14"])
    names = list(c.EnumComponents(0) or ())
    if job["ref"] not in names:                # 不存在的位号也会让进程崩溃
        raise RuntimeError(f"载体电路里没有 {job['ref']}，现有 {names}")
    new_ref = c.ReplaceComponent(job["ref"], "", 0, job["group"], job["family"], job["name"], job.get("model", ""))
    c.SaveAs(os.path.abspath(job["out"]))
    return {"ref": str(new_ref), "components": list(c.EnumComponents(0) or ()), "last_error": _clean(c.LastErrorMessage)}


def _run(job):
    app = _connect()
    try:
        op = job["op"]
        if op == "blank":
            c = app.NewFile()
            c.SaveAs(os.path.abspath(job["out"]))
            return {"out": job["out"]}
        if op == "image":
            c = _open(app, job["ms14"])
            c.GetCircuitImage(0, os.path.abspath(job["out"]))
            return {"out": job["out"], "last_error": _clean(c.LastErrorMessage)}
        if op == "components":
            c = _open(app, job["ms14"])
            return {"components": list(c.EnumComponents(0) or ()), "outputs": list(c.EnumOutputs(0) or ())}
        if op == "analyze":
            return _analyze(app, job)
        if op == "spice":
            return _spice(app, job)
        if op == "replace":
            return _replace(app, job)
        raise ValueError(f"未知任务 {op}")
    finally:
        try:
            app.Disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        result = _run(json.loads(sys.stdin.read()))
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {_clean(exc)}"}
    print(json.dumps(result, ensure_ascii=False))
