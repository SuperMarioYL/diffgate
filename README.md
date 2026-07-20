**简体中文** | [English](./README.en.md)

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=180&section=header&text=DiffGate&fontSize=58&fontColor=ffffff&fontAlignY=38&desc=Agent%20编辑回路的结构化校验门&descAlignY=62&descSize=14" alt="DiffGate banner" />
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="Apache License 2.0" /></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python 3.12+" />
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/SuperMarioYL/diffgate/main/bench/catch_rate.json" alt="Catch rate" />
  <img src="https://img.shields.io/badge/languages-9-success" alt="9 languages" />
  <img src="https://img.shields.io/badge/Agent-ready-7c3aed" alt="Agent ready" />
  <img src="https://img.shields.io/badge/MCP-compatible-0ea5e9" alt="MCP compatible" />
</p>

> **DiffGate 是给 Agent 编辑回路装的结构化校验门 —— 改没改、改对没改，AST 说了算。**

## 目录

- [为什么需要 DiffGate](#为什么需要-diffgate)
- [架构](#架构)
- [快速开始](#快速开始)
- [演示](#演示)
- [它是怎么工作的](#它是怎么工作的)
- [对比同类项目](#对比同类项目)
- [配置说明](#配置说明)
- [路线图](#路线图)
- [付费 / Pricing](#付费--pricing)
- [License & 贡献](#license--贡献)
- [Share this](#share-this)

## 为什么需要 DiffGate

Cursor、Claude Code、Codex、GPT-5.5 这些 Agent 编辑代码时，经常返回 `success`，但 diff 是空的、改在了错误的文件，或者只是在注释里"承诺"了改动。Tessl 的 1281 次实测显示，这是**大型代码库上最常见的失败类**之一 —— 但**没有任何 Agent 框架在 edit 步骤之后做结构化校验**。

DiffGate 在 Agent 工具调用和下一轮 loop 之间插一道关：解析改前改后的 AST，对比 Agent 自己声明的 `claimed_actions`，不一致就返回 `exit_code=1`，让 Agent 自己重试。把一类"安静地撒谎"，变成"吵闹地报错"。

## <img src="https://api.iconify.design/tabler:topology-star-3.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 架构

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./assets/atlas-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="./assets/atlas-light.svg">
    <img src="./assets/atlas-light.svg" width="880" alt="Coding Agent 发出 EditClaim（改前/改后 blob + 声明动作），DiffGate 用 tree-sitter 解析双侧并把声明对齐到 AST diff，Verdict 要么放行（exit 0），要么返回 exit_code 1 作为结构化背压让 Agent 重试">
  </picture>
</p>

Coding Agent（Cursor / Claude Code / Codex / LangGraph）在每次编辑后发出一个 **`EditClaim`** —— 改前 blob、改后 blob、声明的动作。`cli.py` 与 `mcp_server.py` 是同一个 `verifier.verify(EditClaim) → Verdict` 的两层薄壳：核心用 tree-sitter 解析双侧 AST（Python / TypeScript / TSX / JavaScript / Go / Rust / Java / C++ / Ruby），把每条声明对齐到真实的结构化 diff。**Verdict** 要么放行让 loop 继续（`exit 0`），要么返回 `exit_code=1` 作为**结构化背压**，带着 mismatch 原因把 Agent 打回重试 —— 全程本地、离线、确定性，无守护进程、无数据库、无网络调用。

## 快速开始

```bash
pipx install diffgate                                          # ≤30s
diffgate verify --before X.py --after X.py.new --claim "rename foo→bar"
diffgate mcp-server --stdio                                    # 注册到 Claude Code / Cursor 的 mcp.json
```

接下来把下面 3 行加进 `~/.config/claude-code/mcp.json`（或 Cursor 的同等配置）：

```json
{
  "mcpServers": {
    "diffgate": { "command": "diffgate", "args": ["mcp-server", "--stdio"] }
  }
}
```

完整的 hook 教程见 [`examples/claude_code_hook.md`](./examples/claude_code_hook.md)；Cursor 集成见 [`examples/cursor_integration.md`](./examples/cursor_integration.md)。

## <img src="https://api.iconify.design/tabler:photo.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 演示

<p align="center">
  <img src="./assets/demo.gif" alt="diffgate verify 终端演示：Claude Code 声称改名但实际是空 diff，DiffGate 返回 exit_code 1" width="820" />
</p>

<sub>↑ 终端实录（由 CI 用 <a href="https://github.com/charmbracelet/vhs">vhs</a> 渲染 <a href="./docs/demo.tape">docs/demo.tape</a>，打 tag 时自动生成）。</sub>

60 秒画面：Claude Code 声称把 `module_x.py` 里的 `foo` 全部改名为 `bar` → DiffGate 解析双侧 AST → 发现实际有 0 处 rename → `exit_code=1` → Agent 自动重试。

## 它是怎么工作的

三个本地进程，全部不联网：

```
[ coding agent ]  ──tool_call──►  [ diffgate MCP server (python) ]
                                          │
                                          ▼
                                  [ verifier core ]
                                   ├── tree-sitter 解析器 (py/ts/tsx/js/go/rs/java/cpp/ruby)
                                   └── claim → ast_change 匹配器
```

核心数据原语是 **`EditClaim`**：

```python
EditClaim {
  before_blob: str
  after_blob: str
  claimed_actions: [
    {kind: "rename"|"add"|"delete"|"move"|"signature_change",
     symbol: str, scope: str}
  ]
}
→ Verdict { passed: bool, mismatches: [...], structural_diff: ast_summary }
```

`cli.py` 和 `mcp_server.py` 是同一个 `verifier.verify(edit_claim) → Verdict` 的两层薄壳。没有守护进程、数据库、网络调用。

### 作用域（scope）感知 — v0.2.0

`claimed_actions` 里的 `scope` 字段现在会被严格校验。声明 `add MyClass.helper`
只有当 `helper` 真的落在 `MyClass` 里时才放行 —— Agent 改成了一个模块级的
`helper()` 不再算数。这堵死了一类常见的"安静撒谎"：把类方法和同名的模块级
函数互相混淆。

```bash
# Agent 声称给类 A 加了方法 helper，实际只加了一个模块级函数 → exit_code 1
diffgate verify --before a.py --after a.py.new --claim "add helper in A"
```

`scope` 留空时是通配符，按符号名匹配（与 v0.1 行为完全一致），所以旧的无作用域
声明不受影响。

## 对比同类项目

诚实对比，不画饼：

| 维度                              | DiffGate            | [Aider](https://github.com/Aider-AI/aider) test-loop | [langgenius/dify](https://github.com/langgenius/dify) | [ChromeDevTools/chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) |
| --------------------------------- | ------------------- | ---------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| 拦截"空 diff / 改错文件"的假成功 | ✓                  | —                                                    | —                                                     | —                                                                                           |
| 行为正确性（跑测试）              | —                   | ✓                                                   | partial                                               | —                                                                                           |
| Agent 工作流编排                  | —                   | partial                                              | **✓ (远超 DiffGate)**                                | —                                                                                           |
| 浏览器 / DevTools 侧观察          | —                   | —                                                    | —                                                     | **✓ (远超 DiffGate)**                                                                      |
| 跨 Agent / 跨 IDE                 | ✓ (MCP 协议)        | Aider-only                                           | Dify-only                                             | Chrome-only                                                                                 |
| 部署                              | 单进程 / 本地       | 单进程                                               | 多服务 / 容器                                         | Chrome 扩展                                                                                 |

DiffGate 只解决一类问题（结构化撒谎），不取代上面任何一个。Aider 仍然是更好的 pair-programmer；Dify 仍然是更好的 Agent 编排；chrome-devtools-mcp 在浏览器侧依然无可替代。

## 配置说明

| 键               | 类型   | 默认               | 含义                                                       |
| ---------------- | ------ | ------------------ | ---------------------------------------------------------- |
| `languages`      | list   | `[py, ts, tsx, js, go, rs, java, cpp, ruby]` | 启用的 tree-sitter 解析器                                  |
| `strict_renames` | bool   | `true`             | rename claim 必须改到所有引用；`false` 时只校验声明位置    |
| `mcp.transport`  | enum   | `stdio`            | `stdio` 或 `sse`                                           |
| `bench.traces`   | path   | 内置 200 条        | `diffgate bench` 使用的 ground-truth JSONL                 |

完整配置见 `diffgate --help`。

## 路线图

- [x] **m1 — `diffgate verify`**：CLI + Python/TS 解析 + 20 条 silent-lie fixture 全部命中
- [x] **m2 — `diffgate mcp-server`**：MCP 工具 `verify_edit`，配合 Claude Code / Cursor 即开即用
- [x] **m3 — `diffgate bench`**：回放 trace，输出 precision/recall
- [x] **v0.2 — 作用域感知校验**：`scope` 字段严格匹配，拦截"类方法 vs 同名模块函数"混淆
- [x] **v0.3 — CLI/MCP 对齐 + 多语言**：结构化 `--claim-file`（含 stdin）、多文件校验、新增 Java / C++ / Ruby 解析器，外加三处 silent-lie 修复
- [ ] **DiffGate Cloud**（付费）：跨团队聚合 catch-rate、SSO、Prometheus exporter
- [ ] **框架集成**：LangGraph / Mastra / Autogen 官方可选 gate

## 付费 / Pricing

**自托管永远免费。** CLI、MCP server、tree-sitter 解析器、bench 工具全部 Apache 2.0 协议，没有任何 phone-home。

**付费产品（v0.2）—— DiffGate Cloud**：面向字节、阿里、腾讯、美团、京东这类内部 Dev Platform 团队的托管聚合面板。把每个工程师、每个团队的 Agent catch-rate 汇总到一个仪表盘，带 SSO、审计日志、Prometheus exporter，并优先支持 Java / C++ 解析器。定价大约**¥1,200 / 工程师 / 年**（按容量分层），约为一个 Cursor Business 席位的 1/3 —— 定位为"你已经付了席位钱的那个 Agent，再加一道安全网"。

试点流程：14 天免费 → 第 14 天给一份团队聚合的 catch-rate + 估算的"节省工程师小时"读数 → 年合同（最低 ¥100k 起，按席位扩容）。Stripe（USD）+ 阿里云国际 / 微信支付商户号（CNY）双通道结算。

如果你在字节 / 阿里 / 腾讯 / 美团 / 京东的内部 Dev Platform，欢迎邮件 `leo.stack@outlook.com` 聊试点。

## License & 贡献

Apache 2.0，详见 [LICENSE](./LICENSE)。Bug、误判、漏判一律欢迎开 issue —— 把 `EditClaim` 的 before/after 贴上来即可，复现非常便宜。PR 之前先开 issue 对齐一下范围。

## Share this

```
DiffGate — 给 Coding Agent 编辑回路的结构化校验门。
你已经付了 Cursor / Claude Code 的席位钱，再加一道 Agentic 安全网，
让"声称改完了"的假成功变成 exit-code 1。OSS + MCP。
https://github.com/SuperMarioYL/diffgate
```

---

<sub>Apache-2.0 © 2026 SuperMarioYL</sub>
