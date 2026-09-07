#!/usr/bin/env python3
"""Give both runtimes the same book and the same trace, and diff them.

``tests/cross_language/scenarios.json`` holds nine hand-written cases.
Nine is enough to catch a rewrite and not enough to catch a drift: the
argument inversion in the TypeScript no_reversal parser lived through
that file for months, and so did eleven patterns the TypeScript parser
matched and then dropped.

This generates cases instead. Each is a small rulebook and a random
trace of tool calls; each runtime is asked, step by step, whether the
call is allowed. A divergence anywhere means one of them is enforcing a
book the other is not, which is the only thing that has to be true for
two SDKs of one product.

    python scripts/fuzz_cross_language.py --cases 400 --seed 7

Deterministic: the seed is printed and a failing case can be replayed
with ``--seed``. Exits non-zero on the first divergence class found.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

TOOLS = [
    "check_policy",
    "issue_refund",
    "verify_identity",
    "share_record",
    "deploy_service",
    "run_backup",
    "send_email",
    "read_database",
    "approve_loan",
    "wire_transfer",
    "delete_file",
    "write_audit",
]

# Rule shapes both runtimes claim to parse. The generator picks a shape
# and fills it, so the corpus covers phrasings nobody wrote down.
SHAPES = [
    ("tool `{a}` must precede `{b}`", 2, 0),
    ("`{a}` always followed by `{b}`", 2, 0),
    ("never `{a}` after `{b}`", 2, 0),
    ("tool `{a}` must not follow `{b}`", 2, 0),
    ("`{a}` is forbidden after `{b}`", 2, 0),
    ("tool `{a}` at most {n} times", 1, 1),
    ("`{a}` at most once", 1, 0),
    ("tools `{a}` and `{b}` are mutually exclusive", 2, 0),
    ("never call `{a}` and `{b}` together", 2, 0),
    ("cooldown of {n} steps between `{a}`s", 1, 1),
    ("`{a}` requires confirmation", 1, 0),
    ("tool `{a}` requires permission `{b}`", 2, 0),
    ("after `{a}`, `{b}` must occur within {n} steps", 2, 1),
    ("backup `{a}` before `{b}`", 2, 0),
    ("dry-run `{a}` before `{b}`", 2, 0),
    ("`{a}` must be followed by audit step `{b}`", 2, 0),
    ("at most {n} retries of `{a}`", 1, 1),
    ("segregation of duty between `{a}` and `{b}`", 2, 0),
]


def make_case(rng: random.Random) -> dict:
    names = rng.sample(TOOLS, k=rng.randint(2, 5))
    rules = []
    for _ in range(rng.randint(1, 3)):
        shape, arity, needs_n = rng.choice(SHAPES)
        picked = rng.sample(names, k=min(arity, len(names)))
        while len(picked) < 2:
            picked.append(picked[0])
        text = shape.format(a=picked[0], b=picked[1], n=rng.randint(1, 4))
        if text not in rules:
            rules.append(text)
    trace = [
        {"tool": rng.choice(names), "args": {"i": i}} for i in range(rng.randint(3, 12))
    ]
    return {"contracts": rules, "steps": trace}


def python_verdicts(case: dict) -> list[dict] | None:
    """Ask the Python guard, step by step. None when it will not arm.

    The comparison is ``allowed`` plus which patterns fired, not the
    message: the two runtimes word a violation differently on purpose and
    a diff on prose would drown the diff that matters.
    """
    import contextlib
    import io

    import sponsio

    try:
        # Constructing a guard prints the armed-contracts banner, and a
        # few hundred of those bury the result.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            guard = sponsio.Sponsio(
                agent_id="fuzz",
                contracts=case["contracts"],
                mode="enforce",
                verbose=False,
            )
    except Exception:  # noqa: BLE001 - refusing to arm is an answer, not a crash
        return None
    out = []
    for step in case["steps"]:
        try:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                r = guard.guard_before(step["tool"], step["args"])
        except Exception as exc:  # noqa: BLE001 - a raising check is a divergence
            out.append({"error": type(exc).__name__})
            continue
        out.append(
            {
                "allowed": bool(r.allowed),
                "rules": sorted(
                    {
                        str(getattr(v, "rule_id", "") or "")
                        for v in (r.det_violations or [])
                    }
                ),
            }
        )
    return out


TS_RUNNER = r"""
const { Sponsio } = require(process.argv[2]);
const cases = JSON.parse(require("fs").readFileSync(process.argv[3], "utf8"));
const out = [];
for (const c of cases) {
  let guard;
  try {
    guard = new Sponsio({ agentId: "fuzz", contracts: c.contracts, mode: "enforce" });
  } catch (e) {
    out.push(null);
    continue;
  }
  const rows = [];
  for (const s of c.steps) {
    try {
      const r = guard.guardBefore(s.tool, s.args);
      rows.push({
        allowed: Boolean(r.allowed),
        rules: [...new Set((r.detViolations || []).map((v) => v.ruleId || ""))].sort(),
      });
    } catch (e) {
      rows.push({ error: e && e.constructor ? e.constructor.name : "Error" });
    }
  }
  out.push(rows);
}
process.stdout.write(JSON.stringify(out));
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=300)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--show", type=int, default=5, help="how many divergences to print")
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(2**31)
    print(f"seed {seed}  cases {args.cases}")
    rng = random.Random(seed)

    cases = [make_case(rng) for _ in range(args.cases)]
    py = [python_verdicts(c) for c in cases]

    dist = ROOT / "ts" / "packages" / "sdk" / "dist" / "index.js"
    if not dist.exists():
        print(f"build the TS SDK first: {dist} is missing", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        payload = pathlib.Path(tmp) / "cases.json"
        payload.write_text(json.dumps(cases))
        runner = pathlib.Path(tmp) / "run.cjs"
        runner.write_text(TS_RUNNER)
        proc = subprocess.run(
            ["node", str(runner), str(dist), str(payload)],
            capture_output=True,
            text=True,
            env={**os.environ, "SPONSIO_QUIET": "1"},
        )
    if proc.returncode != 0:
        print(proc.stderr[-2000:], file=sys.stderr)
        return 2
    ts = json.loads(proc.stdout)

    armed_neither = armed_one = agreed = 0
    diffs: list[str] = []
    for case, a, b in zip(cases, py, ts):
        if a is None and b is None:
            armed_neither += 1
            continue
        if (a is None) != (b is None):
            armed_one += 1
            diffs.append(
                f"one runtime armed and the other refused: {case['contracts']}"
            )
            continue
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                diffs.append(
                    f"{case['contracts']}\n"
                    f"    trace {[s['tool'] for s in case['steps']]}\n"
                    f"    step {i} ({case['steps'][i]['tool']}): py={x} ts={y}"
                )
                break
        else:
            agreed += 1

    print(f"agreed          {agreed}")
    print(f"neither armed   {armed_neither}")
    print(f"diverged        {len(diffs)}")
    if diffs:
        print()
        for d in diffs[: args.show]:
            print(f"  {d}")
        if len(diffs) > args.show:
            print(f"  ... and {len(diffs) - args.show} more")
        print(f"\nreplay with: python scripts/fuzz_cross_language.py --seed {seed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
