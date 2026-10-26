# 第三方声明

## multisim-mcp（MIT）

本项目的以下部分移植或改写自 [multisim-mcp](https://github.com/yxy050208/multisim-mcp)：

- `router.py` 中的引脚逃逸与直角 A* 布线（原 `orthogonal_routing.py`）
- `xmlio.py` 中保留属性换行的 XML 读写做法（原 `native_xml.py`）
- `builder.py` 中的符号引脚定位、RefDes 记录格式、图纸尺寸设置
- `sim.py` 中的电压探针插入（原 `schematic_builder._add_probes`）
- `com.py` 中的 COM 调用方式与 gen_py 缓存修复
- `structural.py` 中的结构模板提取（原 `tools/extract_native_component_templates.py`）

```
MIT License

Copyright (c) 2026 Multisim MCP contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## PKWare DCL 码表

`codec.py` 中的哈夫曼码表是 PKWare Data Compression Library（implode/explode）格式的固定常量，与 StormLib 的 pklib 实现所用的码表相同，编解码代码为本项目自行实现。

## 运行时依赖（不随本仓库分发）

- [electronics-workbench-decoder](https://github.com/cinderblock/electronics-workbench-decoder)（ISC）：仅 `parts fetch` 读取 Multisim 主元件库索引时通过 Node.js 调用。
- NI Multisim 14：元件模板库由用户本机已授权的 Multisim 示例和数据库在本地生成，存放在 `%LOCALAPPDATA%\multisim-cli\parts`，不包含在本仓库中，也不应再分发。
