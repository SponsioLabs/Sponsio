---
title: OpenClaw integration
description: Gate every OpenClaw before_tool_call event through Sponsio's contract engine, with bundled ClawHavoc + CVE-2026-25253 coverage.
---

# OpenClaw integration

Sponsio plugs into the OpenClaw runtime as a host-level gate. Every `before_tool_call` event is checked against your active contracts before the tool fires, and every decision is streamed to your terminal.

## Install

Use the standard Python onboarding path. Paste the [Python one-shot prompt](../getting-started/onboard-prompt.md#python-project) into Claude Code / Codex / Cursor, and when the wizard asks about IDE hosts, pick `openclaw=full`. The wizard installs the `sponsio:incident/openclaw` bundle and wires up the host gate.

Or run the CLI yourself:

```bash
pip install sponsio
sponsio init .
# When prompted for IDE hosts, choose openclaw=full
```

## What you get

The bundled `sponsio:incident/openclaw` pack covers:

- **[CVE-2026-25253](https://nvd.nist.gov/vuln/detail/CVE-2026-25253)**: WebSocket 1-click remote code execution.
- **[ClawHavoc](https://cyberpress.org/clawhavoc-poisons-openclaws-clawhub-with-1184-malicious-skills/)**: 1,184 malicious skills on ClawHub (Koi Security disclosure, Feb 2026).
- The `--yolo` flag.
- The weather-skill `.env` exfiltration pattern ([Trend Micro write-up](https://www.trendmicro.com/en_us/research/26/b/openclaw-skills-used-to-distribute-atomic-macos-stealer.html)).

45 mixed det/sto rules in total. See [`docs/reference/contract-lib.md`](../reference/contract-lib.md#sponsioincidentopenclaw) for the full rule list.

## Watch live blocks

Every Sponsio decision against your OpenClaw runtime streams here:

```bash
sponsio host trace openclaw --follow
```

Output format and filtering options: [`docs/reference/observability.md`](../reference/observability.md).

## Social-account actions

If an OpenClaw plugin can act through a public social account, put an explicit
approval boundary in front of the live execution tool. The
[`openclaw-social-account-actions`](../../examples/integrations/openclaw-social-account-actions)
example shows a TweetClaw recipe that leaves endpoint discovery available
through `explore`, requires `confirm_reconfirmed` before `tweetclaw` executes,
and caps live X/Twitter API calls per session.

## Fork the pack

The OpenClaw bundle is also a worked example to fork from when authoring your own incident packs. Source: [`sponsio/contracts/incident/openclaw.yaml`](https://github.com/SponsioLabs/Sponsio/blob/main/sponsio/contracts/incident/openclaw.yaml).

---

← [Back to README](../../README.md) · [Other integrations](index.md)
