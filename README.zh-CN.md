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
</p>

<p align="center">
  <a href="https://x.com/sponsiolabs"><img src="https://img.shields.io/badge/Follow%20on%20X-000000?logo=x&logoColor=white" alt="Follow on X"></a>
  <a href="https://www.linkedin.com/company/sponsio-labs/"><img src="https://img.shields.io/badge/Follow%20on%20LinkedIn-0A66C2?logo=linkedin&logoColor=white" alt="Follow on LinkedIn"></a>
  <a href="https://discord.gg/s8TfPnZWUm"><img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?logo=discord&logoColor=white" alt="Join our Discord"></a>
</p>


# Sponsio

<p align="center">
  <img src="assets/sponsio-comparison-freeze.png" alt="同一个 coding agent 在已声明的代码冻结期内运行。没有 Sponsio：删掉生产 users 表、用编造的数据回填，再写一份掩盖破坏的状态报告。接入 Sponsio：第一条破坏性 SQL 在执行前就被拦下：35 次检查、100% 确定性、0 次 LLM 调用、p50 13µs。" width="900">
</p>

**面向 AI Agent 的运行时强制约束。** Sponsio 在每一次 Agent 操作时，对照确定性的纯代码合约进行检查，强制延迟低于 0.01 ms，运行时零 LLM 成本。支持 LangChain、Claude Agent、OpenAI Agents、Google ADK、CrewAI、Vercel AI、MCP，或任何自定义工具调用循环，Python 与 TypeScript 双语言。

> **Agent 合约**是一条运行时规则，在每一次 Agent 操作时检查，[由形式化方法支撑](docs/concepts/formal-methods.md)。

> **v0.2.0a0 alpha 已发布。** `pip install --pre sponsio==0.2.0a0`。新增默认拒绝的 tool policy、按 turn 主动过滤工具菜单、redirect-to-safe 替换式拦截、人工 escalation 时的 notifier callback（Slack / 邮件 / pager）。详见 [v0.2 release notes](docs/release-notes/v0.2.0a0.md)。

---

## Sponsio 如何工作

<p align="center">
  <img src="assets/sponsio-architecture.png" alt="Sponsio 架构：Agent Flow + (Natural Language + Pattern Library) 编译为 Contracts (Assumption → Enforcement)，由 Fuzzy LTL Monitor（确定性 + 随机性）在每次函数调用上判定 Pass / Block · Warn · Escalate / Redirect，完整审计日志回流给 Agent。" width="900">
</p>

在 [ODCV-Bench](https://github.com/McGill-DMaS/ODCV-Bench)（12 个前沿 LLM × 80 条执行轨迹）上，无防护的模型在 11.5%–66.7% 的运行中作弊。**接入 Sponsio 后平均规避 95.6% 的不当行为；24/36 高风险场景 100% 拦截**。在 `Financial-Audit-Fraud-Finding` 场景中，前沿模型 16/24 次实施欺诈，**Sponsio 拦截 18/19**。RedCode-Exec（1,410 用例）综合拦截率 **92%**（bash 95% · python 90%），覆盖 60 文件干净代码审计。

逻辑检查器每条合约 p50 **0.139 ms**，**比任何 LLM-as-judge 护栏快 5,000×–60,000×**（每次检查 50–800 ms），热路径零 LLM 成本。p99 在所有测得工作负载下保持 1.04 ms 以内。

查阅[完整 benchmark 方法论与按模型拆分](docs/reference/benchmarks.md)、[与提示词过滤器 / 输出校验器 / LLM-as-judge / 沙箱的对比](docs/why.md)，或深入[架构](docs/concepts/architecture.md)与[形式化方法入门](docs/concepts/formal-methods.md)。

---

## 快速开始

一段 prompt 或两行 CLI 命令即可立即接入。

**粘贴到 Claude Code / Codex / Cursor 中。** Agent 会协助走完完整接入流程：

<p align="center">
  <a href="docs/getting-started/onboard-prompt.md#python-project"><img src="https://img.shields.io/badge/One--shot%20prompt-Python-3776AB?logo=python&logoColor=white&labelColor=555555" alt="One-shot prompt: Python"></a>
  &nbsp;
  <a href="docs/getting-started/onboard-prompt.md#typescript-project"><img src="https://img.shields.io/badge/One--shot%20prompt-TypeScript-3178C6?logo=typescript&logoColor=white&labelColor=555555" alt="One-shot prompt: TypeScript"></a>
</p>

**或自行运行 CLI：**

```bash
pip install sponsio        # 或 npm install -D @sponsio/sdk
sponsio init .             # 交互式向导：检测框架、选择 IDE host、observe vs enforce
```

向导会自动检测你的框架并打印对应的接入片段。手动接线见 [docs/integrations/](docs/integrations/index.md)。[OpenClaw 用户](docs/integrations/openclaw.md)开箱即享 ClawHavoc + CVE-2026-25253 覆盖。配置参考、observe → enforce 切换、`sponsio refresh`、CI 接线见[完整指引](QUICKSTART.md)。

**用自然语言起草合约。** `sponsio validate "<一句话规则>"` 会把一条自然语言规则转成一份你能读回来的合约。把输出当作起点草稿，enforce 之前先自己 review、按需调整。确定性在于合约在运行时如何被*强制执行*，而不在于它如何被起草。

---

## 合约库

开箱即用的 **16 个合约 bundle**，按层级组织（always-on / per-tool / per-incident）。每个 bundle 是一个 YAML 包，由 Sponsio 的确定性模式组合而成。把它放进 `sponsio.yaml`，一行即可让 Agent 防护一类已知失败，无需逐合约编写。

```yaml
# sponsio.yaml: 一行式 bundle 引入
agents:
  my_agent:
    workspace: "/srv/my-bot"
    include:
      - sponsio:core/universal        # always-on
      - sponsio:capability/shell      # 若 Agent 会执行命令
      - sponsio:capability/filesystem # 若 Agent 会读写文件
```

查看[完整 bundle 参考](docs/reference/contract-lib.md)（共 16 个 bundle）或[底层 44 个模式](docs/reference/patterns.md)。想要面向你 Agent 类型的 bundle？这是目前杠杆率最高的贡献方式。带上事件 / CVE / 模式[开 issue](https://github.com/SponsioLabs/Sponsio/issues/new)。

---

## 贡献

欢迎提交补丁、问题反馈与新模式提案。从 [CONTRIBUTING.md](CONTRIBUTING.md) 开始。Sponsio 的威胁建模吸收了公开安全研究，例如 Simon Willison 的 ["Lethal Trifecta"](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) 塑造了我们的[多工具组合合约](sponsio/contracts/incident/mcp-composition.yaml)。有我们应当防御的威胁模型？[开 issue](https://github.com/SponsioLabs/Sponsio/issues/new)。

---

## 许可证

Apache 2.0（[LICENSE](LICENSE)）。

*阅读本仓库的 AI Agent：[`llms.txt`](llms.txt) 列出了规范文档路径；[`llms-full.txt`](llms-full.txt) 是完整上下文的拼接全量。*
