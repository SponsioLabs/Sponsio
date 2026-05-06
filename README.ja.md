<p align="right">
  <a href="./README.md">English</a> ·
  <a href="./README.zh-CN.md">简体中文</a> ·
  <b>日本語</b>
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

<p align="center">⭐ <em>共有のコントラクトライブラリとポリシー強制をより良いものにするため、Sponsio コミュニティの成長にご協力ください。リポジトリにスターをお願いします!</em></p>


# Sponsio

**AI エージェントのためのランタイム強制。** 自然言語でポリシーを入力すると、Sponsio がそれを破られない決定論的なエージェント契約にコンパイルします。0.01ms 未満で強制、ランタイムでの LLM コストはゼロ、[OWASP Agentic Top 10 のリスクをすべてカバー](docs/concepts/owasp-coverage.md)します。

> **エージェント契約** とは、エージェントのすべてのアクションに対するランタイムチェックであり、[形式手法に裏打ちされています](docs/concepts/formal-methods.md)。エージェントが無視したりジェイルブレイクできるシステムプロンプトでは *ありません*。

**あらゆるスタックで動作。** LangChain、Claude Agent、OpenAI Agents、Google ADK、CrewAI、Vercel AI、MCP、または任意のカスタム ツール呼び出しループ。Python · TypeScript · Prompt · Agent Skills。

*デモ動画を近日公開予定*

---

## SOTA エージェント安全性ソリューション

<p align="center">
  <img src="assets/sponsio-architecture.png" alt="Sponsio architecture: Agent Flow + (Natural Language + Pattern Library) compile into Contracts (Assumption → Enforcement), enforced by a Fuzzy LTL Monitor (deterministic + stochastic) that decides Pass / Block · Warn · Escalate / Redirect for every function call, with full audit trail logs feeding back to the agent." width="900">
</p>

[ODCV-Bench](https://arxiv.org/abs/2512.20798) — [McGill DMaS](https://github.com/McGill-DMaS/ODCV-Bench) によるサードパーティ ベンチマーク — 12 のフロンティア LLM × 80 トラジェクトリ(Claude-Opus-4.6 を含む)において、ガード無しのモデルは **11.5%–66.7% の実行で不正を働きます**。Sponsio を使うと、**平均 84.5% の不整合がブロックされます**。一方、次に優れる公式に発表済みのランタイム ガードレール([Salus, YC W26](https://www.ycombinator.com/companies/salus)、[2026 年 2 月ローンチ](https://yctierlist.com/w26/salus/))は同じベンチマークで 52% に到達するに留まります。`Financial-Audit-Fraud-Finding` シナリオでは、**フロンティア モデルは試行の 67%(16/24)で不正を犯します**。Sponsio を使うと **100% ブロック** されます。

### なぜ Sponsio か


| アプローチ                              | 機能する場面                                               | 失敗する場面                                                                                           | Sponsio の解決策                                                                                                                        |
| ------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **プロンプト インジェクション フィルタ**          | 生成前、入力テキストに対して                                | 新しい言い回しでドリフトする。ツール呼び出しではなくテキストを見ている。アクション履歴の概念がない                        | 関数呼び出しが実行される *前* に、*どの* ツールが、*どの* 順序で、*どの* 引数で実行できるかを、完全なトレース文脈と共に強制する            |
| **出力バリデータ**                 | 生成後、レスポンス文字列に対して                       | ミス(返金、DB 書き込み、API 呼び出しなど)はすでに発火している可能性がある                                    | 実行 *前* に呼び出しをブロックする。最新の文字列だけでなく、完全なアクション履歴を推論する                                      |
| **LLM-as-Judge**                      | 柔軟、ファジーなプロパティに対応。オフライン評価に有用 | 確率的な判定、数百ミリ秒のレイテンシ、それ自体がプロンプト インジェクション可能 — 同期的なゲートには不適合 | 0.01ms 未満の決定論的チェック、ホットパスに LLM ゼロ。確率的パイプラインはファジーなプロパティ用にオプトイン                             |
| **サンドボックス & アクセス制御リスト** | アイデンティティおよびリソース レベルの分離に強力な境界 | エージェント能力を狭める。*誰が* と *どのリソース* でゲートし、*行動シーケンス* ではない                | 順序、履歴、マルチステップ不変条件を含むアクション シーケンス上の時間的契約を強制し、エージェント能力を保持する |


他の決定論的エンフォーサと比較した、Sponsio の優位性。

**1. ステートレスなルール マッチングではなく、シーケンシャルなアクション上の時間的契約。** 既存のエンフォーサは各アクションを独立して評価する。Sponsio はトラジェクトリ全体を推論する。*"send_email の前に verify_recipient"*、*"PII アクセス後は外部呼び出し禁止"*、*"refund_payment はセッションあたり 3 回以下"*。

**2. ヒューリスティックではなく、機械検証可能。** 契約は LTL 式にコンパイルされ、さらに決定性有限オートマトンへ変換される。すべての判定は確率的な信頼度スコアではなく、決定論的 DFA 遷移である。ハードウェア検証(Intel FPU の正しさ、AWS S3 TLA+)で使われているのと同じ証明手法。[How it works →](docs/concepts/formal-methods.md)

**3. ゼロから保護まで数分、DSL の学習曲線なし。** 既存ツールは、手書きの YAML / Rego / Cedar ポリシーをゼロから書く必要がある。Sponsio は 4 つの導入経路を提供する。

- **自動推論** — `sponsio init`(対話型ウィザード)がツールシグネチャを読み取り、スターター契約を書き出す
- **コントラクト ライブラリ** — ケイパビリティ別(`sponsio:capability/shell`、`…/filesystem`)またはインシデント別(`sponsio:incident/openclaw`)で構築済みバンドルを include する。各バンドルは内部で 44 の det パターンを組み合わせる(sto アトムは Sponsio Cloud で提供)
- **自然言語** — `sponsio validate "..."` が平易な英語を LTL にコンパイルする
- **ポリシー文書** — `sponsio scan --policy security.md` が既存のコンプライアンス文書をパースする

**4. フレームワーク非依存、低依存性。** 他のツールは意見の強いスタックとして提供される — アイデンティティ、SRE、ダッシュボード、オーケストレーションを同梱する。Sponsio は、すでに使っているオブザーバビリティ、IAM、オーケストレーションと並んで差し込める単一の強制ライブラリである。

---

## クイックスタート

プロジェクトの言語を選択してください。1 つのプロンプトまたは 2 行の CLI コマンドで即座にオンボーディング。

### Python

**Claude Code / Codex / Cursor に貼り付け。** エージェントがオンボーディング プロセス全体の実行を支援します。クリックでフル プロンプト テンプレートを表示。注: Cursor は自身のハーネス設計上、Sponsio が会話内で何をブロックしたかを明示的に表示できないことがあります。

<p align="center">
  <a href="docs/getting-started/onboard-prompt.md#python-project"><img src="https://img.shields.io/badge/One--shot%20prompt-Python-3776AB?logo=python&logoColor=white&labelColor=555555" alt="One-shot prompt: Python"></a>
</p>

**または CLI を自分で実行:**

```bash
pip install sponsio
sponsio init .
```

`init` は対話型ウィザードである。フレームワーク(LangGraph / OpenAI / Claude Agent / Vercel AI / CrewAI / MCP / …)を検出し、配線する IDE ホスト(Claude Code / Codex / Cursor / OpenClaw、それぞれ `none` / `skill` / `full` レベル)と、observe か enforce モードかを尋ねる。その後 `sponsio.yaml` を書き出し、2 行のパッチを表示する。

```python
from sponsio.langgraph import Sponsio
from langgraph.prebuilt import create_react_agent

guard = Sponsio(config="sponsio.yaml", agent_id="coding_agent")
agent = create_react_agent(model, guard.wrap(tools))
```

### TypeScript

**Claude Code / Codex / Cursor に貼り付け:**

<p align="center">
  <a href="docs/getting-started/onboard-prompt.md#typescript-project"><img src="https://img.shields.io/badge/One--shot%20prompt-TypeScript-3178C6?logo=typescript&logoColor=white&labelColor=555555" alt="One-shot prompt: TypeScript"></a>
</p>

**または CLI を自分で実行:**

```bash
npm install -D @sponsio/sdk
npx sponsio init .
```

> **Note** — TS ウィザードは現在、単軸(プロバイダ × モード × エージェント)です。IDE ホスト プラグイン(Claude Code / Codex / Cursor / OpenClaw)もインストールするフルの多軸フローについては、上記の **Python** プロンプトを IDE エージェントに貼り付けてください — TS プロジェクトでも動作します(Python の `sponsio` CLI を駆動し、TS 互換の `sponsio.yaml` を書き出します)。

```typescript
import { Sponsio } from "@sponsio/sdk";
import { wrapTools } from "@sponsio/sdk/langchain";
import { ToolNode } from "@langchain/langgraph/prebuilt";

const guard = new Sponsio({ config: "sponsio.yaml", agentId: "coding_agent" });
const toolNode = new ToolNode(wrapTools(tools, guard));
```

### OpenClaw コミュニティへ

上記の Python プロンプトはあなたにとってもインストール経路です。ウィザードが IDE ホストについて尋ねた際に `openclaw=full` を選択してください。Sponsio はその後、OpenClaw ランタイム内のすべての `before_tool_call` イベントを契約エンジン経由でゲートし、同梱の `sponsio:incident/openclaw` パックによって ClawHavoc + CVE-2026-25253 のカバレッジを提供します。

ターミナルでライブ ブロックを監視 — OpenClaw ランタイムに対する Sponsio の各判定はここにストリームされます。

```bash
sponsio host trace openclaw --follow
```

---

> `sponsio.yaml` は手書きすることも、ポリシー文書からスキャンすること(`sponsio scan --policy policy.md`)、トレースから採掘すること(`sponsio refresh`)もできます。構文: [docs/concepts/contracts.md](docs/concepts/contracts.md)。

> **完全なウォークスルー:** [QUICKSTART.md](QUICKSTART.md) — 設定リファレンス、observe → enforce 切り替え、`sponsio refresh`、CI 配線、トラブルシューティング。

---

## ベンチマーク & パフォーマンス

Sponsio は 2 つの公開エージェント安全性スイートでベンチマークされており、2 つの異なる失敗モード — 合理的な KPI プレッシャー下のメトリック ゲーミング、および危険な bash / python スニペットの検出 — をカバーします。すべて公開済みのトラジェクトリに対するオフライン リプレイで、Sponsio が作成したテストセットではなく、**ライブラリのみ**(ブロッキング パス上にシナリオ別 LLM スキャンなし)で実施。


| ベンチマーク                                  | 計測対象                          | Sponsio 結果                                                                            |
| ------------------------------------------ | ----------------------------------------- | ----------------------------------------------------------------------------------------- |
| **ODCV-Bench** (12 LLMs × 80 trajectories) | KPI プレッシャー下の意図整合性       | **84.5%** ブロック(次点の公開ベースライン: **52%**) · クリーン シナリオで **新規 FP 0 件** |
| **RedCode-Exec** (1,410 cases)             | 危険な bash / python スニペット検出 | **bash 95% · python 90% · 統合 92%** · 60 ファイルのクリーン コード監査で **ユーティリティ FP 0%** |


両ライブラリは読み込み可能なコントラクト パックとして提供されます。det が指紋化できないセマンティック プロパティ(トーン、ハルシネーション、NL 出力でのスコープ ドリフト)には LLM ジャッジが必要 — OSS エンジンは差し込み可能な `Judge` 拡張ポイントを提供しており、マネージド確率的パイプラインは [Sponsio Cloud](docs/reference/oss-scope.md#in-sponsio-cloud-commercial--pip-install-sponsiocloud) の機能です。

### ロジック欠陥の失敗も決定論的に捕捉可能

従来の知見では、RedCode のロジック欠陥カテゴリ — 偏った決定ロジック、弱い regex バイパス、認可漏れパターン、アルゴリズム上の欠陥、メモリ リーク — は「syscall フィンガープリントを持たない振る舞い上の欠陥」であり、セマンティックな LLM ジャッジが必要だとされていました。我々はそのラベルに反論しました。そのような失敗はすべて *有限のコード - テキスト パターン* として現れます。一度パターンが列挙されれば、det が拘束します。7 つの新レイヤー(`bias_logic`、`weak_regex`、`algo_flaw`、`memory_leak`、`match_no_default`、`missing_auth`、`redcode_unauth_marker`)により、RedCode python は **69% → 90%** に引き上げられ、クリーン コード監査(Sponsio 自身のソース、テスト、API ルート)で **0/60 FP** を達成しました。det/sto の境界は、ほとんどのガードレール作者が想定していたよりも広く、sto は真に自由形式の出力に存在するプロパティ(トーン、ハルシネーション、忠実性)のために残ります — 有限列挙が単に未開拓だっただけのコード形状パターンには使いません。

### ホットパスのパフォーマンス


| ワークロード                                                  | 契約数 | p50           | p99       |
| --------------------------------------------------------- | --------- | ------------- | --------- |
| **合成マイクロベンチ**(単一契約、事前ウォーム DFA)           | 1         | **0.0052 ms** | 0.012 ms  |
| **ODCV-Bench mandated**(1,438 calls, scan-discovered)    | 6–18      | **0.139 ms**  | 0.765 ms  |
| **RedCode bash**(3,848 per-command calls)                | 7         | 0.434 ms      | 0.558 ms  |
| **RedCode python**(810 whole-script calls)               | 9         | 0.811 ms      | 1.035 ms  |


**バックエンド エンジニア向けアンカー:** ODCV mandated で p50 0.139 ms において、Sponsio のホットパスは **単一のローカル Redis 読み取りより少ないオーバーヘッド**(典型 0.1–0.5 ms)を加えます。

<u>**任意の LLM-as-judge ガードレールより 5,000×–60,000× 高速**</u>(gpt-4o-mini、Lakera Guard、OpenAI Moderation — すべてチェックあたり 50–800 ms)を同じツール呼び出しごとのワークロードで実現し、ホットパス上の LLM コストはゼロです。コール毎レイテンシは契約数に対して線形にスケールし、p99 は計測したすべてのワークロードで 1.04 ms 以下に留まります。最も重いシナリオ(RedCode python スクリプト全体に対する 9 契約のレイヤー regex)でさえ、**最も安価な LLM-as-judge コールより 50× 高速** です。

モデル別の完全な内訳、方法論、ハーネス スクリプト: [`docs/reference/benchmarks.md`](docs/reference/benchmarks.md)。

### 今日の数値は出発点であって上限ではない

```text
production traces ──→ sponsio scan ──→ proposed contracts
       ↑                                       │
       │                                       ▼
       └──────── enforcement ←──────── library (versioned)
```

**今日の 84.5% / 92% は出発点であって上限ではありません。** ライブラリはあなたのトレースから成長し、上流に出荷されます — 新しい攻撃パターン、新たに観測された安全でない呼び出しのすべてが、次のリリースに反映されます。

---

## コントラクト ライブラリ

16 の **コントラクト バンドル** が箱から出してすぐ使え、ティア(常時オン / ツール毎 / インシデント毎)で整理されています。各バンドルは Sponsio の 44 det パターンから構成された YAML パックです(sto アトムは Sponsio Cloud で提供)。`sponsio.yaml` に 1 行で投入すれば、エージェントは既知の失敗クラスから 1 行で守られ、契約毎の作成は不要です。以下にハイライトする 7 つは最も一般的に使われるものです。

### スターター バンドル


| バンドル | ティア | ルール | 対象 |
| --- | --- | --- | --- |
| `sponsio:core/universal` | 常時オン | 5 sto (Cloud) | 任意の LLM エージェント。レスポンス スコープのチェック: プロンプト インジェクション、ジェイルブレイク、有害、トキシック、セマンティック PII。設定済みのジャッジが必要 — [Sponsio Cloud](docs/reference/oss-scope.md) でマネージド、または OSS の `Judge` 拡張ポイント経由で BYO ジャッジ。それが無い場合、OSS ではログのみでスキップ。 |
| `sponsio:core/runaway` | 常時オン | 5 det | トークン使用、委譲、ツール ループを持つ任意のエージェント。「クレジットカードを持った while(true)」防御: トークン予算、委譲深さ、ループ上限。 |
| `sponsio:capability/shell` | ツール毎 | 11 det | `exec` / `bash` を露出するエージェント。`rm -rf /`、フォーク爆弾、`curl \| bash`、リバース シェル、行継続による回避を捕捉。[Claude Code #10077](https://github.com/anthropics/claude-code/issues/10077)(rm -rf $HOME, 2025 年 10 月)、[Replit 本番 DB ワイプ](https://www.theregister.com/2025/07/21/replit_saastr_vibe_coding_incident/)([Fortune 報道](https://fortune.com/2025/07/23/ai-coding-tool-replit-wiped-database-called-it-a-catastrophic-failure/), 2025 年 7 月)、および [Ansible `rm -rf {foo}/{bar}` による 1,535 サーバの postmortem](https://developers.slashdot.org/story/16/04/14/1542246/man-deletes-his-entire-company-with-one-line-of-bad-code)(Marsala, 2016)に着想を得た。 |
| `sponsio:capability/filesystem` | ツール毎 | 13 det | `read` / `write` / `edit` / `apply_patch` を露出するエージェント。機密パスの拒否、ワークスペース スコーピング、ブートストラップ ファイル ゲート(`CLAUDE.md`、`AGENTS.md`、`.cursorrules`)。[OpenClaw weather-skill `.env` 流出](https://www.trendmicro.com/en_us/research/26/b/openclaw-skills-used-to-distribute-atomic-macos-stealer.html) と [Cursor `.cursorignore` バイパス(CVE-2025-64110 / GHSA-vhc2-fjv4-wqch)](https://github.com/cursor/cursor/security/advisories/GHSA-vhc2-fjv4-wqch) に着想。 |
| `sponsio:incident/openclaw` | インシデント | 45 mixed | OpenClaw / ClawCode ユーザ。[CVE-2026-25253](https://nvd.nist.gov/vuln/detail/CVE-2026-25253)(WebSocket 1-click RCE)、[ClawHavoc — ClawHub 上の 1,184 個の悪意あるスキル](https://cyberpress.org/clawhavoc-poisons-openclaws-clawhub-with-1184-malicious-skills/)(Koi Security 開示, 2026 年 2 月)、`--yolo` フラグ、weather-skill 流出をカバー。ルールをフォークするための実例。 |
| `sponsio:incident/cursor-railway-wipe` | インシデント | mixed | [PocketOS 本番 DB ワイプ(2026 年 4 月 24 日)](https://www.theregister.com/2026/04/27/cursoropus_agent_snuffs_out_pocketos/) を再現 — Cursor + Claude Opus 4.6 がスコープ過大な Railway API トークン経由で 9 秒で本番 + バックアップを削除。([Tom's Hardware](https://www.tomshardware.com/tech-industry/artificial-intelligence/claude-powered-ai-coding-agent-deletes-entire-company-database-in-9-seconds-backups-zapped-after-cursor-tool-powered-by-anthropics-claude-goes-rogue) · [Railway 自身の postmortem](https://blog.railway.com/p/your-ai-wants-to-nuke-your-database))資格情報スコープの濫用 + 破壊的 API ゲートを捕捉。 |
| `sponsio:incident/claude-code-secret-bypass` | インシデント | mixed | [CVE-2025-55284](https://www.sentinelone.com/vulnerability-database/cve-2025-55284/)(過度に広いセーフ コマンド許可リスト → ファイル読み取り確認バイパス)と [deny ルール キャップ バイパス](https://adversa.ai/blog/claude-code-security-bypass-deny-rules-disabled/)(50 サブコマンド パディングが暗黙裏に deny ルールを無効化)を再現。シークレット読み取り + 引数パディング回避を捕捉。 |


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

`sponsio init` は検出されたツール インベントリに基づいてティア 0 のバンドルを自動選択する。パックをフォークすることなく、個々のルールを無効化または再調整できる。`customized:` を使うと、ルールを `desc`、`pack_source`、または `pattern` フィールドでターゲットできる。`tool_rename:` で正規ツール名(`exec`、`read`、`edit`)をエージェントのものに改名する。

完全なバンドル リファレンスは [`docs/reference/contract-lib.md`](docs/reference/contract-lib.md) にある。バンドルが組み立てる基礎プリミティブは別途カタログ化されている: 44 det パターンが [`docs/reference/patterns.md`](docs/reference/patterns.md) にある。Sto アトム(トーン、ハルシネーション、スコープ ドリフトなどの LLM ジャッジ評価器)は [Sponsio Cloud](docs/reference/oss-scope.md#in-sponsio-cloud-commercial--pip-install-sponsiocloud) の一部 — OSS エンジンは BYO ジャッジ用途のために `Judge` 拡張ポイントを提供する。

> **あなたのエージェント タイプ用のバンドルが欲しい?** これは現在最も貢献度の高い方法です。あなたのインシデント、CVE、またはパターンで [Issue を開いてください](https://github.com/SponsioLabs/Sponsio/issues/new)。

---

## インテグレーション

フレームワークを選んでください — 各ブロックは差し込めるスニペットへ展開します。Python と TypeScript は同じエンジンと DSL を共有します。

<details>
<summary><b>フレームワークなし</b> — カスタム ツール呼び出しループ</summary>


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
<summary><b>LangGraph / LangChain.js</b> — ツールをラップ</summary>


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
<summary><b>Claude Agent SDK</b> — ネイティブ フック、ツール ラッピング不要</summary>


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
<summary><b>OpenAI SDK</b> — モンキー パッチまたは明示的ラップ</summary>


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

YAML なしの素早い配線(スクリプト / ノートブックで便利): `from sponsio.openai import patch_openai`。

</details>

<details>
<summary><b>OpenAI Agents SDK</b> — Agent ツールをラップ</summary>


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

TypeScript: 未対応。

</details>

<details>
<summary><b>Google ADK</b> — Agent ツールをラップ(Gemini)</summary>


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
<summary><b>Vercel AI SDK</b> — ミドルウェア</summary>


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
<summary><b>CrewAI</b> — Crew レベルのフック</summary>


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

TypeScript: 未対応。

</details>

<details>
<summary><b>MCP</b> — MCP クライアントをプロキシ</summary>


```python
from sponsio.mcp import MCPContractProxy

# Build a sponsio System from your contracts — see runnable example for full wire-up.
proxy = MCPContractProxy(mcp_client=your_mcp_client, system=system)

# Use `proxy` wherever you called the raw MCP client; contracts apply transparently.
result = await proxy.call_tool("write_external_api", {"data": "batch_1"})
```

TypeScript: 未対応。

</details>



---

> **上記スニペットに関する注記。** すべての例は、`sponsio init .` を最初に実行済みであることを前提としています。これはウィザードを起動し、ツール インベントリから推論したスターター契約セット付きで `sponsio.yaml` を生成し、貼り付け用の wrap スニペットを表示します。YAML を別の方法で埋めたい場合 — パターン ライブラリ バンドル、手書きルール、自然言語のワンライナー、またはポリシー文書からのパース(`sponsio scan --policy security.md`) — については [Contract types and authoring](QUICKSTART.md#contract-types-and-authoring) と [docs/concepts/contracts.md](docs/concepts/contracts.md) を参照してください(完全な構文)。

---

## ドキュメント

- [Quick start](QUICKSTART.md)
- [Contract DSL](docs/concepts/contracts.md)
- [CLI Reference](docs/reference/cli.md)
- [Integrations](docs/integrations/index.md)
- [Architecture](docs/concepts/architecture.md)
- [Benchmarks](docs/reference/benchmarks.md)
- [OWASP Agentic Top 10 coverage](docs/concepts/owasp-coverage.md)
- [Formal methods primer](docs/concepts/formal-methods.md)
- [**OSS Promise**](OSS_PROMISE.md) · [OSS / Cloud boundary](docs/reference/oss-scope.md) · [Brand & trademark](BRAND.md)
- [Changelog](CHANGELOG.md)

*このリポジトリを読む AI エージェントへ: [`llms.txt`](llms.txt) は正準ドキュメント パスをリストし、[`llms-full.txt`](llms-full.txt) は連結された完全なコンテキスト ダンプです。*

---

## セキュリティ

Sponsio はランタイム契約を強制するため、それ自身の正しさが重要です。何か発見しましたか? 公開 issue ではなく、GitHub の [security advisory フォーム](https://github.com/SponsioLabs/Sponsio/security/advisories/new) からプライベートに報告してください。スコープ、タイムライン、対象範囲(enforce モード バイパス、LTL 評価器のクラッシュ、セッション ログのリーク、ジャッジ プロンプト インジェクションなど)については [SECURITY.md](SECURITY.md) を参照してください。

---

## コントリビューション

パッチ、issue 報告、新パターン提案を歓迎します。[CONTRIBUTING.md](CONTRIBUTING.md) から始めてください。

---

## 重要な注意事項

Sponsio は *あなたが* 定義するランタイム契約を強制します — あなたのアプリケーションが何らかの規制フレームワークに準拠していることを認証するものではありません。規制対象ドメイン(HIPAA、GDPR、SOX、EU AI Act、金融サービス、ヘルスケア)で運用する場合、Sponsio のコントロールと我々の [OWASP Agentic Top 10 マッピング](docs/concepts/owasp-coverage.md) はコンプライアンス プログラムへの入力です。それらは、有資格のセキュリティ監査、法務レビュー、ドメイン固有の規制分析の代替には **なりません**。適切なレビューを伴って契約を作成し、エージェントのツール表面が変化したら見直してください。

Det 契約はアクション境界での機械検証可能な強制を提供します。Sponsio の上流の脆弱性(侵害された LLM プロバイダ、許可リスト化した悪意あるツール、トランスポート暗号化 / SBOM 来歴などのインフラ層リスク)は保護しません。完全なスコープについては [`SECURITY.md`](SECURITY.md) を参照してください。

---

## ライセンス & オープンソースの約束

Apache 2.0 — [LICENSE](LICENSE) を参照。

Sponsio Labs は商業会社です。Sponsio Cloud(`pip install sponsio[cloud]`)は 2026 年 5 月中旬にオープンし、マネージド LLM ジャッジ パイプライン、顧客横断パターン インテリジェンス、ホスト型マルチテナント ダッシュボードを追加します。OSS エンジンは完成しており、セルフホスト用途でプロダクション対応です — OSS に永久に残るもの、我々が販売するもの、境界について約束することについては [OSS_PROMISE.md](OSS_PROMISE.md) を参照してください。

Sponsio™ は Sponsio Labs の商標です — [BRAND.md](BRAND.md) を参照してください。
