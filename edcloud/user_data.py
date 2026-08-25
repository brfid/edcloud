"""Cloud-init user-data template rendering and input validation.

Handles interpolation of runtime variables into the cloud-init template,
with injection-prevention validation on all inputs.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

_USER_DATA_PATH = Path(__file__).resolve().parent.parent / "cloud-init" / "user-data.yaml"

# Render slots injected into the cloud-init template. These are the only names
# ``render()`` substitutes.
_RENDER_KEYS = (
    "TAILSCALE_AUTH_KEY_SSM_PARAMETER",
    "TAILSCALE_HOSTNAME",
    "AWS_REGION",
    "DOTFILES_REPO",
    "DOTFILES_BRANCH",
)


class _UserDataTemplate(string.Template):
    """``string.Template`` with a distinct ``@@`` delimiter.

    The cloud-init template is mostly shell, which uses ``$VAR`` and ``${VAR}``
    heavily. Using ``@@{KEY}`` for edcloud render slots means a shell variable
    can never collide with (and be silently clobbered by) a render key, even if
    a future maintainer introduces a shell variable named e.g. ``AWS_REGION``.
    """

    delimiter = "@@"


def validate_inputs(
    tailscale_hostname: str,
    tailscale_auth_key: str | None = None,
    tailscale_auth_key_ssm_parameter: str | None = None,
    aws_region: str | None = None,
    dotfiles_repo: str | None = None,
    dotfiles_branch: str | None = None,
) -> None:
    """Validate user-data template inputs to prevent injection attacks.

    Raises:
        ValueError: If any input contains invalid or dangerous characters.
    """
    if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$", tailscale_hostname):
        raise ValueError(
            f"Invalid tailscale_hostname: {tailscale_hostname!r}. "
            "Must be 1-63 alphanumeric/hyphen characters, cannot start/end with hyphen."
        )

    if tailscale_auth_key is not None:
        dangerous_chars = ["\n", "\r", "`", "$(", "${", ";", "'", '"', "|", "&"]
        for char in dangerous_chars:
            if char in tailscale_auth_key:
                raise ValueError(
                    f"Invalid tailscale_auth_key: contains dangerous character {char!r}"
                )

    if tailscale_auth_key_ssm_parameter is not None and not re.match(
        r"^[a-zA-Z0-9/_.-]+$", tailscale_auth_key_ssm_parameter
    ):
        raise ValueError(
            f"Invalid tailscale_auth_key_ssm_parameter: {tailscale_auth_key_ssm_parameter!r}. "
            "Must contain only alphanumeric, /, _, ., - characters."
        )

    if aws_region is not None and not re.match(r"^[a-z]{2}(-[a-z]+-[0-9]+)?$", aws_region):
        raise ValueError(
            f"Invalid aws_region: {aws_region!r}. Must match AWS region format (e.g., us-east-1)."
        )

    if dotfiles_repo is not None:
        if dotfiles_repo == "auto":
            pass
        elif not re.match(
            r"^(https://github\.com|git@github\.com:)[A-Za-z0-9._/-]+\.git$",
            dotfiles_repo,
        ):
            raise ValueError(
                f"Invalid dotfiles_repo: {dotfiles_repo!r}. "
                "Only GitHub URLs are supported (https or SSH, ending in .git), or 'auto'."
            )

    if dotfiles_branch is not None:
        if not re.match(r"^[A-Za-z0-9._/-]{1,100}$", dotfiles_branch):
            raise ValueError(
                f"Invalid dotfiles_branch: {dotfiles_branch!r}. "
                "Use a simple branch/ref name (alphanumeric, ., _, /, -)."
            )
        if ".." in dotfiles_branch or dotfiles_branch.startswith("-"):
            raise ValueError(
                f"Invalid dotfiles_branch: {dotfiles_branch!r}. "
                "Branch cannot contain '..' or start with '-'."
            )


def render(
    tailscale_auth_key_ssm_parameter: str,
    tailscale_hostname: str,
    aws_region: str,
    dotfiles_repo: str,
    dotfiles_branch: str,
) -> str:
    """Read the cloud-init template and interpolate runtime variables.

    Uses a distinct ``@@`` delimiter (see :class:`_UserDataTemplate`) for
    single-pass substitution that cannot collide with the template's shell
    ``$VAR`` / ``${VAR}`` usage. Strict ``substitute`` is intentional: an
    unknown ``@@{...}`` slot is a template bug and must fail at render time
    instead of remaining unresolved in the bootstrap script.

    Returns:
        Rendered user-data string ready for RunInstances.

    Raises:
        ValueError: If a template input is invalid.
        KeyError: If the template contains an unknown render slot.
        RuntimeError: If a render slot was left in the shell ``${KEY}`` form
            (which would silently boot empty) instead of ``@@{KEY}``.
    """
    validate_inputs(
        tailscale_hostname=tailscale_hostname,
        tailscale_auth_key_ssm_parameter=tailscale_auth_key_ssm_parameter,
        aws_region=aws_region,
        dotfiles_repo=dotfiles_repo,
        dotfiles_branch=dotfiles_branch,
    )
    raw = _USER_DATA_PATH.read_text()
    rendered = _UserDataTemplate(raw).substitute(
        TAILSCALE_AUTH_KEY_SSM_PARAMETER=tailscale_auth_key_ssm_parameter,
        TAILSCALE_HOSTNAME=tailscale_hostname,
        AWS_REGION=aws_region,
        DOTFILES_REPO=dotfiles_repo,
        DOTFILES_BRANCH=dotfiles_branch,
    )
    stray = [key for key in _RENDER_KEYS if f"${{{key}}}" in rendered]
    if stray:
        raise RuntimeError(
            "cloud-init template still contains shell-style placeholders for "
            f"edcloud render keys: {', '.join(stray)}. "
            "Use the @@{KEY} render syntax, not ${KEY}."
        )
    return rendered
