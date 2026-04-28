"""Tests for sponsio/sponsiorc.py — the rcfile loader.

Coverage:

* Loader: missing file, malformed yaml, partial dict, source_path
  population, search-up-the-tree behavior, git-boundary stop.

Integration tests against ``detect_framework`` / ``detect_provider``
were removed when the rcfile-aware versions of those helpers were
rolled back from cli.py / onboard.py.  The rcfile loader itself stands
alone and is unit-tested below.
"""

from __future__ import annotations


from sponsio.sponsiorc import (
    find_sponsiorc,
    load_sponsiorc,
)


# ---------------------------------------------------------------------------
# load_sponsiorc — basic file IO
# ---------------------------------------------------------------------------


class TestLoadSponsiorc:
    def test_no_file(self, tmp_path):
        rc = load_sponsiorc(tmp_path)
        assert not rc.found
        assert rc.framework is None
        assert rc.extractor_provider is None

    def test_minimal_file(self, tmp_path):
        (tmp_path / ".sponsiorc").write_text("framework: langgraph\n")
        rc = load_sponsiorc(tmp_path)
        assert rc.found
        assert rc.framework == "langgraph"
        # Other fields untouched.
        assert rc.extractor_provider is None
        assert rc.judge_provider is None

    def test_full_config(self, tmp_path):
        (tmp_path / ".sponsiorc").write_text(
            """
framework: crewai
extractor:
  provider: anthropic
  model: claude-3-5-sonnet-20241022
  api_key_env: ANTHROPIC_API_KEY
judge:
  provider: anthropic
  model: claude-3-5-sonnet-20241022
  api_key_env: ANTHROPIC_API_KEY
  fallback_mode: deny
"""
        )
        rc = load_sponsiorc(tmp_path)
        assert rc.found
        assert rc.framework == "crewai"
        assert rc.extractor_provider == "anthropic"
        assert rc.extractor_model == "claude-3-5-sonnet-20241022"
        assert rc.extractor_api_key_env == "ANTHROPIC_API_KEY"
        assert rc.judge_provider == "anthropic"
        assert rc.judge_fallback_mode == "deny"

    def test_malformed_yaml_returns_empty(self, tmp_path):
        # Truly broken yaml: unterminated bracket.
        (tmp_path / ".sponsiorc").write_text("framework: [oops\n")
        rc = load_sponsiorc(tmp_path)
        # Silent failure — the rcfile is treated as absent.
        assert not rc.found

    def test_non_dict_top_level(self, tmp_path):
        # Yaml parses to a list instead of a mapping.
        (tmp_path / ".sponsiorc").write_text("- foo\n- bar\n")
        rc = load_sponsiorc(tmp_path)
        assert not rc.found

    def test_extractor_typo_doesnt_crash(self, tmp_path):
        # User wrote `extractor: gemini` instead of nested form.  We
        # silently coerce non-dict sub-section to empty rather than
        # crash; partial info still loads.
        (tmp_path / ".sponsiorc").write_text(
            "framework: langgraph\nextractor: gemini\n"
        )
        rc = load_sponsiorc(tmp_path)
        assert rc.found
        assert rc.framework == "langgraph"
        assert rc.extractor_provider is None  # bogus shape ignored

    def test_source_path_populated(self, tmp_path):
        path = tmp_path / ".sponsiorc"
        path.write_text("framework: none\n")
        rc = load_sponsiorc(tmp_path)
        assert rc.source_path == path


# ---------------------------------------------------------------------------
# find_sponsiorc — search behavior
# ---------------------------------------------------------------------------


class TestFindSponsiorc:
    def test_found_in_start_dir(self, tmp_path):
        (tmp_path / ".sponsiorc").write_text("framework: none\n")
        assert find_sponsiorc(tmp_path) == tmp_path / ".sponsiorc"

    def test_walks_up_one_level(self, tmp_path):
        (tmp_path / ".sponsiorc").write_text("framework: none\n")
        sub = tmp_path / "subdir"
        sub.mkdir()
        assert find_sponsiorc(sub) == tmp_path / ".sponsiorc"

    def test_walks_up_multiple_levels(self, tmp_path):
        (tmp_path / ".sponsiorc").write_text("framework: none\n")
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        assert find_sponsiorc(deep) == tmp_path / ".sponsiorc"

    def test_stops_at_git_root(self, tmp_path):
        # `.sponsiorc` is at tmp_path, but tmp_path/proj has its
        # own `.git/` — search from tmp_path/proj/sub must NOT
        # leak past the git boundary.
        (tmp_path / ".sponsiorc").write_text("framework: outside\n")
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        sub = proj / "sub"
        sub.mkdir()
        assert find_sponsiorc(sub) is None

    def test_starts_from_file(self, tmp_path):
        # User passed `sponsio onboard agent.py` — find_sponsiorc
        # should search from agent.py's parent directory.
        (tmp_path / ".sponsiorc").write_text("framework: none\n")
        agent = tmp_path / "agent.py"
        agent.write_text("# stub\n")
        assert find_sponsiorc(agent) == tmp_path / ".sponsiorc"

    def test_nonexistent_start_returns_none(self, tmp_path):
        assert find_sponsiorc(tmp_path / "nope") is None
