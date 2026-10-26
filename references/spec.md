# spec.json 写法

```json
{
  "netlist": "* RC 方波响应\nV1 vin 0 PULSE(0 2 0 1u 1u 1m 2m)\nR1 vin vout 1k\nC1 vout 0 0.1u\nXSC1 vin vout 0 nc1 0 nc2 OSCILLOSCOPE\n",
  "output": "D:/work/multisim/rc_step.ms14",
  "layout": {"V1": [100, 260], "R1": [250, 180], "C1": [400, 260, 90],
             "XSC1": [560, 140], "GND": [[100, 350], [400, 350], [610, 230]]},
  "instruments": {"XSC1": {"timebase": "500us", "a_scale": "1V", "b_scale": "1V", "trigger": "none"}},
  "sim_time": "100ms"
}
```

| 字段 | 说明 |
|---|---|
| netlist | 网表，写法见下 |
| output | 输出 .ms14；文件名只能英文、数字、下划线、短横线 |
| layout | `{"位号": [x, y] 或 [x, y, 角度], "GND": [[x, y], ...]}` |
| instruments | 仪器面板预设，见下 |
| sim_time | 交互式仿真结束时间；有示波器时默认 20 屏 |
| net_names | 是否显示网络名，默认 false |
| colors | 导线颜色 `{"vout": "blue"}`（red/blue/green/magenta/orange/black）；示波器 B 通道默认蓝色，波形颜色跟随导线 |
| sheet | 图纸尺寸 `[宽, 高]`（英寸）；默认按电路范围收紧，F7 缩放时电路能占满窗口 |

## 网表

| 器件 | 写法 | 引脚顺序 / 备注 |
|---|---|---|
| 电阻/电容/电感 | `R1 a b 1k`、`C1 a b 0.1u`、`L1 a b 10m` | 数值可带 k/m/u/n/p/meg；末尾可再写库元件名换型号（如电解电容 `CAP_ELECTROLIT_VIRTUAL`） |
| 直流电压源 | `V1 vcc 0 DC 12` | 第一个节点是正极 |
| 正弦源 | `V1 vin 0 DC 0 AC 1 SIN(0 <峰值> <频率>)` | 要做交流分析/波特图时必须带 `AC 1` |
| 脉冲/方波源 | `V1 vin 0 PULSE(<低> <高> <延时> <上升> <下降> <脉宽> <周期>)` | 同样可加 `AC 1` |
| 直流电流源 | `I1 a b DC 1m` | |
| 二极管 | `D1 a k 1N4148` | 阳极、阴极 |
| 三极管 | `Q1 c b e 2N3904` | 集电极、基极、发射极 |
| MOS 管 | `M1 d g s b <型号>` | |
| 库元件（位置写法） | `XU1 inp inn vp vn out LM324AJ` | 按 `parts show` 的 pins 顺序；X 开头会去掉（显示为 U1A） |
| 库元件（引脚名写法） | `U1 LM555CN GND=0 TRI=thr OUT=out RST=vcc CON=con THR=thr DIS=dis VCC=vcc` | 引脚多时用这种；隐藏电源脚也可以这样改接 |
| 示波器 | `XSC1 <A+> <B+> <A-> <T+> <B-> <T-> OSCILLOSCOPE` | A-、B- 接 0；外触发不用时写只出现一次的节点名（nc1、nc2） |
| 函数发生器 | `XFG1 <+> <COM> <-> FGEN WAVE=SQUARE FREQ=1k AMPLITUDE=2 OFFSET=0 DUTY=50` | WAVE: SINE/TRIANGLE/SQUARE；AMPLITUDE 是峰值；COM 接地 |
| 波特图仪 | `XBP1 <IN+> <IN-> <OUT+> <OUT-> BODE` | IN-、OUT- 接 0；电路里要有带 AC 的信号源 |
| 万用表 | `XMM1 <+> <-> MULTIMETER` | |

注释行用 `*` 开头，`+` 开头的行接到上一行，`.end` 等点命令忽略。网络名不区分大小写，`0`、`gnd` 都是地。电路接了地就必须在 layout 里给 `"GND"` 位置。

## 布局

坐标单位约 1/96 英寸，Multisim 网格是 9。写的是**元件引脚的中心**（所有引脚外接矩形的中心），生成时自动对齐网格。

- **角度按屏幕顺时针**。两端元件（电阻、电容、电感、二极管）0° 横放、第一个节点在**左**；90° 竖放、第一个节点在**上**；180° 在右；270° 在下。并联到地的元件一律写 90（上端接信号、下端接地）。
- 电压源、电流源本身是竖的，0° 时正极（第一个节点）在上，一般不用转。
- 多引脚器件先 `parts show` 看 `pin_xy`：例如 LM555CN 的 VCC 在上 81、GND 在下 81、OUT 在右、其余在左；运放的同相/反相输入在左、输出在右。
- 信号从左到右；相邻元件引脚中心间距 ≥ 120，留出布线通道。离图纸左、上边 ≥ 100。
- 示波器、波特图仪的引脚在符号底部，本体向上伸出约 60（再往上是名称）：中心 y ≥ 120，放在电路右上方，A/B 引线尽量短。各元件本体范围见 `parts show` 的 `body`。
- 每个接地引脚正下方约 90 放一个接地符号，地线就不用绕来绕去；多个接地符号自动各自就近连接。
- 想让导线笔直，就让相连的引脚 x 或 y 对齐（例如串联元件放在同一 y，竖放的并联元件与上方节点同一 x）。
- 生成后看 `--preview` 的图和 `warnings`：导线穿过元件、元件重叠、交叉多就挪坐标。`problems`（连线与网络不一致）必须为空。

## 仪器面板预设 instruments

| 示波器 XSC | 含义 | 例 |
|---|---|---|
| timebase | 时基 s/div | `"500us"` |
| a_scale / b_scale | 刻度 V/div | `"1V"`、`"10mV"` |
| a_offset / b_offset / x_offset | Y/X 位移（格） | `-2` |
| a_coupling / b_coupling | `dc` / `ac` / `gnd` | `"ac"` |
| trigger | `none`（无）/ `auto` / `normal` / `single` | 截图用 `none` 最稳 |
| trigger_source / trigger_edge / trigger_level | `a`/`b`/`ext`，`rising`/`falling`，电平 V | |
| show | 运行时是否弹出面板，默认 true（函数发生器默认 false） | |

| 波特图仪 XBP | 含义 | 例 |
|---|---|---|
| mode | `magnitude`（幅频）/ `phase`（相频） | |
| horizontal / vertical | `log` / `lin` | |
| f_start / f_stop | 频率范围 | `"10Hz"`、`"100kHz"` |
| db_min / db_max | 垂直范围（对数刻度，dB） | `-40`、`5` |
| lin_min / lin_max | 垂直范围（线性刻度，倍数） | `0`、`1` |
| phase_min / phase_max | 相位范围（度） | `-90`、`90` |

幅频和相频要分别截图时，生成两个文件（`xx_mag.ms14`、`xx_phase.ms14`，mode 不同），各截一次。

**sim_time**：交互式仿真在这个时刻自动停止，示波器停在最后一屏。取一屏时长（10 × 时基）的整数倍，并且长于电路进入稳态的时间（≥ 5τ，或耦合电容充电完成）。扫描起点和整屏边界没对齐时，`capture` 会自动微调再跑一次。

## sim 的输出

- `--op`：各网络对地直流电压。
- `--ac 起始,终止,每十倍频点数 --out <网络> [--in <网络>] [--at 频率,...]`：`peak` 最大增益处（谐振频率），`cutoff` 增益降到峰值 0.707 倍的频率及相位（低通/高通各一个，带通两个），`at` 指定频率处的增益、dB、相位。
- `--tran 时长[,采样率]`：每个网络的最大/最小/峰峰值/平均/有效值/周期/频率；`--csv` 写出全部数据点。
- 输入网络交流幅值为 0 时会提示：信号源要写 `AC 1`。
