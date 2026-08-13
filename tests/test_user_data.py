"""Tests for cloud-init user-data rendering and input validation."""

from __future__ import annotations

import pytest

from edcloud import user_data

_VALID_KWARGS = {
    "tailscale_auth_key_ssm_parameter": "/edcloud/tailscale_auth_key",
    "tailscale_hostname": "edcloud",
    "aws_region": "us-east-1",
    "dotfiles_repo": "auto",
    "dotfiles_branch": "main",
}


def test_render_substitutes_all_slots():
    rendered = user_data.render(**_VALID_KWARGS)
    # Every render slot is filled; none of the @@{KEY} tokens survive.
    assert "@@{" not in rendered
    assert 'AWS_DEFAULT_REGION="us-east-1"' in rendered
    assert '--name "/edcloud/tailscale_auth_key"' in rendered
    assert '--hostname="edcloud"' in rendered


def test_render_preserves_shell_variables():
    """Shell $VAR / ${VAR} usage must pass through untouched."""
    rendered = user_data.render(**_VALID_KWARGS)
    for shell_var in ("$HOME", "${ROOT}", "$STATE_DEV", "$TS_AUTH_KEY"):
        assert shell_var in rendered, f"shell variable {shell_var} was clobbered"


def test_render_does_not_leak_delimiter_into_shell():
    """A hostname value is placed verbatim; the @@ delimiter never appears."""
    rendered = user_data.render(**_VALID_KWARGS)
    assert "@@" not in rendered


def test_render_raises_on_shell_style_render_slot(tmp_path, monkeypatch):
    """A render key left in ${KEY} form must fail loudly, not boot empty."""
    bogus = tmp_path / "user-data.yaml"
    bogus.write_text('region="${AWS_REGION}"\n')
    monkeypatch.setattr(user_data, "_USER_DATA_PATH", bogus)

    with pytest.raises(RuntimeError, match="shell-style placeholders"):
        user_data.render(**_VALID_KWARGS)


def test_render_raises_on_unknown_slot(tmp_path, monkeypatch):
    """An unknown @@{...} slot is a template bug and must raise at render."""
    bogus = tmp_path / "user-data.yaml"
    bogus.write_text('value="@@{NOT_A_REAL_KEY}"\n')
    monkeypatch.setattr(user_data, "_USER_DATA_PATH", bogus)

    with pytest.raises(KeyError):
        user_data.render(**_VALID_KWARGS)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tailscale_hostname", "bad host"),
        ("tailscale_hostname", "-leadinghyphen"),
        ("aws_region", "not_a_region"),
        ("dotfiles_repo", "https://evil.example/repo.git"),
        ("dotfiles_branch", "../escape"),
    ],
)
def test_validate_inputs_rejects_bad_values(field, value):
    kwargs = dict(_VALID_KWARGS)
    kwargs[field] = value
    with pytest.raises(ValueError):
        user_data.validate_inputs(**kwargs)


@pytest.mark.parametrize("payload", ["key;rm -rf /", "key$(whoami)", "key`id`", "key\nmore"])
def test_validate_inputs_rejects_dangerous_auth_key(payload):
    with pytest.raises(ValueError):
        user_data.validate_inputs(
            tailscale_hostname="edcloud",
            tailscale_auth_key=payload,
        )
