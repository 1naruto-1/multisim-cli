# multisim-cli

用文字控制 NI Multisim 14 的命令行工具：按网表和坐标生成可编辑的 `.ms14` 电路，导出预览并自检连线，用 Multisim 自己的仿真引擎取数，打开 Multisim 截取真实的示波器、波特图仪面板。也可以作为 Claude Code 的 skill 使用。

## 功能

- **生成电路**：SPICE 风格网表 + 元件坐标/角度 → `.ms14`。自动布线（树形连接、避开元件和标签）、多个接地符号、示波器/波特图仪/函数发生器面板预设、交互式仿真结束时间。
- **任意元件**：元件模板从 `.ms14` 中整组采集；库里没有的型号用 `parts fetch` 通过 Multisim 自动化接口从数据库取（如 LM555CN、74LS00N）。仿真用元件自带的真实模型。
- **自检**：不依赖 Multisim，按导线和结点的图形连接还原网络，与电气网络逐一对比；报告元件重叠、导线穿过元件、交叉数。
- **仿真取数**：在网络上放电压探针，通过 COM 做直流工作点、交流扫描、瞬态分析，输出谐振峰、-3 dB 频率、相位、峰峰值、周期等；也能把任意 SPICE 文本交给 Multisim 命令行引擎。
- **真实截图**：打开 Multisim、运行仿真、等波形稳定后截取电路图、仪器面板和整屏；示波器扫描未铺满时自动校准仿真时间重跑。
- **读已有电路**：`inspect` 读出元件、网络和仪器面板设置。
- `.ms14` 编解码纯 Python 实现：解出的 XML 与官方解码器一致，XML 读写对 133 个官方示例逐字节还原。

## 环境

- Windows，NI Multisim 14.0（已授权，含官方示例）
- Python 3.10+（任意位数）；截图需要 `pywin32`、`Pillow`、`numpy`、`scipy`
- 32 位 Python 3.12 + `pywin32`：Multisim 14 的自动化接口是 32 位进程内组件，COM 调用在这个解释器里执行
- 可选：Node.js + `electronics-workbench-decoder`（`parts fetch` 读数据库索引）

## 安装

```powershell
git clone https://github.com/1naruto-1/multisim-cli
cd multisim-cli
pip install -e .[capture]

# 32 位 Python（用 uv 最省事）
uv python install cpython-3.12-windows-x86-none
uv venv --python cpython-3.12-windows-x86-none "$env:LOCALAPPDATA\multisim-cli\.venv32"
uv pip install --python "$env:LOCALAPPDATA\multisim-cli\.venv32\Scripts\python.exe" pywin32

npm install -g electronics-workbench-decoder   # 可选，parts fetch 用

multisim-cli parts init     # 用本机 Multisim 建元件库（约半分钟）
```

路径默认按 Multisim 的标准安装位置自动查找，不同时在仓库目录下放一个 `config.json` 覆盖（键见 `references/parts.md`）。

作为 Claude Code skill：把仓库克隆或链接到 `~/.claude/skills/multisim-cli`，`SKILL.md` 即为说明。

## 快速开始

`rc.json`：

```json
{
  "netlist": "V1 vin 0 DC 0 AC 1 SIN(0 1 1k)\nR1 vin vout 1k\nC1 vout 0 0.1u\nXSC1 vin vout 0 nc1 0 nc2 OSCILLOSCOPE\n",
  "output": "D:/work/rc.ms14",
  "layout": {"V1": [100, 260], "R1": [250, 180], "C1": [400, 260, 90], "XSC1": [560, 140],
             "GND": [[100, 350], [400, 350], [610, 230]]},
  "instruments": {"XSC1": {"timebase": "500us", "a_scale": "1V", "b_scale": "1V", "trigger": "none"}}
}
```

```powershell
multisim-cli build rc.json --preview rc.png                      # 生成 + 自检 + 预览图
multisim-cli sim D:/work/rc.ms14 --ac 10,100k --out vout --in vin  # 截止频率、相位
multisim-cli capture D:/work/rc.ms14 shots                        # 真实截图（期间不要动鼠标键盘）
```

网表、布局和仪器预设的完整写法见 `references/spec.md`，元件库机制见 `references/parts.md`。

## 许可

MIT，见 `LICENSE`。部分代码移植自 multisim-mcp（MIT），见 `THIRD_PARTY_NOTICES.md`。元件模板库在本机生成，不随仓库分发。
