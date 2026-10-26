# 元件库与本机环境

## 元件库是什么

Multisim 的 .ms14 里，一个元件由好几个对象组成：电气元件（CiComponent，含 SPICE 模板和参数）、各端口（CiPort）、它引用的模型（CiModel）、原理图符号（CIITSymbolComp），虚拟仪器还有面板状态，多单元器件还有 RefDes 记录。`parts` 把这些对象从现成的 .ms14 里整组取出来存成模板；生成电路时整组复制、换新 ID、改位号和数值、接到网络上。所以**任何在某个 .ms14 里出现过的元件都能用**，仿真时用的也是元件自带的真实模型（LM324AJ 会饱和，555 用真实宏模型）。

库在 `%LOCALAPPDATA%\multisim-cli\parts\`：
- `index.json`：名称 → 族、引脚顺序、隐藏电源脚、SPICE 模板、来源
- `<名称>.xml`：元件模板
- `_struct\`：空白电路壳（本机 Multisim 新建）、导线、外部引脚、结点、网络、网络名、电压探针、仿真设置模板
- `db_index.json`：Multisim 主元件库的 {元件名: [组, 族]}（`parts fetch` 用）

## 补充元件

1. **`parts fetch <型号>`（首选）**：查 `db_index.json` 得到组和族，在载体电路里用 COM 的 ReplaceComponent 换成目标元件，另存后采集。载体必须与目标同类，否则 Multisim 进程会崩溃——所以依次试三极管载体（模拟器件：运放、555、二极管、三极管…）和 7404N 载体（TTL/CMOS 数字器件）。崩溃会被自动清理，不影响后续调用。
2. **`parts harvest <文件或目录>`**：用户在 Multisim 里放好元件、保存，再采集。同名元件默认跳过，`--force` 覆盖（只对单个文件生效）。
3. **`parts init`**：重建整个库（结构模板 + NI 官方 133 个示例里的约 230 种元件），需要 Multisim 和 COM。

型号名用数据库里的完整名称，如 `LM555CN`、`74LS00N`、`UA741CD`、`2N2222A`。不知道全名时 `parts fetch` 的报错会列出相近的名称。

## 引脚

- `pins` 是网表位置写法的顺序：取自 SPICE 模板里 `%t<引脚名>` 出现的顺序，与 SPICE 习惯一致（二极管 A K，三极管 C B E，LM324AJ 为 1IN+ 1IN- VS+ VS- 1OUT）。
- 仪器端口是数字，含义见 `parts show` 的标注（示波器 1=A+、4=A-、2=B+、5=B-、3/6=外触发）。
- `hidden_pins` 是符号上不画出来的电源脚（74 系列的 VCC/GND），默认接到同名网络；需要时用引脚名写法改接，例如 `VCC=vcc5`。
- 多单元器件（LM324 有 4 个运放、74LS00 有 4 个与非门）每个实例只用 A 单元，位号显示为 U1A、U2A…

## 本机路径（config.py 的缺省值，可用 skill 目录下的 config.json 覆盖）

| 键 | 默认值 |
|---|---|
| multisim_exe | 从 Multisim 自动化接口的注册信息自动找到；找不到时为 `C:\Program Files (x86)\National Instruments\Circuit Design Suite 14.0\multisim.exe` |
| samples_dir | `C:\Users\Public\Documents\National Instruments\Circuit Design Suite 14.0\samples` |
| master_db | `C:\ProgramData\National Instruments\Circuit Design Suite\14.0\database\MSCOMP_S.PRD` |
| python32 | `%LOCALAPPDATA%\multisim-cli\.venv32\Scripts\python.exe`（只装了 pywin32，专门执行 COM） |
| library_dir | `%LOCALAPPDATA%\multisim-cli\parts` |
| work_dir | `C:\msim_work`（Multisim 命令行引擎不接受带空格的路径） |

## 内部机制（排查问题时看）

- **.ms14 编解码**：`codec.py` 纯 Python 实现（PKWare DCL implode），解出的 XML 与官方解码器一致；XML 读写对 133 个官方示例逐字节还原。不再依赖 Node。
- **COM**：Multisim 14 的自动化接口只能被 32 位进程调用；每个任务起一个 32 位子进程：连接（自动启动隐藏的 Multisim，约 3 秒）→ 执行 → 断开（实例退出）。`ReportNetlist` 和不兼容的 `ReplaceComponent` 会让进程崩溃，工具不调用前者，后者失败后自动清理残留实例。
- **仿真取数**：在要测的网络上放电压探针，另存到 `work_dir\probe\`（文件名必须与原文件相同），再用 COM 做 DoDCOperatingPoint / DoACSweep / RunSimulation。探针要放在元件引脚处的导线端点上，放在结点或仪器端子上会被 Multisim 丢掉。
- **自检**：`check` 不依赖 Multisim，按导线和结点的图形连接把引脚分组，与电气网络逐一对比。
- **截图**：`capture` 用 PrintWindow 截面板和电路窗口，F5 运行、F7 缩放、F6 暂停都是直接投递按键消息，不需要键盘焦点；只有整屏截图需要把窗口切到前台。
