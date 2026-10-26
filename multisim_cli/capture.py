"""打开 .ms14 → 运行交互式仿真 → 截取仪器面板和电路窗口（真实的 Multisim 截图）。

用系统 Python 运行（需要 pywin32、Pillow、numpy、scipy）。仪器面板是否弹出、档位、
仿真结束时间都由 builder 写进 .ms14。截图期间 Multisim 窗口会被切到前台，不要动鼠标键盘。
输出：
    <stem>_circuit.png      电路图（裁掉空白）
    <stem>_<仪器名>.png     每个仪器面板，例如 rc_XSC1.png
    <stem>_screen.png       面板叠在电路窗口上的整屏截图
"""
import ctypes
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image, ImageGrab
from scipy import ndimage

from . import xmlio
from .config import CONFIG

ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32 = ctypes.windll.user32
PANEL_HINTS = ("XSC", "XBP", "XFG", "XMM", "示波器", "波特", "函数", "万用表", "Oscilloscope", "Bode", "Function", "Multimeter")


def top_windows(pid=None):
    found = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        _, owner = win32process.GetWindowThreadProcessId(hwnd)
        if pid is None or owner == pid:
            found.append((hwnd, win32gui.GetWindowText(hwnd), win32gui.GetClassName(hwnd), owner))
    win32gui.EnumWindows(cb, None)
    return found


def find_main(stem: str):
    for hwnd, title, cls, pid in top_windows():
        if cls.startswith("Multisim") and f"[{stem}]" in title:
            return hwnd, pid
    return None, None


def grab(hwnd: int) -> Image.Image:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)     # PW_RENDERFULLCONTENT：被遮挡也能完整渲染
    info = bmp.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    return img.copy()


def bring_to_front(hwnd: int) -> bool:
    """绕过前台锁：挂接到当前前台线程后再切换。"""
    fg = win32gui.GetForegroundWindow()
    me = win32api.GetCurrentThreadId()
    other = win32process.GetWindowThreadProcessId(fg)[0] if fg else me
    try:
        user32.AttachThreadInput(me, other, True)
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass
    finally:
        user32.AttachThreadInput(me, other, False)
    time.sleep(0.4)
    return win32gui.GetForegroundWindow() == hwnd


def send_key(hwnd: int, vk: int):
    """直接投递按键消息，不依赖键盘焦点。"""
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
    time.sleep(0.05)
    win32api.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0xC0000000)


def panels(pid: int, main: int):
    return [(h, t) for h, t, c, _ in top_windows(pid)
            if h != main and c == "#32770" and any(k in t for k in PANEL_HINTS)]


def _longest_run(mask, gap=6):
    """最长连续 True 段 [起, 止)，允许不超过 gap 的短缺口（网格线、坐标轴）。"""
    mask = list(mask)
    for i in range(len(mask)):
        if not mask[i] and any(mask[max(0, i - gap):i]) and any(mask[i + 1:i + 1 + gap]):
            mask[i] = True
    best, start = (0, 0), None
    for i, v in enumerate(mask + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    return best


def scope_coverage(img: Image.Image) -> float:
    """示波器黑色显示区里有彩色波形的列所占比例（0~1）。"""
    a = np.asarray(img).astype(int)
    dark = a.sum(axis=2) < 60
    r0, r1 = _longest_run(dark.mean(axis=1) > 0.6)
    if r1 - r0 < 10:
        return 0.0
    c0, c1 = _longest_run(dark[r0:r1].mean(axis=0) > 0.6)
    if c1 - c0 < 10:
        return 0.0
    region = a[r0:r1, c0:c1]
    colored = ((region.max(axis=2) - region.min(axis=2)) > 80) & (region.max(axis=2) > 120)
    hit = colored.any(axis=0)
    lo, hi = int(len(hit) * 0.03), int(len(hit) * 0.97)
    return float(hit[lo:hi].mean())


def _view_rect(main: int):
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == "***EWbCDSTabWnd***":
            found.append(win32gui.GetWindowRect(hwnd))
    win32gui.EnumChildWindows(main, cb, None)
    return found[0] if found else None


def drawing_area(main: int, img: Image.Image) -> Image.Image:
    """裁出图纸视图（不含菜单、工具栏、设计工具箱和底部表格视图）。"""
    left, top, *_ = win32gui.GetWindowRect(main)
    r = _view_rect(main)
    return img.crop((r[0] - left, r[1] - top, r[2] - left, r[3] - top)) if r else img


def ink_mask(img: Image.Image):
    """白色页面范围内的笔迹掩码（黑色线条文字或高饱和彩色），返回 (掩码, 页面左上角偏移)。"""
    a = np.asarray(img).astype(int)
    lo, hi = a.min(axis=2), a.max(axis=2)
    white = lo > 235
    c0, c1 = _longest_run(white.mean(axis=0) > 0.6, gap=3)
    r0, r1 = _longest_run(white[:, c0:c1].mean(axis=1) > 0.6, gap=3)
    if r1 - r0 < 10 or c1 - c0 < 10:
        return np.zeros(lo.shape, bool), (0, 0)
    ink = (hi[r0:r1, c0:c1] < 60) | ((hi - lo)[r0:r1, c0:c1] > 120)
    # 去掉网格点和图框刻线：只保留像素足够多的连通块
    labels, count = ndimage.label(ink, structure=np.ones((3, 3)))
    if count:
        sizes = ndimage.sum(ink, labels, index=np.arange(1, count + 1))
        keep = np.zeros(count + 1, bool)
        keep[1:] = sizes >= 30
        ink = keep[labels]
    return ink, (c0, r0)


def frame_inner(img: Image.Image):
    """图纸内框（分区编号条以内）：靠近页边、贯穿整页的灰色直线。"""
    a = np.asarray(img).astype(int)
    lo, hi = a.min(axis=2), a.max(axis=2)
    gray = (lo > 60) & (hi < 210) & ((hi - lo) < 25)
    h, w = gray.shape
    rows = np.where(gray.mean(axis=1) > 0.5)[0]
    cols = np.where(gray.mean(axis=0) > 0.5)[0]
    return (max([c for c in cols if c < w * 0.3], default=0), max([r for r in rows if r < h * 0.3], default=0),
            min([c for c in cols if c > w * 0.7], default=w), min([r for r in rows if r > h * 0.7], default=h))


def crop_ink(img: Image.Image, margin=40) -> Image.Image:
    ink, (ox, oy) = ink_mask(img)
    rows = np.where(ink.sum(axis=1) > 2)[0]
    cols = np.where(ink.sum(axis=0) > 2)[0]
    if len(rows) == 0 or len(cols) == 0:
        return img
    x0, y0, x1, y1 = ox + cols.min(), oy + rows.min(), ox + cols.max(), oy + rows.max()
    fl, ft, fr, fb = frame_inner(img)
    # 留白不越过图框内边；元件压到图框上时按笔迹裁，不切掉元件
    left = max(x0 - margin, fl + 2) if x0 > fl else max(x0 - margin, 0)
    top = max(y0 - margin, ft + 2) if y0 > ft else max(y0 - margin, 0)
    right = min(x1 + margin, fr - 1) if x1 < fr else min(x1 + margin, img.width)
    bottom = min(y1 + margin, fb - 1) if y1 < fb else min(y1 + margin, img.height)
    return img.crop((left, top, right, bottom))


def arrange_panels(main: int, pid: int, view: Image.Image):
    """把面板挪到图纸视图里电路笔迹最少的角落，尽量不挡电路。"""
    rect = _view_rect(main)
    if rect is None:
        return
    vx0, vy0, vx1, vy1 = rect
    ink, (ox, oy) = ink_mask(view)
    occupied = np.zeros((vy1 - vy0, vx1 - vx0), bool)
    occupied[oy:oy + ink.shape[0], ox:ox + ink.shape[1]] = ink[:occupied.shape[0] - oy, :occupied.shape[1] - ox]
    for hwnd, _ in panels(pid, main):
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w, h, pad = r - l, b - t, 20
        corners = [(vx1 - w - pad, vy0 + pad), (vx1 - w - pad, vy1 - h - pad), (vx0 + pad, vy1 - h - pad), (vx0 + pad, vy0 + pad)]

        def cost(pos):
            x, y = pos[0] - vx0, pos[1] - vy0
            return occupied[max(y, 0):y + h, max(x, 0):x + w].sum()
        x, y = min(corners, key=cost)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, x, y, 0, 0, win32con.SWP_NOSIZE)
        occupied[max(y - vy0, 0):y - vy0 + h, max(x - vx0, 0):x - vx0 + w] = True


def open_design(ms14: Path):
    """返回 (主窗口, 进程号, 是否由本次启动)。"""
    main, pid = find_main(ms14.stem)
    launched = main is None
    if launched:
        subprocess.Popen([CONFIG["multisim_exe"], str(ms14)])
        deadline = time.time() + 120
        while main is None and time.time() < deadline:
            time.sleep(1)
            main, pid = find_main(ms14.stem)
        if main is None:
            raise RuntimeError("等待 Multisim 窗口超时")
        time.sleep(3)
    win32gui.ShowWindow(main, win32con.SW_MAXIMIZE)
    time.sleep(1)
    send_key(main, win32con.VK_F7)                    # 缩放到整张图纸
    time.sleep(1)
    return main, pid, launched


def run_once(ms14: Path, out: Path, wait: float, timeout: float):
    stem = ms14.stem
    main, pid, launched = open_design(ms14)
    result = {"ms14": str(ms14), "images": {}, "warnings": []}
    send_key(main, win32con.VK_F5)
    deadline = time.time() + 15
    while not panels(pid, main) and time.time() < deadline:
        time.sleep(0.5)
    if not panels(pid, main):
        result["warnings"].append("运行后没有仪器面板弹出：检查 instruments 的 show 设置")
    # 仿真到 sim_time 自动停止：等各面板画面不再变化再截图
    previous, stable_since = None, None
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.7)
        shots = [np.asarray(grab(h)) for h, _ in panels(pid, main)]
        same = previous is not None and len(shots) == len(previous) and all(
            a.shape == b.shape and (a == b).all() for a, b in zip(shots, previous))
        previous = shots
        if not same:
            stable_since = None
        elif stable_since is None:
            stable_since = time.time()
        elif time.time() - stable_since >= wait:
            break
    else:
        send_key(main, win32con.VK_F6)
        time.sleep(1.0)
        result["warnings"].append("仿真在超时前没有停止，已手动暂停：检查 sim_time")
    worst = 1.0
    for hwnd, title in panels(pid, main):
        name = title.split("-")[-1].strip() or "panel"
        img = grab(hwnd)
        path = out / f"{stem}_{name}.png"
        img.save(path)
        result["images"][name] = str(path)
        if "XSC" in title or "示波器" in title:
            coverage = scope_coverage(img)
            result.setdefault("scope_coverage", {})[name] = round(coverage, 2)
            worst = min(worst, coverage)
    view = drawing_area(main, grab(main))
    crop_ink(view).save(out / f"{stem}_circuit.png")
    result["images"]["circuit"] = str(out / f"{stem}_circuit.png")
    # 整屏截图：Multisim 切到前台后抓屏，面板按真实位置叠在电路窗口上
    if bring_to_front(main):
        arrange_panels(main, pid, view)
        time.sleep(0.8)
        l, t, r, b = win32gui.GetWindowRect(main)
        ImageGrab.grab(bbox=(max(l, 0), max(t, 0), r, b), all_screens=True).save(out / f"{stem}_screen.png")
        result["images"]["screen"] = str(out / f"{stem}_screen.png")
    else:
        result["warnings"].append("无法把 Multisim 切到前台（截图时请勿操作鼠标键盘），没有生成整屏截图")
    return result, pid, launched, worst


def sweep_info(ms14: Path):
    """从文件里读交互式仿真结束时间和示波器一屏时长。"""
    root = xmlio.load(ms14)
    state = next(root.iter("CIITDiagram")).get("SimState") or ""
    _, _, tail = state.partition("DEFAULT_INST_SETTINGS")
    m = re.search(r"TSTOP:double\{([^}]*)\}", tail)
    sim_time = float(m.group(1)) if m else None
    sweep = None
    for node in root.iter("CSourceSymbolCollectNode"):
        if (node.get("CompLongName") or "").upper().startswith("&ASCXSC"):
            for el in node.iter("Element"):
                if el.get("Key") == "&ASCNI_EWB_TIMEBASE_SCALE_VALUE":
                    sweep = 10 * float(el.find("CDataElement").get("Data"))
            break
    return sim_time, sweep


def capture(ms14, outdir, wait=1.5, timeout=90, keep_open=False) -> dict:
    from .builder import set_sim_time
    ms14, out = Path(ms14).resolve(), Path(outdir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sim_time, sweep = sweep_info(ms14)
    result, pid, launched, worst = run_once(ms14, out, wait, timeout)
    if worst < 0.9 and launched and sim_time and sweep and sim_time < 1e20:
        # 同一电路的扫描起点固定：把结束时间推到这一屏快扫完的位置，再跑一次
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(2)
        new_time = sim_time + (0.99 - worst) * sweep
        set_sim_time(ms14, new_time)
        first = result
        result, pid, launched, worst = run_once(ms14, out, wait, timeout)
        result["warnings"] = first["warnings"] + result["warnings"]
        result["sim_time_adjusted"] = new_time
    if worst < 0.9:
        result["warnings"].append(f"示波器波形只占屏幕 {worst:.0%}；可增大 sim_time 后重新生成、重新截图")
    if launched and not keep_open:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    return result
