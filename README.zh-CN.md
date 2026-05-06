<p align="right">
  <a href="./README.md">English</a> ·
  <b>简体中文</b> ·
  <a href="./README.ja.md">日本語</a>
</p>

![Sponsio](assets/readme-banner.png)

<p align="center">
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-orange.svg" alt="License"></a>
  <a href="https://pypi.org/project/sponsio/"><img src="https://img.shields.io/badge/install-pip%20install%20sponsio-blue?logo=python&logoColor=white" alt="Install from PyPI"></a>
  <a href="https://sponsio.dev"><img src="https://img.shields.io/badge/Visit-sponsio.dev-181818?logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjI4MyA3NjMgMzczIDM3MyI%2bPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMCwyMDQ4KSBzY2FsZSgwLjEsLTAuMSkiIGZpbGw9IiNGRkZGRkYiPjxwYXRoIGQ9Ik01MDEwIDEyNTAxIGMtNTggLTkgLTE4NyAtNDEgLTI2NyAtNjYgLTI2IC05IC05OSAtNDEgLTE2MCAtNzEgLTM1NCAtMTc0IC02MTMgLTQ3NiAtNzM2IC04NTkgLTQzIC0xMzMgLTY0IC0yNTEgLTczIC00MDcgbC03IC0xMTggLTQ2MiAwIC00NjMgMCAtNiAtMjIgYy0zIC0xMyAtMyAtNjYgMCAtMTE4IDE2IC0yODQgMTA2IC01NTYgMjYwIC03ODggMTEzIC0xNjggMzI0IC0zNTYgNTE2IC00NjAgMjcyIC0xNDcgNjM3IC0xOTAgOTY4IC0xMTUgMjM2IDUzIDQ1NiAxNzggNjQwIDM2MyAyNzIgMjczIDQxMyA2MTEgNDIzIDEwMjAgbDMgMTE1IDQ1NSA1IDQ1NCA1IDMgNDUgYzQgNDcgLTEyIDIwNyAtMjkgMzAwIC0xMDcgNTkyIC01MjMgMTAzMSAtMTA5NCAxMTU3IC03OSAxNyAtMzQxIDI2IC00MjUgMTR6IG0zMjAgLTk2MCBjNzMgLTI3IDE2MiAtOTkgMjA1IC0xNjQgNTggLTg3IDEwNCAtMjM5IDEwNSAtMzQ1IGwwIC01MiAtNDU3IDIgLTQ1OCAzIC0zIDQ4IGMtNSA3MyAyNCAyMDQgNjAgMjc3IDYxIDExOSAxOTEgMjI1IDMxMCAyNTAgNjQgMTMgMTc2IDUgMjM4IC0xOXogbS02MTIgLTY0MSBjMTMgLTI5NSAtMTkxIC01MjAgLTQ3MCAtNTIwIC0yMTcgMCAtMzkzIDE0NCAtNDUzIDM3MSAtMTUgNTUgLTIwIDIxMCAtOCAyMjIgMyA0IDIxNCA2IDQ2NyA1IGw0NjEgLTMgMyAtNzV6Ii8%2bPC9nPjwvc3ZnPg==&logoColor=white&labelColor=555555" alt="Visit sponsio.dev"></a>
  <a href="docs/concepts/owasp-coverage.md"><img src="https://img.shields.io/badge/OWASP%20Agentic%20Top%2010-10%2F10%20Covered-2E7D32?labelColor=555555" alt="OWASP Agentic Top 10 Covered"></a>
</p>

<p align="center">
  <a href="https://x.com/sponsiolabs"><img src="https://img.shields.io/badge/Follow%20on%20X-000000?logo=x&logoColor=white" alt="Follow on X"></a>
  <a href="https://www.linkedin.com/company/sponsio-labs/"><img src="https://img.shields.io/badge/Follow%20on%20LinkedIn-0A66C2?logo=linkedin&logoColor=white" alt="Follow on LinkedIn"></a>
  <a href="https://discord.gg/s8TfPnZWUm"><img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?logo=discord&logoColor=white" alt="Join our Discord"></a>
</p>

<p align="center">⭐ <em>帮助我们壮大 Sponsio 社区，共建更完善的合约库与策略强制约束。给本仓库点个 Star！</em></p>


# Sponsio

**面向 AI Agent 的运行时强制约束。** 用自然语言输入策略，Sponsio 将其编译为不可绕过的确定性 Agent 合约。强制延迟低于 0.01ms，运行时零 LLM 成本，[覆盖全部 10 项 OWASP Agentic 风险](docs/concepts/owasp-coverage.md)。

> **Agent 合约**是在每一次 Agent 操作时执行的运行时检查，[由形式化方法支撑](docs/concepts/formal-methods.md) —— 它*不是*一段你的 Agent 可以无视或越狱绕过的系统提示词。

**适配任意技术栈。** LangChain、Claude Agent、OpenAI Agents、Google ADK、CrewAI、Vercel AI、MCP，或任何自定义工具调用循环。Python · TypeScript · Prompt · Agent Skills。

*演示视频即将发布*

---

## 当前最先进的 Agent 安全方案

<p align="center">
  <img src="assets/sponsio-architecture.png" alt="Sponsio architecture: Agent Flow + (Natural Language + Pattern Library) compile into Contracts (Assumption → Enforcement), enforced by a Fuzzy LTL Monitor (deterministic + stochastic) that decides Pass / Block · Warn · Escalate / Redirect for every function call, with full audit trail logs feeding back to the agent." width="900">
</p>

在 [ODCV-Bench](https://arxiv.org/abs/2512.20798) —— 一项来自 [McGill DMaS](https://github.com/McGill-DMaS/ODCV-Bench) 的第三方基准，覆盖 12 个前沿 LLM × 80 条执行轨迹（含 Claude-Opus-4.6） —— 上，无防护的模型在 **11.5%–66.7% 的运行中作弊**。接入 Sponsio 后，**平均 84.5% 的失准行为被拦截**，而下一档已公开发布的运行时护栏（[Salus, YC W26](https://www.ycombinator.com/companies/salus)，[2026 年 2 月发布](https://yctierlist.com/w26/salus/)）在同一基准上仅达 52%。在 `Financial-Audit-Fraud-Finding` 场景中，**前沿模型在 67% 的试验里实施了欺诈（16/24）**；接入 Sponsio 后，**100% 被拦截**。

### 为什么选 Sponsio


| 方案                              | 适用场景                                               | 失效之处                                                                                           | Sponsio 如何解决                                                                                                                        |
| ------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **提示词注入过滤器**          | 生成前作用于输入文本                               | 对新表述容易漂移；只看文本，看不到工具调用；不理解操作历史                        | 在函数调用执行*之前*，结合完整 trace 上下文，强制约束*哪些*工具可以运行、*以什么*顺序、*带什么*参数            |
| **输出校验器**                 | 生成后作用于响应字符串                        | 错误操作（如退款、写库、API 调用）可能已经发生                                    | 在执行*之前*拦截调用；基于完整操作历史推理，而非仅看最近一次输出字符串                                      |
| **LLM-as-Judge**                      | 灵活，能处理模糊属性；适用于离线评测 | 判定具有随机性，延迟数百毫秒，自身可被提示词注入 —— 不适合作为同步关卡 | 亚 0.01ms 的确定性检查，热路径零 LLM 调用；针对模糊属性的随机性流水线为可选项                             |
| **沙箱与访问控制列表（ACL）** | 在身份与资源层面提供强边界隔离 | 削弱 Agent 能力。按*谁*与*什么资源*放行，而非按*行为序列*                | 在操作序列上强制时序合约，包含顺序、历史与多步不变量，同时保留 Agent 能力 |


与其他确定性强制方案相比，Sponsio 的优势：

**1. 面向序列操作的时序合约，而非无状态规则匹配。** 现有强制方案孤立地评估每一次操作。Sponsio 在完整执行轨迹上推理：*"send_email 之前必须 verify_recipient"*、*"访问 PII 之后不得有外部调用"*、*"refund_payment 每会话不超过 3 次"*。

**2. 机器可验证，而非启发式。** 合约编译为 LTL 公式，再编译为确定性有限自动机。每个判定都是确定性的 DFA 状态转移，而非概率置信分数。这与硬件验证（Intel FPU 正确性、AWS S3 TLA+）使用的是同一证明技术。[工作原理 →](docs/concepts/formal-methods.md)

**3. 几分钟即可上防护，无需学习任何 DSL。** 现有工具要求从零手写 YAML / Rego / Cedar 策略。Sponsio 提供四条上手路径：

- **自动推断** —— `sponsio init`（交互式向导）读取你的工具签名，写出起始合约
- **合约库** —— 按能力（`sponsio:capability/shell`、`…/filesystem`）或按事件（`sponsio:incident/openclaw`）引入预制 bundle；每个 bundle 在底层组合 44 个确定性模式（随机性 atom 在 Sponsio Cloud 提供）
- **自然语言** —— `sponsio validate "..."` 将自然英语编译为 LTL
- **策略文档** —— `sponsio scan --policy security.md` 解析现有的合规文档

**4. 框架无关、低依赖。** 其他工具以"全家桶"形态交付 —— 捆绑身份、SRE、仪表盘、编排。Sponsio 是单一的强制库，可与你已有的可观测性、IAM 与编排无缝并行使用。

---

## 快速开始

选择你项目所用的语言。一段 prompt 或两行 CLI 命令即可立即接入。

### Python

**粘贴到 Claude Code / Codex / Cursor 中。** Agent 会协助走完完整接入流程。点击查看完整 prompt 模板。注意：Cursor 由于自身 harness 设计，可能无法在对话里明确显示 Sponsio 的拦截动作。

<p align="center">
  <a href="docs/getting-started/onboard-prompt.md#python-project"><img src="https://img.shields.io/badge/One--shot%20prompt-Python-3776AB?logo=python&logoColor=white&labelColor=555555" alt="One-shot prompt: Python"></a>
</p>

**或自行运行 CLI：**

```bash
pip install sponsio
sponsio init .
```

`init` 是一个交互式向导。它会检测你的框架（LangGraph / OpenAI / Claude Agent / Vercel AI / CrewAI / MCP / …），询问要接入哪些 IDE host（Claude Code / Codex / Cursor / OpenClaw，每个均可设为 `none` / `skill` / `full` 层级），以及 observe 还是 enforce 模式。然后写出 `sponsio.yaml`，并打印两行接入补丁：

```python
from sponsio.langgraph import Sponsio
from langgraph.prebuilt import create_react_agent

guard = Sponsio(config="sponsio.yaml", agent_id="coding_agent")
agent = create_react_agent(model, guard.wrap(tools))
```

### TypeScript

**粘贴到 Claude Code / Codex / Cursor 中：**

<p align="center">
  <a href="docs/getting-started/onboard-prompt.md#typescript-project"><img src="https://img.shields.io/badge/One--shot%20prompt-TypeScript-3178C6?logo=typescript&logoColor=white&labelColor=555555" alt="One-shot prompt: TypeScript"></a>
</p>

**或自行运行 CLI：**

```bash
npm install -D @sponsio/sdk
npx sponsio init .
```

> **说明** —— TS 向导目前是单维度的（provider × mode × agent）。要走完包含 IDE host 插件（Claude Code / Codex / Cursor / OpenClaw）的完整多维度流程，请把上面的 **Python** prompt 粘贴进你的 IDE Agent —— 它对 TS 项目同样适用（驱动 Python `sponsio` CLI，写出与 TS 兼容的 `sponsio.yaml`）。

```typescript
import { Sponsio } from "@sponsio/sdk";
import { wrapTools } from "@sponsio/sdk/langchain";
import { ToolNode } from "@langchain/langgraph/prebuilt";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "coding_agent" });
const toolNode = new ToolNode(wrapTools(tools, guard));
```

### 致 OpenClaw 社区

上面的 Python prompt 同样是你的安装路径。当向导询问 IDE host 时选择 `openclaw=full`；之后 Sponsio 会通过合约引擎对你的 OpenClaw 运行时中的每一次 `before_tool_call` 事件做关卡检查，并通过内置的 `sponsio:incident/openclaw` 包覆盖 ClawHavoc + CVE-2026-25253。

在终端实时查看拦截动作 —— 每一次 Sponsio 对你 OpenClaw 运行时的判定都会流式输出到这里：

```bash
sponsio host trace openclaw --follow
```

---

> `sponsio.yaml` 也可以手写、从策略文档扫描生成（`sponsio scan --policy policy.md`），或从 trace 中挖掘（`sponsio refresh`）。语法见：[docs/concepts/contracts.md](docs/concepts/contracts.md)。

> **完整指引：** [QUICKSTART.md](QUICKSTART.md) —— 配置参考、observe → enforce 切换、`sponsio refresh`、CI 接线、故障排查。

---

## 基准测试与性能

Sponsio 在两个公开的 Agent 安全测试套件上做了基准测试，覆盖两类不同的失败模式 —— 理性的 KPI 压力下指标博弈，以及危险的 bash / python 片段检测。所有测试均基于已发布的执行轨迹做离线回放，而非由 Sponsio 自行编写的测试集；并且**仅使用合约库**（拦截路径上不做按场景的 LLM 扫描）。


| 基准                                  | 衡量内容                          | Sponsio 结果                                                                            |
| ------------------------------------------ | ----------------------------------------- | ----------------------------------------------------------------------------------------- |
| **ODCV-Bench**（12 LLM × 80 条执行轨迹） | KPI 压力下的意图完整性       | 拦截率 **84.5%**（下一档已公开基线：**52%**）· 干净场景下 **0 个新增 FP** |
| **RedCode-Exec**（1,410 个用例）             | 危险 bash / python 片段检测 | **bash 95% · python 90% · 综合 92%** · 60 文件干净代码审计上**实用性 FP 0%** |


两个库都以可加载的合约包形式发布。确定性无法刻画的语义属性（语气、幻觉、自然语言输出中的范围漂移）需要 LLM judge —— OSS 引擎提供可插拔的 `Judge` 扩展点；托管的随机性流水线属于 [Sponsio Cloud](docs/reference/oss-scope.md#in-sponsio-cloud-commercial--pip-install-sponsiocloud) 功能。

### 逻辑缺陷类失败同样可以确定性地捕获

传统观点认为 RedCode 的逻辑缺陷类目 —— 偏见决策逻辑、弱正则绕过、缺失鉴权模式、算法缺陷、内存泄漏 —— 是"无系统调用指纹的行为缺陷"，必须依赖语义 LLM judge。我们对此提出反驳。每一类此类失败都呈现为*有限的代码文本模式*；一旦把模式枚举出来，确定性即可生效。新增的七层（`bias_logic`、`weak_regex`、`algo_flaw`、`memory_leak`、`match_no_default`、`missing_auth`、`redcode_unauth_marker`）将 RedCode python 拦截率从 **69% 提升到 90%**，并在干净代码审计（Sponsio 自身的源码、测试与 API 路由）上保持 **0/60 FP**。确定性 / 随机性的边界比绝大多数护栏作者所设想的要宽得多；随机性应保留给真正存在于自由格式输出中的属性（语气、幻觉、忠实度），而非那些有限枚举此前未被充分挖掘的代码形态模式。

### 热路径性能


| 工作负载                                                  | 合约数 | p50           | p99       |
| --------------------------------------------------------- | --------- | ------------- | --------- |
| **合成微基准**（单合约，预热 DFA） | 1         | **0.0052 ms** | 0.012 ms  |
| **ODCV-Bench 强制项**（1,438 次调用，扫描发现）    | 6–18      | **0.139 ms**  | 0.765 ms  |
| **RedCode bash**（每命令 3,848 次调用）                | 7         | 0.434 ms      | 0.558 ms  |
| **RedCode python**（整脚本 810 次调用）               | 9         | 0.811 ms      | 1.035 ms  |


**给后端工程师的参照：** 在 ODCV 强制项上 0.139 ms 的 p50，意味着 Sponsio 热路径增加的开销**比一次本地 Redis 读取还少**（典型 0.1–0.5 ms）。

<u>**比任何 LLM-as-judge 护栏快 5,000×–60,000×**</u>（gpt-4o-mini、Lakera Guard、OpenAI Moderation —— 每次检查均为 50–800 ms），在同等"每次工具调用"工作负载下，热路径上的 LLM 成本为零。每次调用的延迟随合约数量线性增长；在所有测得的工作负载下，p99 均保持在 1.04 ms 以内。最重的场景（对整段 RedCode python 脚本做 9 合约分层正则）依然**比最便宜的 LLM-as-judge 调用快 50×**。

完整的按模型拆分、方法论与基准脚本：[`docs/reference/benchmarks.md`](docs/reference/benchmarks.md)。

### 当前数字是起点，不是上限

```text
production traces ──→ sponsio scan ──→ proposed contracts
       ↑                                       │
       │                                       ▼
       └──────── enforcement ←──────── library (versioned)
```

**当前 84.5% / 92% 是起点，不是上限。** 合约库从你的 trace 中生长，并回流上游 —— 每一种新攻击模式、每一次新观察到的不安全调用，都会进入下一个版本。

---

## 合约库

开箱即用的 16 个**合约 bundle**，按层级组织（always-on / per-tool / per-incident）。每个 bundle 都是一个 YAML 包，由 Sponsio 的 44 个确定性模式组合而成（随机性 atom 在 Sponsio Cloud 提供）。把它放进 `sponsio.yaml`，一行即可让你的 Agent 防护住一类已知失败，无需逐合约编写。下方高亮的 7 个是最常用的。

### 起始 bundle


| Bundle | 层级 | 规则数 | 适用对象 |
| --- | --- | --- | --- |
| `sponsio:core/universal` | Always-on | 5 sto（Cloud） | 任意 LLM Agent。响应级检查：提示词注入、越狱、有害内容、毒性、语义 PII。需要配置 judge —— 由 [Sponsio Cloud](docs/reference/oss-scope.md) 托管，或通过 OSS 的 `Judge` 扩展点 BYO judge。未配置时，OSS 上仅记录并跳过。 |
| `sponsio:core/runaway` | Always-on | 5 det | 任何使用 token、委派或工具循环的 Agent。"带信用卡的 while(true)"防御：token 预算、委派深度、循环上限。 |
| `sponsio:capability/shell` | Per-tool | 11 det | 暴露 `exec` / `bash` 的 Agent。捕获 `rm -rf /`、fork 炸弹、`curl \| bash`、反向 shell、续行规避。灵感来自 [Claude Code #10077](https://github.com/anthropics/claude-code/issues/10077)（rm -rf $HOME，2025 年 10 月）、[Replit 生产数据库被擦事件](https://www.theregister.com/2025/07/21/replit_saastr_vibe_coding_incident/)（[Fortune 报道](https://fortune.com/2025/07/23/ai-coding-tool-replit-wiped-database-called-it-a-catastrophic-failure/)，2025 年 7 月）以及 [Ansible `rm -rf {foo}/{bar}` 摧毁 1,535 台服务器的事后复盘](https://developers.slashdot.org/story/16/04/14/1542246/man-deletes-his-entire-company-with-one-line-of-bad-code)（Marsala，2016）。 |
| `sponsio:capability/filesystem` | Per-tool | 13 det | 暴露 `read` / `write` / `edit` / `apply_patch` 的 Agent。敏感路径拒绝、工作区范围限定、引导文件关卡（`CLAUDE.md`、`AGENTS.md`、`.cursorrules`）。灵感来自 [OpenClaw weather-skill `.env` 数据外泄](https://www.trendmicro.com/en_us/research/26/b/openclaw-skills-used-to-distribute-atomic-macos-stealer.html) 与 [Cursor `.cursorignore` 绕过（CVE-2025-64110 / GHSA-vhc2-fjv4-wqch）](https://github.com/cursor/cursor/security/advisories/GHSA-vhc2-fjv4-wqch)。 |
| `sponsio:incident/openclaw` | Incident | 45 mixed | OpenClaw / ClawCode 用户。覆盖 [CVE-2026-25253](https://nvd.nist.gov/vuln/detail/CVE-2026-25253)（WebSocket 一键 RCE）、[ClawHavoc —— ClawHub 上 1,184 个恶意 skill](https://cyberpress.org/clawhavoc-poisons-openclaws-clawhub-with-1184-malicious-skills/)（Koi Security 披露，2026 年 2 月）、`--yolo` 标志以及 weather-skill 数据外泄。是一个可借鉴 fork 规则的范例。 |
| `sponsio:incident/cursor-railway-wipe` | Incident | mixed | 复盘 [PocketOS 生产数据库被擦事件（2026 年 4 月 24 日）](https://www.theregister.com/2026/04/27/cursoropus_agent_snuffs_out_pocketos/) —— Cursor + Claude Opus 4.6 通过一个授权过宽的 Railway API token，在 9 秒内删除了生产环境与备份。（[Tom's Hardware](https://www.tomshardware.com/tech-industry/artificial-intelligence/claude-powered-ai-coding-agent-deletes-entire-company-database-in-9-seconds-backups-zapped-after-cursor-tool-powered-by-anthropics-claude-goes-rogue) · [Railway 官方事后复盘](https://blog.railway.com/p/your-ai-wants-to-nuke-your-database)）捕获凭证范围滥用 + 破坏性 API 关卡。 |
| `sponsio:incident/claude-code-secret-bypass` | Incident | mixed | 复盘 [CVE-2025-55284](https://www.sentinelone.com/vulnerability-database/cve-2025-55284/)（safe-command 允许列表过宽 → 文件读取确认绕过）以及 [deny 规则上限绕过](https://adversa.ai/blog/claude-code-security-bypass-deny-rules-disabled/)（用 50 个子命令做 padding 静默禁用 deny 规则）。捕获机密读取 + 参数 padding 规避。 |


```yaml
# sponsio.yaml — one-line bundle inclusion
agents:
  my_agent:
    workspace: "/srv/my-bot"
    include:
      - sponsio:core/runaway          # always-on
      - sponsio:core/universal        # always-on
      - sponsio:capability/shell      # if your agent runs commands
      - sponsio:capability/filesystem # if your agent touches files
```

`sponsio init` 会基于检测到的工具清单自动选择 tier-0 bundle。你可以在不 fork 包的前提下禁用或重新调优单条规则：`customized:` 允许通过 `desc`、`pack_source` 或 `pattern` 字段定位规则。通过 `tool_rename:` 把规范工具名（`exec`、`read`、`edit`）重命名为你 Agent 中的名字。

完整的 bundle 参考见 [`docs/reference/contract-lib.md`](docs/reference/contract-lib.md)。bundle 所组合的底层原语单独编目：44 个确定性模式见 [`docs/reference/patterns.md`](docs/reference/patterns.md)。随机性 atom（用于语气、幻觉、范围漂移等的 LLM-judge 评估器）属于 [Sponsio Cloud](docs/reference/oss-scope.md#in-sponsio-cloud-commercial--pip-install-sponsiocloud) —— OSS 引擎提供可插拔的 `Judge` 扩展点用于 BYO judge。

> **想要面向你 Agent 类型的 bundle？** 这是目前杠杆率最高的贡献方式。带上你的事件、CVE 或模式，[开 issue](https://github.com/SponsioLabs/Sponsio/issues/new)。

---

## 集成

选择你的框架 —— 每个块展开后是可直接接入的代码片段。Python 与 TypeScript 共享同一引擎与 DSL。

<details>
<summary><b>无框架</b> —— 自定义工具调用循环</summary>


```python
from sponsio import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="bank_bot")

for name, args in agent_calls:
    result = guard.guard_before(name, args)
    if result.blocked:
        continue
    output = tools[name](**args)
    guard.guard_after(name, output)
```

```typescript
import { Sponsio } from "@sponsio/sdk";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "bank_bot" });

const result = guard.guardBefore(name, args);
if (!result.blocked) {
  const output = tools[name](args);
  guard.guardAfter(name, output);
}
```


</details>

<details>
<summary><b>LangGraph / LangChain.js</b> —— 包装工具</summary>


```python
from sponsio.langgraph import Sponsio
from langgraph.prebuilt import create_react_agent

guard = Sponsio(config="sponsio.yaml", agent_id="hr_bot")
agent = create_react_agent(llm, guard.wrap(tools))
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { wrapTools } from "@sponsio/sdk/langchain";
import { ToolNode } from "@langchain/langgraph/prebuilt";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "hr_bot" });
const toolNode = new ToolNode(wrapTools(tools, guard));
```


</details>

<details>
<summary><b>Claude Agent SDK</b> —— 原生 hook，零工具包装</summary>


```python
from sponsio.claude_agent import Sponsio
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

guard = Sponsio(config="sponsio.yaml", agent_id="support_bot")
options = ClaudeAgentOptions(hooks=guard.hooks())

async with ClaudeSDKClient(options=options) as client:
    await client.query("Refund order #W456.")
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { sponsioHooks } from "@sponsio/sdk/claude-agent";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "support_bot" });
const hooks = sponsioHooks(guard);
// Pass `hooks` to ClaudeSDKClient options.
```


</details>

<details>
<summary><b>OpenAI SDK</b> —— monkey-patch 或显式包装</summary>


```python
from sponsio.openai import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="db_admin")
resp = client.chat.completions.create(...)
guard.check_response(resp)
```

```typescript
import OpenAI from "openai";
import { Sponsio } from "@sponsio/sdk";
import { wrapOpenAI } from "@sponsio/sdk/openai";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "db_admin" });
const client = wrapOpenAI(new OpenAI(), guard);
```

需要在脚本 / notebook 中无 YAML 快速接入：`from sponsio.openai import patch_openai`。

</details>

<details>
<summary><b>OpenAI Agents SDK</b> —— 包装 Agent 工具</summary>


```python
from sponsio.agents import Sponsio
from agents import Agent, Runner

guard = Sponsio(config="sponsio.yaml", agent_id="deploy_bot")

agent = Agent(
    name="deploy_bot",
    instructions="Ship v2.1 to production.",
    tools=guard.wrap([run_tests, deploy_staging, deploy_production]),
)

result = Runner.run_sync(agent, "Deploy v2.1 now.")
```

TypeScript：暂未支持。

</details>

<details>
<summary><b>Google ADK</b> —— 包装 Agent 工具（Gemini）</summary>


```python
from sponsio.google_adk import Sponsio
from google.adk.agents.llm_agent import Agent

guard = Sponsio(config="sponsio.yaml", agent_id="travel_agent")

root_agent = Agent(
    name="travel_agent",
    model="gemini-flash-latest",
    instruction="Search before booking. Charge only once.",
    tools=guard.wrap([search_flights, book_flight, charge_payment]),
)
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { wrapGoogleAdkTools } from "@sponsio/sdk/google-adk";
import { LlmAgent } from "@google/adk";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "travel_agent" });
const tools = wrapGoogleAdkTools([searchFlights, bookFlight, chargePayment], guard);
export const rootAgent = new LlmAgent({ name: "travel_agent", tools, model: "gemini-flash-latest" });
```


</details>

<details>
<summary><b>Vercel AI SDK</b> —— 中间件</summary>


```python
from sponsio.vercel_ai import Sponsio

guard = Sponsio(config="sponsio.yaml", agent_id="publish_bot")

async for msg in agent.run(model, messages, middleware=[guard.wrap()]):
    ...
```

```typescript
import { Sponsio } from "@sponsio/sdk";
import { sponsioMiddleware } from "@sponsio/sdk/vercel-ai";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "publish_bot" });
const middleware = sponsioMiddleware(guard);
```


</details>

<details>
<summary><b>CrewAI</b> —— Crew 级 hook</summary>


```python
from sponsio.crewai import Sponsio
from crewai import Agent, Crew, Task

guard = Sponsio(config="sponsio.yaml", agent_id="moderator")

crew = Crew(
    agents=[agent],
    tasks=[task],
    before_tool_call=guard.on_tool_start,
    after_tool_call=guard.on_tool_end,
)
result = crew.kickoff()
```

TypeScript：暂未支持。

</details>

<details>
<summary><b>MCP</b> —— 代理 MCP 客户端</summary>


```python
from sponsio.mcp import MCPContractProxy

# Build a sponsio System from your contracts — see runnable example for full wire-up.
proxy = MCPContractProxy(mcp_client=your_mcp_client, system=system)

# Use `proxy` wherever you called the raw MCP client; contracts apply transparently.
result = await proxy.call_tool("write_external_api", {"data": "batch_1"})
```

TypeScript：暂未支持。

</details>



---

> **关于上述代码片段。** 所有示例假设你已经先运行了 `sponsio init .`，向导会跑完，基于你的工具清单生成带起始合约集的 `sponsio.yaml`，并打印出可粘贴的包装代码。要以不同方式填充该 YAML —— 模式库 bundle、手写规则、自然语言一行式，或从策略文档解析（`sponsio scan --policy security.md`）—— 见 [合约类型与编写](QUICKSTART.md#contract-types-and-authoring) 与完整语法 [docs/concepts/contracts.md](docs/concepts/contracts.md)。

---

## 文档

- [快速开始](QUICKSTART.md)
- [合约 DSL](docs/concepts/contracts.md)
- [CLI 参考](docs/reference/cli.md)
- [集成](docs/integrations/index.md)
- [架构](docs/concepts/architecture.md)
- [基准测试](docs/reference/benchmarks.md)
- [OWASP Agentic Top 10 覆盖](docs/concepts/owasp-coverage.md)
- [形式化方法入门](docs/concepts/formal-methods.md)
- [**OSS 承诺**](OSS_PROMISE.md) · [OSS / Cloud 边界](docs/reference/oss-scope.md) · [品牌与商标](BRAND.md)
- [更新日志](CHANGELOG.md)

*阅读本仓库的 AI Agent：[`llms.txt`](llms.txt) 列出了规范文档路径；[`llms-full.txt`](llms-full.txt) 是完整上下文的拼接全量。*

---

## 安全

Sponsio 强制运行时合约，因此其自身的正确性至关重要。发现问题？请通过 GitHub 的[安全公告表单](https://github.com/SponsioLabs/Sponsio/security/advisories/new)私下报告，而非通过公开 issue。范围、时间线以及哪些属于 in-scope（enforce 模式绕过、LTL 求值器崩溃、会话日志泄漏、judge prompt 注入等）见 [SECURITY.md](SECURITY.md)。

---

## 贡献

欢迎提交补丁、问题反馈与新模式提案。从 [CONTRIBUTING.md](CONTRIBUTING.md) 开始。

---

## 重要提示

Sponsio 强制*你*所定义的运行时合约 —— 它不会为你的应用对任何监管框架的合规性做出认证。如果你处于受监管领域（HIPAA、GDPR、SOX、EU AI Act、金融服务、医疗健康），Sponsio 的控制项以及我们的 [OWASP Agentic Top 10 映射](docs/concepts/owasp-coverage.md)是你合规计划的输入。它们**不能**替代具备资质的安全审计、法律审查或特定领域的监管分析。请在适当审查下编写你的合约，并在 Agent 工具面变化时重新评估。

确定性合约为你提供了在操作边界上的机器可验证强制约束。它们不防御 Sponsio 上游的漏洞（被攻陷的 LLM 提供方、你已加入允许列表的恶意工具、传输加密 / SBOM 来源等基础设施层风险）。完整范围见 [`SECURITY.md`](SECURITY.md)。

---

## 许可证与开源承诺

Apache 2.0 —— 见 [LICENSE](LICENSE)。

Sponsio Labs 是一家商业公司；Sponsio Cloud（`pip install sponsio[cloud]`）将于 2026 年 5 月中旬开放，提供托管的 LLM-judge 流水线、跨客户的模式情报，以及托管的多租户仪表盘。OSS 引擎已完整且可在自托管场景下投入生产 —— 关于哪些功能将永远留在 OSS、我们卖什么、以及我们对边界的承诺，见 [OSS_PROMISE.md](OSS_PROMISE.md)。

Sponsio™ 是 Sponsio Labs 的商标 —— 见 [BRAND.md](BRAND.md)。
