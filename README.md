**简体中文** | [English](./README.en.md)

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=180&section=header&text=DiffGate&fontSize=58&fontColor=ffffff&fontAlignY=38&desc=Agent%20编辑回路的结构化校验门&descAlignY=62&descSize=14" alt="DiffGate banner" />
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/status-WIP-orange.svg" alt="WIP" />
  <img src="https://img.shields.io/badge/Agent-ready-7c3aed" alt="Agent ready" />
  <img src="https://img.shields.io/badge/MCP-compatible-0ea5e9" alt="MCP compatible" />
</p>

> **DiffGate 是给 Agent 编辑回路装的结构化校验门 —— 改没改、改对没改，AST 说了算。**

## 目录

- [为什么需要 DiffGate](#为什么需要-diffgate)
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

## 演示

> 📼 Demo coming soon (see [assets/README.md](./assets/README.md))

60 秒画面：Claude Code 声称把 `module_x.py` 里的 `foo` 全部改名为 `bar` → DiffGate 解析双侧 AST → 发现实际有 0 处 rename → `exit_code=1` → Agent 自动重试。

## 它是怎么工作的

三个本地进程，全部不联网：

```
[ coding agent ]  ──tool_call──►  [ diffgate MCP server (python) ]
                                          │
                                          ▼
                                  [ verifier core ]
                                   ├── tree-sitter 解析器 (py/ts/go/rs)
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
| `languages`      | list   | `[py, ts, go, rs]` | 启用的 tree-sitter 解析器                                  |
| `strict_renames` | bool   | `true`             | rename claim 必须改到所有引用；`false` 时只校验声明位置    |
| `mcp.transport`  | enum   | `stdio`            | `stdio` 或 `sse`                                           |
| `bench.traces`   | path   | 内置 200 条        | `diffgate bench` 使用的 ground-truth JSONL                 |

完整配置见 `diffgate --help`。

## 路线图

- [x] **m1 — `diffgate verify`**：CLI + Python/TS 解析 + 20 条 silent-lie fixture 全部命中
- [ ] **m2 — `diffgate mcp-server`**：MCP 工具 `verify_edit`，配合 Claude Code / Cursor 即开即用
- [ ] **m3 — `diffgate bench`**：回放 200 条 trace，输出 precision/recall 到 README 徽章
- [ ] **v0.2 — DiffGate Cloud**（付费）：跨团队聚合 catch-rate、SSO、Prometheus exporter；Java / C++ 解析器
- [ ] **v0.3 — 框架集成**：LangGraph / Mastra / Autogen 官方可选 gate

## 付费 / Pricing

**自托管永远免费。** CLI、MCP server、tree-sitter 解析器、bench 工具全部 MIT 协议，没有任何 phone-home。

**付费产品（v0.2）—— DiffGate Cloud**：面向字节、阿里、腾讯、美团、京东这类内部 Dev Platform 团队的托管聚合面板。把每个工程师、每个团队的 Agent catch-rate 汇总到一个仪表盘，带 SSO、审计日志、Prometheus exporter，并优先支持 Java / C++ 解析器。定价大约**¥1,200 / 工程师 / 年**（按容量分层），约为一个 Cursor Business 席位的 1/3 —— 定位为"你已经付了席位钱的那个 Agent，再加一道安全网"。

试点流程：14 天免费 → 第 14 天给一份团队聚合的 catch-rate + 估算的"节省工程师小时"读数 → 年合同（最低 ¥100k 起，按席位扩容）。Stripe（USD）+ 阿里云国际 / 微信支付商户号（CNY）双通道结算。

如果你在字节 / 阿里 / 腾讯 / 美团 / 京东的内部 Dev Platform，欢迎邮件 `itleiyu@gmail.com` 聊试点。

## License & 贡献

MIT，详见 [LICENSE](./LICENSE)。Bug、误判、漏判一律欢迎开 issue —— 把 `EditClaim` 的 before/after 贴上来即可，复现非常便宜。PR 之前先开 issue 对齐一下范围。

## Share this

```
DiffGate — 给 Coding Agent 编辑回路的结构化校验门。
你已经付了 Cursor / Claude Code 的席位钱，再加一道 Agentic 安全网，
让"声称改完了"的假成功变成 exit-code 1。OSS + MCP。
https://github.com/<your-org>/diffgate
```

---

<sub>本仓库由 [ai-radar](https://github.com/itleiyu/ai-radar) scan `scan-2026-05-25-1815` 的 winner `need_t3ver002` 产出。计划详见 `workspace/projects/scan-2026-05-25-1815/F-plan/winner-t3ver002/`。</sub>
