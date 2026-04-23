"""Tests for the D / F / G quality fixes called out in the original
pack review.

Why these matter individually:

* **D** — `arg_blacklist` regexes that match too broadly cause
  day-1 false positives, which is the fastest way to get an entire
  contract pack disabled.  Pin the *non-matches* (the safe paths
  that must NOT trip the rule) so future regex tweaks don't
  silently re-broaden the scope.
* **F** — the strict 1:1 confirm-to-exec ratio breaks legitimate
  batch-approval workflows.  Pin the doc-comment that points users
  at the override + replacement pattern, since that's the only
  guidance preventing them from disabling the whole pack.
* **G** — stochastic ``beta`` values without rationale comments
  prompt "why this number?" questions that erode trust in the
  pack's defaults.  Pin that the rationale text exists.

Plus a smoke check: every pack must still load cleanly after these
changes — quality fixes that broke loading would be net-negative.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sponsio.config import load_config

PACKS_DIR = Path(__file__).parent.parent / "sponsio" / "contracts"


# ---------------------------------------------------------------------------
# D — Tightened regexes in capability/filesystem.yaml § 1
# ---------------------------------------------------------------------------


def _read_pack(name: str) -> str:
    return (PACKS_DIR / name).read_text()


# The exact pattern shipped in filesystem.yaml § 1 for `.env` files
# after the D fix.  Pin literal so a regression in the YAML surfaces
# at this test, not at first false-positive in the field.
_DOTENV_REGEX = r"(^|/)\.env(\.(?!example$|sample$|template$|dist$)[\w.-]+)?$"
_CRONTAB_WRITE_REGEX = r"(^|/)crontab$"


class TestDotenvRegex:
    """Pin the .env regex doesn't match the conventional non-secret
    variants while still catching real secret files."""

    @pytest.mark.parametrize(
        "should_block",
        [
            ".env",
            "/proj/.env",
            ".env.local",
            ".env.production",
            ".env.staging",
            "secrets/.env.dev",
        ],
    )
    def test_secret_dotenv_files_still_blocked(self, should_block):
        assert re.search(_DOTENV_REGEX, should_block), (
            f"{should_block!r} should match the .env block regex but didn't"
        )

    @pytest.mark.parametrize(
        "should_pass",
        [
            ".env.example",  # the canonical false-positive
            "/proj/.env.sample",  # alt naming
            "configs/.env.template",
            ".env.dist",  # rails-style
            ".envrc",  # direnv config — different file, NOT a secret
            ".envoy/config",  # Envoy proxy dir — same prefix, unrelated
        ],
    )
    def test_safe_dotenv_variants_not_blocked(self, should_pass):
        assert not re.search(_DOTENV_REGEX, should_pass), (
            f"{should_pass!r} should NOT match the .env block regex (false positive)"
        )

    def test_regex_is_present_in_shipped_pack(self):
        """Belt-and-suspenders: assert the actual YAML carries the
        tightened pattern, not just that the test's local copy is
        right.  Catches drift if the YAML gets reverted."""
        text = _read_pack("capability/filesystem.yaml")
        assert "(?!example$|sample$|template$|dist$)" in text, (
            "filesystem.yaml lost the safe-variant carve-out for .env files"
        )


class TestCrontabRegex:
    """`(^|/)crontab` (no end-anchor) matches `mycrontab.txt`,
    `crontab-backups/`, etc.  The fixed version is end-anchored."""

    @pytest.mark.parametrize(
        "should_block", ["crontab", "/etc/crontab", "/var/spool/cron/crontab"]
    )
    def test_crontab_paths_still_blocked(self, should_block):
        assert re.search(_CRONTAB_WRITE_REGEX, should_block), (
            f"{should_block!r} should match the crontab block regex"
        )

    @pytest.mark.parametrize(
        "should_pass",
        [
            "mycrontab.txt",  # filename containing crontab
            "crontab-tools/Makefile",  # dir whose name starts with crontab
            "src/crontab_helpers.py",
        ],
    )
    def test_crontab_substrings_not_blocked(self, should_pass):
        assert not re.search(_CRONTAB_WRITE_REGEX, should_pass), (
            f"{should_pass!r} should NOT match the crontab block regex"
        )

    def test_end_anchor_present_in_shipped_pack(self):
        text = _read_pack("capability/filesystem.yaml")
        # The write/edit path lists; not applied to read because read
        # never blocks crontab (correct — reading a system crontab
        # isn't sensitive on its own).
        assert '"(^|/)crontab$"' in text, (
            "filesystem.yaml writes/edits lost the crontab end-anchor"
        )


# ---------------------------------------------------------------------------
# F — Batch-approval doc comment in capability/shell.yaml § 4
# ---------------------------------------------------------------------------


class TestBatchApprovalDocumented:
    """The strict 1:1 confirm/exec rule is correct for one-by-one
    approval flows but breaks CI/batch flows.  We can't easily
    encode a batch ratio in current LTL, so we document the
    workaround inline as the next-best UX."""

    def test_batch_approval_workaround_documented(self):
        text = _read_pack("capability/shell.yaml")
        # The doc must point to overrides: as the disable mechanism
        # so users don't reach for "just delete the rule" or fork
        # the pack.
        assert "overrides:" in text, (
            "shell.yaml § 4 missing the overrides: workaround docs"
        )
        assert "batch" in text.lower(), (
            "shell.yaml § 4 missing 'batch' rationale — users shouldn't have "
            "to guess why the rule fires on legitimate CI flows"
        )
        # Mentions a sensible batch marker name so users have a
        # starting point, not a blank slate.
        assert "confirm_batch" in text, (
            "shell.yaml § 4 missing the example batch-marker name "
            "(`confirm_batch_5`) — concrete examples beat hand-waving"
        )


# ---------------------------------------------------------------------------
# G — beta rationale comments in core/universal.yaml
# ---------------------------------------------------------------------------


class TestUniversalBetaRationale:
    """Every stochastic contract in universal.yaml gets a one-line
    rationale comment for its beta value.  The test asserts the
    aggregate (not each line individually) so future re-tuning
    can change values without rewriting the test, as long as the
    rationale stays present."""

    def test_each_beta_has_a_neighbouring_comment(self):
        """Walk the file, every `beta:` line must have a
        rationale comment within the 10 lines preceding it.  10 lines
        accommodates the longest contract (scope_respect — 7 lines
        between rationale and beta thanks to the multi-line `args:`
        block) while still being tight enough that an unrelated
        upstream comment can't satisfy the assertion."""
        lines = _read_pack("core/universal.yaml").splitlines()
        beta_lines = [
            i
            for i, ln in enumerate(lines)
            if "beta:" in ln and not ln.lstrip().startswith("#")
        ]
        assert beta_lines, "expected universal.yaml to have beta: entries"

        for idx in beta_lines:
            # Look back for a comment line carrying one of the
            # rationale keywords ("slip-through" / "missed" / "cost" /
            # "regulatory" / "safety" — the vocabulary the rationale
            # comments use).
            window = lines[max(0, idx - 10) : idx]
            has_rationale = any(
                ln.lstrip().startswith("#")
                and any(
                    kw in ln.lower()
                    for kw in (
                        "slip-through",
                        "missed",
                        "cost",
                        "regulatory",
                        "safety",
                        "harm",
                        "fluid",
                        "judge-defined",
                    )
                )
                for ln in window
            )
            assert has_rationale, (
                f"beta on line {idx + 1} has no nearby rationale comment.  "
                f"Window:\n" + "\n".join(window)
            )

    def test_top_level_beta_doc_block_present(self):
        """One-time prose at the top of the section explaining what
        beta means.  Without this, the per-rule rationales lack
        context — readers see "0.95" without knowing that higher =
        more aggressive."""
        text = _read_pack("core/universal.yaml")
        assert "weights the cost" in text or "missed violation" in text, (
            "universal.yaml § Adversarial missing the prose explaining what "
            "beta does — the per-rule comments need that context"
        )


# ---------------------------------------------------------------------------
# Smoke check — every pack still loads cleanly after the changes
# ---------------------------------------------------------------------------


class TestPacksStillLoadAfterFixes:
    """Quality fixes that break loading would be net-negative.  Run
    each pack through the include/load path with the minimum
    surrounding config (just `workspace:` for the fs pack)."""

    # All five shipped packs must round-trip through include + load.
    # openclaw was historically excluded (it used a hard-coded agent
    # id `openclaw_local` instead of the `*` template), but is now
    # template-shaped like the others and uses `<agent>` for the
    # one LTL atom that needs to reference the running agent.
    @pytest.mark.parametrize(
        "spec,needs_workspace",
        [
            ("sponsio:core/universal", False),
            ("sponsio:core/runaway", False),
            ("sponsio:capability/shell", False),
            ("sponsio:capability/filesystem", True),
            ("sponsio:incident/openclaw", True),
        ],
    )
    def test_pack_loads(self, tmp_path, spec, needs_workspace):
        ws_line = '    workspace: "/proj"\n' if needs_workspace else ""
        cfg_path = tmp_path / "sponsio.yaml"
        cfg_path.write_text(f"agents:\n  bot:\n{ws_line}    include: ['{spec}']\n")
        cfg = load_config(cfg_path)
        # Each pack should contribute at least a few rules — exact
        # counts shift over time as packs get tuned, but pinning a
        # nonzero lower bound catches "the rewrites broke parsing"
        # regressions.
        assert len(cfg.agents["bot"].contracts) > 0


# ---------------------------------------------------------------------------
# H — `<agent>` placeholder in LTL atoms gets substituted on include
# ---------------------------------------------------------------------------


class TestAgentPlaceholderRewrite:
    """openclaw § 5.2 has ``flow(<agent>, external)`` — the host's
    agent_id must be substituted in at include time, otherwise the
    taint contract becomes a silent no-op for any agent not literally
    named ``<agent>``."""

    def test_openclaw_taint_ltl_substitutes_agent_id(self, tmp_path):
        cfg_path = tmp_path / "sponsio.yaml"
        cfg_path.write_text(
            "agents:\n  myagent:\n"
            '    workspace: "/proj"\n'
            "    include: ['sponsio:incident/openclaw']\n"
        )
        cfg = load_config(cfg_path)
        ltls = []
        for c in cfg.agents["myagent"].contracts:
            es = c.enforcement if isinstance(c.enforcement, list) else [c.enforcement]
            for ce in es:
                if ce is not None and ce.ltl:
                    ltls.append(ce.ltl)
        # The taint LTL must reference `myagent`, not the literal
        # placeholder, and definitely not the historical `openclaw_local`.
        assert any("flow(myagent, external)" in s for s in ltls), (
            "expected `<agent>` to be substituted with `myagent` in "
            f"openclaw's taint LTL.  Got LTLs: {ltls!r}"
        )
        assert not any("<agent>" in s for s in ltls), (
            "no LTL should retain the unresolved `<agent>` placeholder "
            f"after include.  Got: {ltls!r}"
        )
        assert not any("openclaw_local" in s for s in ltls), (
            "openclaw's LTL still mentions the historical hard-coded "
            f"agent name.  Got: {ltls!r}"
        )
