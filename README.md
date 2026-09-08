[English](./README.en.md) · [Website](https://diffgate.lei6393.com) · [GitHub](https://github.com/SuperMarioYL/diffgate)

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="./assets/presentation/hero-mobile-dark.svg">
  <source media="(max-width: 600px)" srcset="./assets/presentation/hero-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="./assets/presentation/hero-dark.svg">
  <img src="./assets/presentation/hero-light.svg" width="960" alt="Hero diagram">
</picture>

# diffgate

**用实际代码变化检查编辑声明。**

DiffGate 解析修改前后源码，计算结构变化，并与明确编辑声明进行比较。

## 为什么需要它

工具返回成功时，源码仍可能未变，或改到了错误符号。明确的前后声明让调用框架在继续运行前检查结构工作是否发生。

- **确定性不匹配** — 结论来自源码结构。
- **带作用域声明** — 声明可以针对具体符号作用域。
- **共用核心** — CLI 与 MCP 暴露同一验证器。

## 架构

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="./assets/presentation/architecture-mobile-dark.svg">
  <source media="(max-width: 600px)" srcset="./assets/presentation/architecture-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="./assets/presentation/architecture-dark.svg">
  <img src="./assets/presentation/architecture-light.svg" width="960" alt="Architecture diagram">
</picture>

Tree-sitter 解析器提取符号和作用域。验证器计算新增、删除、签名变化与函数体变化，再匹配支持的动作声明。CLI 与 MCP 返回相同的确定性结论和不匹配证据。

| 组件 | 职责 |
| --- | --- |
| `Before / after blobs` | EditClaim input |
| `Tree-sitter symbols` | parsers.py |
| `Claim verifier` | verifier.py |
| `CLI / MCP verdict` | Machine-readable mismatch |

## 安装与快速上手

使用仓库清单指定的运行时版本构建，并在仓库根目录运行示例。

```bash
git clone https://github.com/SuperMarioYL/diffgate.git
cd diffgate
uv venv .venv
uv pip install --python .venv/bin/python -e .
source .venv/bin/activate
```

将相同 foo 到 bar 改名声明分别与未变源码和实际改名函数比较。

```bash
.venv/bin/python examples/presentation-demo.py
```

## 实际运行示例

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="./assets/presentation/process-mobile-dark.svg">
  <source media="(max-width: 600px)" srcset="./assets/presentation/process-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="./assets/presentation/process-dark.svg">
  <img src="./assets/presentation/process-light.svg" width="960" alt="Process diagram">
</picture>

The unchanged case is rejected and the structurally renamed case is accepted.

```text
{"case": "unchanged", "passed": false, "mismatches": ["claimed rename foo\u2192bar but neither name appears in the structural diff (no-op edit)"]}
{"case": "renamed", "passed": true, "mismatches": []}
```

完整命令与输出保存在 [docs/demo-results.json](./docs/demo-results.json). 输入和复现代码均随仓提供。

![已有终端录制](./assets/demo.gif)

保留已有录制供参考；上方文字示例给出当前可复现的操作。

## 用法

CLI 提供以下操作。示例之外的命令需要替换成你的文件路径或标识。

```bash
diffgate verify --before before.py --after after.py --claim "rename foo->bar" --json
diffgate diff --before before.py --after after.py --json
diffgate mcp-server --stdio
```

## 配置

指定前后文件与支持的声明。--lang 可覆盖按扩展名检测的语言，--json 输出结构化数据。程序接口 EditClaim 接受源码、语言以及带可选 scope、new_symbol 的 ClaimedAction。

## 集成与职责分工

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="./assets/presentation/integrations-mobile-dark.svg">
  <source media="(max-width: 600px)" srcset="./assets/presentation/integrations-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="./assets/presentation/integrations-dark.svg">
  <img src="./assets/presentation/integrations-light.svg" width="960" alt="Integrations diagram">
</picture>

以下路径已有源码实现。按任务选择输入，并把生成的结果与项目一起保存。

| 路径 | 已实现职责 |
| --- | --- |
| Source blobs / files | Before and after input |
| Edit claims | Rename, add, delete, move, signature |
| Multiple grammars | Supported tree-sitter languages |
| CLI / MCP | Harness integration surfaces |

## 限制与后续方向

- 结构匹配不证明语义正确、所有引用已更新或测试通过。
- 声明受支持动作词汇与解析器行为限制，应继续配合常规测试。
- 示例只检查小型 Python 改名案例，不覆盖所有语言或跨文件场景。

后续语法和作用域改进应基于可复现不匹配；结构验证仍需与行为测试配合。

## 许可与贡献

许可见 [LICENSE](./LICENSE). 反馈问题时请提供最小输入、执行命令和实际输出。
