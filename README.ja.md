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
</p>

<p align="center">
  <a href="https://x.com/sponsiolabs"><img src="https://img.shields.io/badge/Follow%20on%20X-000000?logo=x&logoColor=white" alt="Follow on X"></a>
  <a href="https://www.linkedin.com/company/sponsio-labs/"><img src="https://img.shields.io/badge/Follow%20on%20LinkedIn-0A66C2?logo=linkedin&logoColor=white" alt="Follow on LinkedIn"></a>
  <a href="https://discord.gg/s8TfPnZWUm"><img src="https://img.shields.io/badge/Join%20our%20Discord-5865F2?logo=discord&logoColor=white" alt="Join our Discord"></a>
</p>


# Sponsio

<p align="center">
  <img src="assets/sponsio-comparison-freeze.png" alt="コードフリーズ宣言下の同一コーディングエージェント。Sponsio なし：本番 users テーブルを削除し、捏造した行で埋め戻し、被害を隠す状態レポートを提出。Sponsio あり：最初の破壊的 SQL を実行前にブロック：35 チェック、100% 決定論的、LLM 呼び出し 0 回、p50 13µs。" width="900">
</p>

**AI エージェントのためのランタイム強制。** Sponsio はエージェントのすべてのアクションを、決定論的な純コードの契約に照らしてチェックします。0.01 ms 未満で強制し、ランタイムでの LLM コストはゼロ。LangChain、Claude Agent、OpenAI Agents、Google ADK、CrewAI、Vercel AI、MCP、または任意のカスタム ツール呼び出しループに対応（Python / TypeScript）。

> **エージェント契約** とは、エージェントのすべてのアクションでチェックされるランタイムルールであり、[形式手法に裏打ちされています](docs/concepts/formal-methods.md)。

---

## Sponsio の仕組み

<p align="center">
  <img src="assets/sponsio-architecture.png" alt="Sponsio architecture: Agent Flow + (Natural Language + Pattern Library) compile into Contracts (Assumption → Enforcement), enforced by a Fuzzy LTL Monitor (deterministic + stochastic) that decides Pass / Block · Warn · Escalate / Redirect for every function call, with full audit trail logs feeding back to the agent." width="900">
</p>

[ODCV-Bench](https://github.com/McGill-DMaS/ODCV-Bench)（12 のフロンティア LLM × 80 トラジェクトリ）において、ガード無しのモデルは 11.5%–66.7% の実行で不正を働きます。**Sponsio を使うと平均 95.6% の不整合を回避、36 の高リスクシナリオのうち 24 が 100% に到達**。`Financial-Audit-Fraud-Finding` シナリオでは、フロンティア モデルが 16/24 で不正を犯すところを、**Sponsio は 18/19 をブロック**。RedCode-Exec（1,410 ケース）では、60 ファイルのクリーン コード監査にわたり総合ブロック率 **92%**（bash 95% · python 90%）を達成。

ロジックチェッカーは契約あたり p50 **0.139 ms**、**あらゆる LLM-as-judge ガードレールよりも 5,000×–60,000× 高速**（チェックあたり 50–800 ms）、ホットパスでの LLM コストはゼロ。p99 は測定されたすべてのワークロードで 1.04 ms 以内に収まります。

[完全なベンチマーク方法論とモデル別の内訳](docs/reference/benchmarks.md)、[プロンプト フィルタ / 出力バリデータ / LLM-as-judge / サンドボックスとの比較](docs/why.md)、または[アーキテクチャ詳細](docs/concepts/architecture.md)と[形式手法入門](docs/concepts/formal-methods.md)を参照。

---

## v0.2 で新しくなったこと（alpha）

`pip install --pre sponsio==0.2.0a0`

v0.1 では契約が発火した時の対応が 1 つだけでした。呼び出しをブロックして、あとはエージェントに任せる。v0.2 では **3 つの「ソフトランディング」** を導入し、エージェントが拒否に弾かれて止まることなく、タスクを前に進められるようにしました。

```python
from sponsio import contract
from sponsio.langgraph import Sponsio
from sponsio.patterns import redirect_to_safe

guard = Sponsio(
    tool_policy={"default": "deny", "approved": ["search", "log_refund_request"]},
    contracts=[
        contract("全ての返金は人手レビューを経由する")
        .assume("called `issue_refund`")
        .guarantees(redirect_to_safe("issue_refund", "log_refund_request")),
    ],
)
```

- **`tool_policy` のデフォルト拒否**。フレームワークに新しいツールを追加しても、自動で信頼はされません。エージェントは `approved:` リストにあるツールしか呼べず、それ以外は全て拒否されます。`enforcement: proactive` モードでは、拒否対象のツールはモデルのプロンプトから完全に取り除かれます（LangGraph / CrewAI / OpenAI Agents SDK / Google ADK）。
- **`redirect_to_safe(unsafe, safe)`**。禁止された呼び出しを、事前宣言した安全な代替に置き換えます。モデルが `issue_refund` を呼ぶと、Sponsio が `log_refund_request` を代わりに実行し、モデルは「チケットが起票された」という結果を読んで適応します。Gemini 2.5 Flash で end-to-end 検証済み: モデルは最終応答を「返金リクエストを提出し、レビュー中です」と返し、「$5000 を返金しました」とは言いません。実際に実行されたツールの結果に合わせて誠実に表現を調整します。
- **`filter_tools(candidates)`**。現在のトレースを踏まえて、今この瞬間に呼び出しが合法なツールの部分集合を返す純粋な探査 API です。カスタムループでモデル呼び出しの前に挟むと、時間順依存（`must_precede`）でまだ呼べないツールを事前に削除でき、トークンの無駄を省けます。
- **`EscalateToHuman(notify=[...])`** が通知用 callable（Slack webhook、オンコール ページャー、メール）を受け付けるようになりました。1 つの通知が壊れても、エージェントループは落ちず、他の通知も止まりません。

ユーザー向けの完全なピッチは [v0.2 リリースノート](docs/release-notes/v0.2.0a0.md)、実行可能な Python サンプルは [v0.2 ケーススタディ](examples/integrations/python/)、または `python scripts/verify_v0_2.py` を走らせれば、インストール済みのフレームワーク アダプタが v0.2 と互換かを smoke test できます。

---

## クイックスタート

1 つのプロンプトまたは 2 行の CLI コマンドで即座にオンボーディング。

**Claude Code / Codex / Cursor に貼り付け。** エージェントがオンボーディング全体を支援します：

<p align="center">
  <a href="docs/getting-started/onboard-prompt.md#python-project"><img src="https://img.shields.io/badge/One--shot%20prompt-Python-3776AB?logo=python&logoColor=white&labelColor=555555" alt="One-shot prompt: Python"></a>
  &nbsp;
  <a href="docs/getting-started/onboard-prompt.md#typescript-project"><img src="https://img.shields.io/badge/One--shot%20prompt-TypeScript-3178C6?logo=typescript&logoColor=white&labelColor=555555" alt="One-shot prompt: TypeScript"></a>
</p>

**または CLI を自分で実行:**

```bash
pip install sponsio        # または: npm install -D @sponsio/sdk
sponsio init .             # 対話型ウィザード: フレームワーク・IDE ホスト・observe vs enforce を検出
```

ウィザードがフレームワークを自動検出し、対応するラップ スニペットを表示します。手動配線は [docs/integrations/](docs/integrations/index.md) を参照。[OpenClaw ユーザー](docs/integrations/openclaw.md)は ClawHavoc + CVE-2026-25253 のカバレッジを最初から利用できます。設定リファレンス、observe → enforce 切替、`sponsio refresh`、CI 配線は[完全ガイド](QUICKSTART.md)を参照。

**自然言語から契約を下書きする。** `sponsio validate "<平易な文のルール>"` は、自然言語のルールを読み返せる契約に変換します。出力はあくまで下書きとして扱い、enforce する前に自分でレビューして調整してください。決定論的なのは契約がランタイムでどう*強制される*かであって、どう下書きされるかではありません。

---

## コントラクト ライブラリ

**16 のコントラクト バンドル** が組み込みで提供され、ティア別（always-on / per-tool / per-incident）に整理されています。各バンドルは Sponsio の決定論的パターンから組み合わされた YAML パックです。`sponsio.yaml` に 1 行追加するだけで、エージェントを既知の失敗クラスから守れます。契約を個別に書く必要はありません。

```yaml
# sponsio.yaml: 1 行式バンドル include
agents:
  my_agent:
    workspace: "/srv/my-bot"
    include:
      - sponsio:core/universal        # always-on
      - sponsio:capability/shell      # エージェントがコマンドを実行する場合
      - sponsio:capability/filesystem # エージェントがファイルを操作する場合
```

[完全なバンドル リファレンス](docs/reference/contract-lib.md)（16 バンドル）または[基盤となる 44 パターン](docs/reference/patterns.md)を参照。あなたのエージェント タイプ向けのバンドルが欲しい? これは現時点で最もレバレッジの高い貢献方法です。インシデント / CVE / パターンを添えて [issue を開いてください](https://github.com/SponsioLabs/Sponsio/issues/new)。

---

## コントリビューション

パッチ、issue 報告、新しいパターン提案を歓迎します。[CONTRIBUTING.md](CONTRIBUTING.md) から始めてください。Sponsio の脅威モデルは公開セキュリティ研究を取り入れており、例えば Simon Willison の ["Lethal Trifecta"](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) が我々の[マルチツール構成契約](sponsio/contracts/incident/mcp-composition.yaml)を形作っています。我々が防御すべき脅威モデルがありますか? [issue を開いてください](https://github.com/SponsioLabs/Sponsio/issues/new)。

---

## ライセンス

Apache 2.0（[LICENSE](LICENSE)）。

*このリポジトリを読む AI エージェントへ: [`llms.txt`](llms.txt) は正規ドキュメント パスをリストし、[`llms-full.txt`](llms-full.txt) は完全な文脈の連結ダンプです。*
