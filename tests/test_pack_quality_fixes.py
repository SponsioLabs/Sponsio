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
        # The doc must point to customized: as the disable mechanism
        # so users don't reach for "just delete the rule" or fork
        # the pack.
        assert "customized:" in text, (
            "shell.yaml § 4 missing the customized: workaround docs"
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
# G — beta rationale comments in core/llm_safety.yaml
# ---------------------------------------------------------------------------


class TestLlmSafetyBetaRationale:
    """Every stochastic contract in llm_safety.yaml gets a one-line
    rationale comment for its beta value.  The test asserts the
    aggregate (not each line individually) so future re-tuning
    can change values without rewriting the test, as long as the
    rationale stays present.

    History: this used to live in ``core/universal.yaml`` but the
    five sto contracts moved to ``core/llm_safety.yaml`` so the
    universal pack stops auto-pulling judge-LLM evaluations for
    tool-call-only agents."""

    def test_each_beta_has_a_neighbouring_comment(self):
        """Walk the file, every `beta:` line must have a
        rationale comment within the 10 lines preceding it.  10 lines
        accommodates the longest contract (scope_respect — 7 lines
        between rationale and beta thanks to the multi-line `args:`
        block) while still being tight enough that an unrelated
        upstream comment can't satisfy the assertion."""
        lines = _read_pack("core/llm_safety.yaml").splitlines()
        beta_lines = [
            i
            for i, ln in enumerate(lines)
            if "beta:" in ln and not ln.lstrip().startswith("#")
        ]
        assert beta_lines, "expected llm_safety.yaml to have beta: entries"

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
        text = _read_pack("core/llm_safety.yaml")
        assert "weights the cost" in text or "missed violation" in text, (
            "llm_safety.yaml § Adversarial missing the prose explaining what "
            "beta does — the per-rule comments need that context"
        )


class TestUniversalEmpty:
    """``core/universal`` is now an empty stub — see
    ``sponsio/contracts/core/universal.yaml`` for the rationale.  Pin
    the emptiness so a regression that shoves contracts back in there
    surfaces immediately (rather than silently re-introducing the
    judge-LLM-on-every-step behaviour we just removed)."""

    def test_universal_pack_is_empty(self, tmp_path):
        # Round-trip through the include path: the only legal way to
        # activate the ``*`` template is through ``include:``, so we
        # write a tiny config that pulls in core/universal and assert
        # the resolved agent has no contracts.
        cfg_path = tmp_path / "sponsio.yaml"
        cfg_path.write_text(
            "agents:\n  bot:\n    include: ['sponsio:core/universal']\n"
        )
        cfg = load_config(cfg_path)
        assert cfg.agents["bot"].contracts == [], (
            "core/universal.yaml must remain an empty stub — sto contracts "
            "moved to core/llm_safety.yaml so the pack can be auto-included "
            "without forcing judge-LLM calls on tool-call-only agents."
        )


# ---------------------------------------------------------------------------
# Smoke check — every pack still loads cleanly after the changes
# ---------------------------------------------------------------------------


class TestPacksStillLoadAfterFixes:
    """Quality fixes that break loading would be net-negative.  Run
    each pack through the include/load path with the minimum
    surrounding config (just `workspace:` for the fs pack)."""

    # All shipped packs that contribute rules must round-trip through
    # include + load.  openclaw was historically excluded (it used a
    # hard-coded agent id ``openclaw_local`` instead of the ``*``
    # template), but is now template-shaped like the others and uses
    # ``<agent>`` for the one LTL atom that needs to reference the
    # running agent.
    #
    # Two packs are excluded from this nonzero-contracts set on purpose:
    #   * ``sponsio:core/runaway`` — intentionally empty (old hard-coded
    #     budget defaults were arbitrary).
    #   * ``sponsio:core/universal`` — also intentionally empty after
    #     the sto contracts moved to ``core/llm_safety``.  Both have
    #     dedicated empty-load tests below.
    @pytest.mark.parametrize(
        "spec,needs_workspace",
        [
            ("sponsio:core/llm_safety", False),
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

    def test_runaway_pack_loads_empty_without_error(self, tmp_path):
        """``core/runaway`` is intentionally empty — the include must
        still resolve cleanly so existing yaml files keep working,
        just with zero contracts contributed.  Asserts the file
        parses + agents block compiles, even with an empty list."""
        cfg_path = tmp_path / "sponsio.yaml"
        cfg_path.write_text("agents:\n  bot:\n    include: ['sponsio:core/runaway']\n")
        cfg = load_config(cfg_path)
        assert cfg.agents["bot"].contracts == []

    def test_universal_pack_loads_empty_without_error(self, tmp_path):
        """``core/universal`` is intentionally empty — the sto
        contracts that used to live here moved to ``core/llm_safety``.
        Existing user configs that ``include: sponsio:core/universal``
        must keep loading (zero contracts contributed), not error out
        on a missing pack."""
        cfg_path = tmp_path / "sponsio.yaml"
        cfg_path.write_text(
            "agents:\n  bot:\n    include: ['sponsio:core/universal']\n"
        )
        cfg = load_config(cfg_path)
        assert cfg.agents["bot"].contracts == []


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
            es = c.guarantee if isinstance(c.guarantee, list) else [c.guarantee]
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


# ---------------------------------------------------------------------------
# I — Usability pass: default-noise reductions on the stricter rules
#
# After the first real-world integration pass (earnings-forecast-hub,
# Probatio), several rules were identified as producing day-1 false
# positives.  Pin the adjustments so future edits can't silently revert
# us to the noisy defaults:
#
#   * confirm-1:1 rules must gate on `A:` so they're vacuous-true until
#     the integration opts in by emitting the marker.
#   * scope_respect must NOT be in core/universal's default contracts —
#     the generic scope string is judge-noise.
#   * Line-continuation regex must require a privileged-command token,
#     so multi-line Makefiles / Dockerfiles don't trip the rule.
#   * untrusted_source_gate default sinks must not include send_message
#     (RAG: web_fetch → send_message is the standard pattern).
# ---------------------------------------------------------------------------


class TestUsabilityTuning:
    def test_shell_confirm_reuse_rule_gated_on_assumption(self):
        """Rule only fires once the agent emits ``confirm_reconfirmed``."""
        text = _read_pack("capability/shell.yaml")
        # The desc + A: pair is what we're pinning — if either drifts,
        # users on day 1 either get drowned in false positives (no A:)
        # or silently lose the rule (no desc).
        assert "Each exec call needs its own confirm_reconfirmed" in text
        m = re.search(
            r'desc:\s*"Each exec call needs its own confirm_reconfirmed.*?'
            r'A:\s*"called\s*`confirm_reconfirmed`"',
            text,
            re.DOTALL,
        )
        assert m, (
            "shell.yaml §4 confirm-1:1 rule must gate on "
            '`A: "called `confirm_reconfirmed`"` — otherwise every '
            "exec call flags until the marker tool is wired in."
        )

    def test_openclaw_confirm_reuse_rules_gated_on_assumption(self):
        """Both openclaw §8 confirm rules must carry the same gate."""
        text = _read_pack("incident/openclaw.yaml")
        # Two separate rules (exec + apply_patch), both need A:.
        for desc_frag in (
            "Confirmations cannot be reused",
            "Same for apply_patch",
        ):
            m = re.search(
                rf'desc:\s*"{re.escape(desc_frag)}.*?'
                r'A:\s*"called\s*`confirm_reconfirmed`"',
                text,
                re.DOTALL,
            )
            assert m, (
                f"openclaw.yaml §8 rule '{desc_frag}' must gate on "
                '`A: "called `confirm_reconfirmed`"` to avoid day-1 '
                "false positives."
            )

    def test_llm_safety_does_not_ship_scope_respect_as_default(self):
        """``scope_respect`` is opt-in — a generic default scope string is
        judge-noise.  The pack may mention it in a recipe comment, but
        must not emit a contract entry using the pattern.

        History: this assertion used to target ``core/universal.yaml``;
        the sto contracts have since moved to ``core/llm_safety.yaml``
        and so does this test."""
        text = _read_pack("core/llm_safety.yaml")
        # Pattern token must only appear in comment lines (after #) or
        # in the recipe block.  A real rule would have it on a YAML
        # data line (no leading #).
        for line in text.splitlines():
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            assert "pattern: scope_respect" not in stripped, (
                "core/llm_safety.yaml must not ship scope_respect as a "
                "default contract — the generic scope string makes it "
                "noise on day 1.  Keep it in the commented recipe only."
            )

    def test_shell_linecont_regex_requires_privileged_cmd(self):
        """`\\\\\\n` alone trips on Makefiles / Dockerfiles / multi-line
        scripts.  The regex must require a privileged-command token to
        follow the continuation, so only the CVE-style evasion fires."""
        text = _read_pack("capability/shell.yaml")
        # The old broad regex was `[\\\\\\n]`.  Pin that the new form
        # contains at least one privileged token anchored to the
        # continuation.  We don't pin the exact regex (too brittle) —
        # just that `sudo`, `rm`, and the `\\n` anchor co-appear in
        # the shell-exec § 1 rule.
        assert "line-continuation chained into a privileged" in text, (
            "capability/shell.yaml § 1 should describe the rule as "
            "'line-continuation chained into a privileged command', "
            "not the broader 'ban all line-continuation' variant."
        )
        # Hex search for the `\\n\s*(sudo|...` pattern fragment.
        assert re.search(r"\\\\\\\\\\\\n\\\\s\*\(sudo", text), (
            "shell § 1 regex must require a privileged command token "
            "(sudo/rm/…) to follow the continuation, so multi-line "
            "scripts don't trip the rule."
        )

    def test_openclaw_untrusted_gate_excludes_send_message_default(self):
        """RAG's ``web_fetch → reason → send_message`` is the common
        case; requiring re-confirmation on every send_message after a
        fetch would cripple chat agents.  Default sinks must not list
        send_message."""
        text = _read_pack("incident/openclaw.yaml")
        # Walk to the untrusted_source_gate block and check the default
        # sink list — comment blocks with example overrides are fine.
        m = re.search(
            r"pattern:\s*untrusted_source_gate\s*\n"
            r"\s*args:\s*\n"
            r"\s*-\s*\[web_fetch\]\s*\n"
            r"\s*-\s*\[([^\]]+)\]",
            text,
        )
        assert m, "could not locate the default untrusted_source_gate sink list"
        sinks = [s.strip() for s in m.group(1).split(",")]
        assert "send_message" not in sinks, (
            f"send_message must be opt-in for untrusted_source_gate "
            f"(RAG is the common path).  Got default sinks: {sinks!r}"
        )
        # Verify the expected core sinks are still present.
        for required in ("exec", "install_skill", "apply_patch"):
            assert required in sinks, (
                f"missing {required!r} from untrusted_source_gate default "
                f"sinks; got {sinks!r}"
            )

    def test_filesystem_read_scope_expanded_beyond_workspace_tmp(self):
        """Read scope is deliberately broader than write scope.  Pin
        that the common read paths (/etc/, /var/log/, /usr/include/)
        appear in the read-scope block so future edits don't tighten
        it back to the old workspace-plus-tmp-only form."""
        # Rather than regex-matching the YAML shape (brittle as the
        # file evolves), load the pack and assert the effective args
        # on the `read`-scope rule via an inline tmp config.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sponsio.yaml"
            # The read-scope rule lives in `filesystem-strict` (split
            # off in 2026-04 to keep the base `filesystem` pack
            # workspace-free).  Include both — the strict pack carries
            # the rule under test.
            p.write_text(
                "agents:\n  bot:\n"
                '    workspace: "/proj"\n'
                "    include:\n"
                "      - sponsio:capability/filesystem\n"
                "      - sponsio:capability/filesystem-strict\n"
            )
            cfg = load_config(p)

        # Find the contract whose enforcement is scope_limit on `read`.
        prefixes = None
        for c in cfg.agents["bot"].contracts:
            es = c.guarantee if isinstance(c.guarantee, list) else [c.guarantee]
            for ce in es:
                if ce is None or ce.pattern != "scope_limit" or not ce.args:
                    continue
                if ce.args[0] == "read":
                    prefixes = ce.args[1]
                    break
            if prefixes is not None:
                break
        assert prefixes is not None, "read-scope `scope_limit` rule not found"
        assert isinstance(prefixes, list), (
            f"read-scope prefixes must be a list, got {type(prefixes).__name__}"
        )
        # Workspace placeholder is rewritten to the configured path at
        # include time; the other entries are literal.
        assert any(p.startswith("/proj") for p in prefixes), (
            f"read scope must contain the workspace path; got {prefixes!r}"
        )
        for required in ("/etc/", "/var/log/", "/usr/include/"):
            assert required in prefixes, (
                f"read scope must include {required!r} (common diagnostic / "
                f"header path) — got {prefixes!r}"
            )

    def test_filesystem_blacklist_hardens_sensitive_etc_paths(self):
        """Broadening read scope to /etc/ means shadow / sudoers / ssh
        host keys must be hard-denied in the blacklist — otherwise the
        relaxation creates a real security hole."""
        text = _read_pack("capability/filesystem.yaml")
        for required in (
            r"^/etc/shadow",
            r"^/etc/sudoers",
            r"^/etc/ssh/ssh_host_",
        ):
            assert required in text, (
                f"filesystem blacklist missing hard-deny for {required!r} — "
                "broadening read scope to /etc/ without this is unsafe."
            )
