# OpenClaw Social Account Actions

This recipe shows how to gate a public social-account plugin from OpenClaw with
Sponsio before the tool performs live X/Twitter API work. It uses TweetClaw as a
concrete example because the plugin exposes a safe `explore` tool for endpoint
review and a `tweetclaw` tool for configured actions such as search tweets,
search tweet replies, post tweets, post tweet replies, direct messages, media
workflows, monitor events, webhooks, follower export, user lookup, and giveaway
draws.

Use this recipe when an OpenClaw agent may act through a real social account and
you want an explicit operator approval boundary before any live API call.

## How It Works

The sample `sponsio.yaml` keeps deterministic OpenClaw host protections in
place and adds three social-account checks:

- `explore` stays available so the agent can inspect the TweetClaw contract and
  plan from available endpoints.
- Every `tweetclaw` execution requires a fresh `confirm_reconfirmed` marker.
- Live TweetClaw calls are capped per session.

The confirmation marker is the same marker used by Sponsio's OpenClaw host
rules. Your OpenClaw/Sponsio integration must emit `confirm_reconfirmed` when
the operator approves the action in the host UI. Add
`sponsio:incident/openclaw` separately when your build supports the full mixed
deterministic and stochastic OpenClaw incident pack.

## Use The Recipe

Copy the sample into your project and adapt the agent id or workspace path:

```bash
cp examples/integrations/openclaw-social-account-actions/sponsio.yaml sponsio.yaml
sponsio validate sponsio.yaml
```

Start in observe mode, review the report, then switch to enforce mode once your
approval marker is wired correctly.
