"""Signed rules against deletion, links, races and config changes
(second review, 2026-10-24). A covered bucket serves only bytes that
were verified, and every unsigned change, removal included, leaves the
last signed rules enforcing or the bucket held."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

from sponsio.daemon import audit, config, guard_service  # noqa: E402
from sponsio.daemon.guard_service import GuardService  # noqa: E402
from sponsio.daemon.signing import engine_dirs, sign_file  # noqa: E402

BUCKET = "_host_claude_code"
SIGNED = f"""version: "1"
defaults:
  unconfigured: deny
agents:
  {BUCKET}:
    contracts:
      - desc: "no rm -rf"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf"]]
"""
UNSIGNED = f"""version: "1"
agents:
  {BUCKET}:
    contracts: []
"""


def _keypair():
    from cryptography.hazmat.primitives import serialization

    private = ed25519.Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, base64.b64encode(raw).decode()


def _call(tool: str = "Bash", command: str = "rm -rf x") -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command} if tool == "Bash" else {"url": "u"},
        "session_id": "s1",
    }


def _allowed(svc: GuardService, **kw) -> bool:
    return svc.check(host="claude-code", payload=_call(**kw))["allowed"]


@pytest.fixture
def signed(tmp_path):
    """A covered bucket with signed rules: ``(state, lib, private, public)``."""
    private, public = _keypair()
    state = tmp_path / "state"
    config.save(state, config.DaemonConfig(signing_public_key=public))
    lib = tmp_path / "plugins" / BUCKET / "sponsio.yaml"
    lib.parent.mkdir(parents=True)
    lib.write_text(SIGNED)
    sign_file(lib, private)
    return state, lib, private, public


def _service(state: Path, lib: Path) -> GuardService:
    return GuardService(root=lib.parent.parent, state_dir=state)


# ---- deletion ----


def test_removing_the_signed_file_keeps_its_rules(signed):
    state, lib, _, _ = signed
    svc = _service(state, lib)
    assert not _allowed(svc)
    lib.unlink()
    Path(str(lib) + ".sig").unlink()
    for _ in range(2):
        assert not _allowed(svc), "deleting the signed file removed its rules"
        assert _allowed(svc, command="ls")
    (rec,) = audit.read(state, "rules_unsigned")
    assert "removed" in rec["reason"] and rec["kept_previous"] is True


def test_a_daemon_that_starts_after_the_removal_holds_the_bucket(signed):
    state, lib, _, _ = signed
    assert not _allowed(_service(state, lib))  # applied once: on record
    lib.unlink()
    restarted = _service(state, lib)
    assert not _allowed(restarted, command="ls")
    assert not _allowed(restarted, tool="mcp__acme__fetch")


def test_signed_rules_put_back_apply_again(signed):
    state, lib, private, _ = signed
    svc = _service(state, lib)
    assert not _allowed(svc)
    lib.unlink()
    assert not _allowed(svc)
    # Signed again, with one more contract: a strengthening applies at once.
    lib.write_text(
        SIGNED
        + """      - desc: "no shred"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["shred"]]
"""
    )
    sign_file(lib, private)
    assert _allowed(svc, command="ls")
    assert not _allowed(svc) and not _allowed(svc, command="shred f")


# ---- links ----


def test_a_rule_file_linked_into_the_engine_is_checked(tmp_path, signed):
    state, lib, _, _ = signed
    target = engine_dirs()[0] / "plugin" / "defaults" / f"{BUCKET}.yaml"
    assert target.exists()
    lib.unlink()
    Path(str(lib) + ".sig").unlink()
    lib.symlink_to(target)
    snap = _service(state, lib).store.resolve(BUCKET)
    assert snap is not None and snap.held_reason is not None


def test_a_rule_file_linked_to_an_unsigned_file_elsewhere_is_held(tmp_path, signed):
    state, lib, _, _ = signed
    other = tmp_path / "elsewhere.yaml"
    other.write_text(UNSIGNED)
    lib.unlink()
    lib.symlink_to(other)
    svc = _service(state, lib)
    assert not _allowed(svc, command="ls")


def test_a_running_daemon_keeps_signed_rules_when_the_file_becomes_a_link(
    tmp_path, signed
):
    state, lib, _, _ = signed
    svc = _service(state, lib)
    assert _allowed(svc, command="ls") and not _allowed(svc)
    other = tmp_path / "elsewhere.yaml"
    other.write_text(UNSIGNED)
    lib.unlink()
    lib.symlink_to(other)
    assert not _allowed(svc)


# ---- what is verified is what is parsed ----


def test_the_bytes_verified_are_the_bytes_parsed(signed, monkeypatch):
    state, lib, _, _ = signed
    lib.write_text(UNSIGNED)  # on disk when the daemon parses ...
    real = guard_service.load_snapshot
    swapped: list[bool] = []

    def racing_load(*a, **kw):
        snap = real(*a, **kw)
        if not swapped:
            lib.write_text(SIGNED)  # ... and signed again when it would verify
            swapped.append(True)
        return snap

    monkeypatch.setattr(guard_service, "load_snapshot", racing_load)
    svc = _service(state, lib)
    assert not _allowed(svc, command="ls")
    # The signed bytes now on disk are a change, and they load.
    assert _allowed(svc, command="ls") and not _allowed(svc)


def test_an_unsigned_include_that_does_not_load_is_held(tmp_path):
    private, public = _keypair()
    state = tmp_path / "state"
    config.save(state, config.DaemonConfig(on_error="allow", signing_public_key=public))
    bucket = tmp_path / "plugins" / BUCKET
    bucket.mkdir(parents=True)
    (bucket / "sponsio.yaml").write_text(
        SIGNED.replace("    contracts:", "    include: [./extra.yaml]\n    contracts:")
    )
    (bucket / "extra.yaml").write_text('agents:\n  "*":\n    contracts: []\n')
    sign_file(bucket / "sponsio.yaml", private)
    sign_file(bucket / "extra.yaml", private)
    (bucket / "extra.yaml").write_text("agents: [not, a, mapping]\n")
    svc = GuardService(root=tmp_path / "plugins", state_dir=state)
    out = svc.check(host="claude-code", payload=_call())
    assert not out["allowed"] and "verified" in out["reason"]
    assert audit.read(state, "rules_unsigned")


# ---- held buckets and their defaults ----


def test_a_held_host_library_does_not_decide_unconfigured_tools(signed):
    state, lib, _, _ = signed
    svc = _service(state, lib)
    assert not _allowed(svc, tool="mcp__acme__fetch")
    lib.write_text(UNSIGNED.replace("version", "defaults: {}\nversion"))
    assert not _allowed(svc, tool="mcp__acme__fetch")
    restarted = _service(state, lib)
    assert not _allowed(restarted, tool="mcp__acme__fetch")


# ---- the signing config ----


@pytest.mark.parametrize("buckets", ["", None, {}, 5, [], ["  "]])
def test_a_key_with_no_usable_bucket_list_covers_every_bucket(tmp_path, buckets):
    _, public = _keypair()
    state = tmp_path / "state"
    state.mkdir()
    import yaml

    raw = {"signing": {"public_key": public, "buckets": buckets}}
    (state / "daemon.yaml").write_text(yaml.safe_dump(raw))
    cfg = config.load(state)
    assert cfg.requires_signature(BUCKET)
    assert cfg.requires_signature("github")


def test_named_buckets_cover_only_themselves(tmp_path):
    _, public = _keypair()
    state = tmp_path / "state"
    state.mkdir()
    (state / "daemon.yaml").write_text(
        f"signing:\n  public_key: {public}\n  buckets: [{BUCKET}]\n"
    )
    cfg = config.load(state)
    assert cfg.requires_signature(BUCKET) and not cfg.requires_signature("github")


def test_turning_signing_on_holds_rules_loaded_unsigned(tmp_path):
    _, public = _keypair()
    state = tmp_path / "state"
    config.save(state, config.DaemonConfig())
    lib = tmp_path / "plugins" / BUCKET / "sponsio.yaml"
    lib.parent.mkdir(parents=True)
    lib.write_text(SIGNED)
    svc = _service(state, lib)
    assert _allowed(svc, command="ls")
    config.save(state, config.DaemonConfig(signing_public_key=public))
    assert not _allowed(svc, command="ls"), "unsigned rules still served"


def test_rotating_the_key_drops_rules_signed_by_the_old_one(signed):
    state, lib, _, _ = signed
    svc = _service(state, lib)
    assert _allowed(svc, command="ls")
    _, new_public = _keypair()
    config.save(state, config.DaemonConfig(signing_public_key=new_public))
    assert not _allowed(svc, command="ls"), "rules signed by the retired key served"
