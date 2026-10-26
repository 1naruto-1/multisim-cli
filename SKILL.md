---
name: multisim-cli
description: 用文字控制本机 NI Multisim 14 的命令行工具：按网表和坐标生成可编辑的 .ms14 电路（自动布线、元件旋转、仪器面板预设），导出预览图并自检连线，用 Multisim 自己的仿真引擎取直流工作点/交流扫描/瞬态数值，打开 Multisim 截取真实的示波器、波特图仪面板和电路截图，从 Multisim 数据库取任意元件（555、74 系列、各型号运放三极管），读出已有 .ms14 的元件和网络。只要任务涉及 Multisim、.ms14 文件、电路仿真截图、仿真数据、示波器/波特图仪画面、在 Multisim 里画电路或验证电路，都应使用本 skill（它取代了 multisim-mcp），即使用户没提工具名。
---

# multisim-cli

在本机控制 Multisim 14.0 的命令行工具，Python 包和本说明都在这个目录里。任何目录下都用下面的方式调用，结果都是 JSON：

```
python -m multisim_cli <命令> ...
```

（Python 的 Scripts 目录在 PATH 里时，也可以直接写 `multisim-cli <命令>`。）

## 硬性原则

- **截图只能来自真实的 Multisim 窗口**（`capture` 命令或用户自己截的）。截不到就说明原因、请用户手动截，绝不用画图代码伪造仪器面板或电路图。
- **数值来自真实仿真**：`sim` 用的是 Multisim 自己的网表和元件模型，结果可以直接引用；不要用估算冒充仿真值。
- `capture` 运行期间 Multisim 会弹到前台，**事先提醒用户约一分钟内不要动鼠标键盘**。它只会关闭自己启动的 Multisim，用户已经打开的同名电路会被复用、不会被关掉。
- 生成的 `.ms14` 文件名只能用英文、数字、下划线、短横线（仪器预设和探针靠文件名匹配）；所在目录可以有中文。

## 命令速查

| 命令 | 作用 |
|---|---|
| `parts list [关键词]` | 查元件库（名称、族、引脚顺序） |
| `parts show <型号>` | 引脚顺序、隐藏电源脚、0° 时各引脚相对位置和引出方向（规划布局用） |
| `parts fetch <型号>` | 库里没有时从 Multisim 数据库取（几秒钟），如 `LM555CN`、`74LS00N`、`UA741CD` |
| `parts harvest <.ms14或目录>` | 从用户自己的电路里采集元件 |
| `build spec.json [--preview x.png]` | 生成 .ms14 + 自检；`--preview` 同时导出预览图 |
| `preview x.ms14 x.png` | 用 Multisim 导出电路图（几秒） |
| `check x.ms14` | 自检：导线是否与网络一致、元件重叠、导线穿过元件、交叉数 |
| `inspect x.ms14` | 读出元件、网络、仪器面板设置（可读用户自己画的电路） |
| `sim x.ms14 --nets a,b --op / --ac 10,100k[,点数] --out b --in a --at 1k / --tran 10m[,采样率] [--csv 目录]` | Multisim 仿真取数 |
| `spice x.cir --cmd "tran 1u 10m"` | 任意 SPICE 文本交给 Multisim 命令行引擎 |
| `capture x.ms14 输出目录` | 真实截图：`<stem>_circuit.png`、`<stem>_XSC1.png` 等面板、`<stem>_screen.png` 整屏 |
| `set-sim-time x.ms14 20ms` | 改交互式仿真结束时间 |
| `decode / encode` | .ms14 ↔ XML（调试用） |

## 工作流程

1. **确认元件**：网表里用到的型号先 `parts list`/`parts show` 查；没有就 `parts fetch`。查引脚顺序和 `pin_xy`，决定写法和摆放方向。
2. **写 spec.json**（写法见 `references/spec.md`）：网表 + 每个元件的坐标/角度 + 接地符号位置 + 仪器预设 + sim_time。
3. **生成并看图**：`build spec.json --preview x.png`，打开预览图看布局。`problems` 非空说明连线错了，必须修；`warnings` 里的重叠、穿过元件、交叉多，改坐标重新生成，一般一两轮就够。
4. **取数**：`sim` 得到工作点、增益/相位/-3 dB 频率/谐振峰、瞬态峰值周期等。数值写进报告前和理论值对一下。
5. **截图**：提醒用户别动鼠标键盘，再 `capture`。逐张看图：波形是否铺满一屏（`scope_coverage` 接近 1）、档位是否合适（波形占屏幕高度 1/3 以上、不削顶）。不满意就改 instruments 或 sim_time 重新 build、重新截。
6. **交付**：说明哪些图是 Multisim 截图、哪些数值来自 `sim`；`warnings` 没处理的要告诉用户。

## 已知限制

- 波特图仪和示波器的读数光标不能预设，截图上光标停在起点；具体频率、电压读数用 `sim` 的结果写进正文或图题。
- 多单元器件（LM324、74LS00 等）每个实例只用 A 单元；数字器件的 VCC/GND 是隐藏脚，默认接到 VCC/GND 网络（Multisim 默认理想数字电源，不用另接）。
- 布线是自动的，复杂电路仍可能绕线，靠调整坐标解决；`check` 的交叉数可作为调整目标。
- `parts fetch` 需要 Node.js 和 `electronics-workbench-decoder`（读数据库索引，首次约 10 秒）；COM 替换偶尔失败时，请用户在 Multisim 里放一个该元件、保存，再 `parts harvest`。

元件库机制和本机路径见 `references/parts.md`。
