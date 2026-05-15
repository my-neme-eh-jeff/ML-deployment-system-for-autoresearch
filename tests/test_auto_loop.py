"""Tests for the differentiator paths in the autoresearch loop.

The original test suite covers preprocess/train/evaluate. This file targets
the controller-side helpers the post-audit fixes introduced — none of which
otherwise have any CI signal.
"""

from __future__ import annotations

import importlib

# Import the module fresh so its top-level `load_dotenv` doesn't pull a stale
# env into the test process. The functions under test are pure (no side
# effects beyond their own args), so module-level side effects are fine.
auto_loop = importlib.import_module("auto_experiment.auto_loop")


# ── _slugify_branch_segment ──────────────────────────────────────────────────


def test_slugify_keeps_clean_input():
    assert auto_loop._slugify_branch_segment("rf_balanced_more_features") == (
        "rf_balanced_more_features"
    )


def test_slugify_replaces_spaces_with_hyphen():
    assert auto_loop._slugify_branch_segment("tune alpha beta") == "tune-alpha-beta"


def test_slugify_strips_unicode_and_special_chars():
    # The LLM has actually produced names like "tune α / β regularization"
    # in prompt experiments. Git rejects spaces and slashes.
    out = auto_loop._slugify_branch_segment("tune α / β regularization")
    assert " " not in out
    assert "/" not in out
    assert "α" not in out and "β" not in out


def test_slugify_strips_leading_trailing_punctuation():
    assert auto_loop._slugify_branch_segment("..weird..name..") == "weird..name"
    assert auto_loop._slugify_branch_segment("---weird---") == "weird"


def test_slugify_truncates_to_30_chars():
    long_name = "a" * 100
    assert len(auto_loop._slugify_branch_segment(long_name)) == 30


def test_slugify_falls_back_to_iter_for_empty_input():
    assert auto_loop._slugify_branch_segment("") == "iter"
    assert auto_loop._slugify_branch_segment("///") == "iter"
    assert auto_loop._slugify_branch_segment(None) == "iter"  # type: ignore[arg-type]


# ── _redact_secrets ──────────────────────────────────────────────────────────


def test_redact_handles_none_and_empty():
    assert auto_loop._redact_secrets(None) == ""
    assert auto_loop._redact_secrets("") == ""


def test_redact_aws_access_key_id_in_assignment():
    out = auto_loop._redact_secrets('AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"')
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "REDACTED" in out


def test_redact_github_token():
    out = auto_loop._redact_secrets("GITHUB_TOKEN=ghp_abc123xyz")
    assert "ghp_abc123xyz" not in out
    assert "REDACTED" in out


def test_redact_anthropic_key_shape():
    out = auto_loop._redact_secrets("key = sk-ant-api03-abcdefghijklmnop12345")
    assert "sk-ant-api03-abcdefghijklmnop12345" not in out


def test_redact_bearer_token_in_error_text():
    out = auto_loop._redact_secrets(
        "401 Unauthorized: Authorization: Bearer abc.def.ghi"
    )
    assert "abc.def.ghi" not in out
    assert "Bearer [REDACTED]" in out


def test_redact_pem_block():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = auto_loop._redact_secrets(pem)
    assert "MIIEpAIBAA" not in out
    assert "REDACTED-PEM" in out


def test_redact_leaves_normal_text_alone():
    msg = "Trained HistGradientBoostingClassifier with learning_rate=0.05"
    assert auto_loop._redact_secrets(msg) == msg


# ── _rewrite_last_history_row_to_failed ──────────────────────────────────────


HEADER = (
    "timestamp\texp_num\texperiment_name\tchange_type\tauc_before\tauc_after\t"
    "delta\toutcome\tinput_tokens\toutput_tokens\tcost_usd\trationale\n"
)


def _write_history(tmp_path, content: str):
    p = tmp_path / "history.tsv"
    p.write_text(content)
    return p


def test_rewrite_no_op_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_loop, "HISTORY_PATH", tmp_path / "missing.tsv")
    auto_loop._rewrite_last_history_row_to_failed("any")
    assert not (tmp_path / "missing.tsv").exists()


def test_rewrite_no_op_when_header_only(tmp_path, monkeypatch):
    p = _write_history(tmp_path, HEADER)
    monkeypatch.setattr(auto_loop, "HISTORY_PATH", p)
    auto_loop._rewrite_last_history_row_to_failed("any")
    assert p.read_text() == HEADER  # untouched


def test_rewrite_no_op_when_last_row_not_improved(tmp_path, monkeypatch):
    row = (
        "2026-01-01\t1\tfoo\tparams_only\t0.9\t0.8\t-0.1\tfailed\t0\t0\t0\trationale\n"
    )
    p = _write_history(tmp_path, HEADER + row)
    monkeypatch.setattr(auto_loop, "HISTORY_PATH", p)
    auto_loop._rewrite_last_history_row_to_failed("any")
    assert p.read_text() == HEADER + row  # not touched (already failed)


def test_rewrite_flips_improved_to_failed_and_appends_reason(tmp_path, monkeypatch):
    row = (
        "2026-01-01\t1\tfoo\tparams_only\t0.85\t0.91\t0.06\timproved\t"
        "100\t200\t0.01\twhy this should help\n"
    )
    p = _write_history(tmp_path, HEADER + row)
    monkeypatch.setattr(auto_loop, "HISTORY_PATH", p)
    auto_loop._rewrite_last_history_row_to_failed("PR #42 closed without merging")
    new = p.read_text()
    assert new.startswith(HEADER)
    last = new.strip().splitlines()[-1]
    cols = last.split("\t")
    assert cols[7] == "failed"  # outcome flipped
    assert "[rolled back: PR #42 closed without merging]" in cols[-1]
    assert cols[2] == "foo"  # other columns preserved


def test_rewrite_only_touches_last_row(tmp_path, monkeypatch):
    first = (
        "2026-01-01\t1\tfoo\tparams_only\t0.85\t0.91\t0.06\timproved\t"
        "100\t200\t0.01\tfirst\n"
    )
    last = (
        "2026-01-02\t2\tbar\tparams_only\t0.91\t0.93\t0.02\timproved\t"
        "100\t200\t0.01\tlast\n"
    )
    p = _write_history(tmp_path, HEADER + first + last)
    monkeypatch.setattr(auto_loop, "HISTORY_PATH", p)
    auto_loop._rewrite_last_history_row_to_failed("anything")
    new = p.read_text()
    lines = new.strip().splitlines()
    # First data row still improved.
    assert lines[1].split("\t")[7] == "improved"
    # Last data row flipped.
    assert lines[2].split("\t")[7] == "failed"
