"""本机路径配置：缺省值按 Multisim 14.0 的标准安装位置给出，可用 skill 目录下的 config.json 覆盖。"""
import json
import os
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
_LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "multisim-cli"
_COM_CLSID = r"WOW6432Node\CLSID\{D9CBB7A1-6AD6-438B-AD1B-42FB2DB00CAE}\InprocServer32"   # MultisimApp


def _multisim_exe() -> str:
    """从自动化接口的注册信息找到 Multisim 安装目录；找不到时用默认安装路径。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, _COM_CLSID) as key:
            return str(Path(winreg.QueryValue(key, None)).with_name("multisim.exe"))
    except OSError:
        return r"C:\Program Files (x86)\National Instruments\Circuit Design Suite 14.0\multisim.exe"


_DEFAULTS = {
    "multisim_exe": _multisim_exe(),
    "samples_dir": r"C:\Users\Public\Documents\National Instruments\Circuit Design Suite 14.0\samples",
    "master_db": r"C:\ProgramData\National Instruments\Circuit Design Suite\14.0\database\MSCOMP_S.PRD",
    "node_decoder": str(Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "electronics-workbench-decoder"),
    "python32": str(_LOCAL / ".venv32" / "Scripts" / "python.exe"),
    "library_dir": str(_LOCAL / "parts"),
    "work_dir": r"C:\msim_work",          # DoCommandLine 的工作目录不能有空格
}


def _load() -> dict:
    path = SKILL_DIR / "config.json"
    user = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {**_DEFAULTS, **user}


CONFIG = _load()


def library_dir() -> Path:
    return Path(CONFIG["library_dir"])
