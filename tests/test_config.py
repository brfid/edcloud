"""Tests for edcloud.config."""

from typing import Any

from edcloud.config import (
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    InstanceConfig,
    get_volume_ids,
    has_managed_tag,
    managed_filter,
    tag_value,
)


def test_default_config() -> None:
    cfg = InstanceConfig()
    assert cfg.instance_type == "t3a.medium"
    assert cfg.volume_size_gb == 30
    assert cfg.volume_type == "gp3"
    assert cfg.state_volume_size_gb == 30
    assert cfg.state_volume_type == "gp3"
    assert cfg.state_volume_device_name == "/dev/sdf"
    assert cfg.tailscale_hostname == "edcloud"


def test_config_tags() -> None:
    cfg = InstanceConfig()
    assert MANAGER_TAG_KEY in cfg.tags
    assert cfg.tags[MANAGER_TAG_KEY] == MANAGER_TAG_VALUE
    assert cfg.name_tag == "edcloud"


def test_custom_config() -> None:
    cfg = InstanceConfig(
        instance_type="t3a.small",
        volume_size_gb=40,
        state_volume_size_gb=20,
        tailscale_hostname="test-lab",
    )
    assert cfg.instance_type == "t3a.small"
    assert cfg.volume_size_gb == 40
    assert cfg.state_volume_size_gb == 20
    assert cfg.tailscale_hostname == "test-lab"


def test_config_is_frozen() -> None:
    cfg = InstanceConfig()
    try:
        cfg.instance_type = "t3a.large"  # type: ignore[misc]
        raise AssertionError("Should have raised AttributeError")
    except AttributeError:
        pass


def test_managed_filter() -> None:
    result = managed_filter()
    assert len(result) == 1
    assert result[0]["Name"] == f"tag:{MANAGER_TAG_KEY}"
    assert result[0]["Values"] == [MANAGER_TAG_VALUE]


def test_has_managed_tag_true() -> None:
    tags = [
        {"Key": "Name", "Value": "edcloud"},
        {"Key": MANAGER_TAG_KEY, "Value": MANAGER_TAG_VALUE},
    ]
    assert has_managed_tag(tags) is True


def test_has_managed_tag_false() -> None:
    tags = [{"Key": "Name", "Value": "edcloud"}]
    assert has_managed_tag(tags) is False


def test_has_managed_tag_none() -> None:
    assert has_managed_tag(None) is False


def test_has_managed_tag_empty() -> None:
    assert has_managed_tag([]) is False


def test_get_volume_ids() -> None:
    instance: dict[str, Any] = {
        "BlockDeviceMappings": [
            {"DeviceName": "/dev/sda1", "Ebs": {"VolumeId": "vol-abc123"}},
            {"DeviceName": "/dev/sdf", "Ebs": {"VolumeId": "vol-def456"}},
        ]
    }
    result = get_volume_ids(instance)
    assert result == ["vol-abc123", "vol-def456"]


def test_get_volume_ids_empty() -> None:
    instance: dict[str, Any] = {"BlockDeviceMappings": []}
    result = get_volume_ids(instance)
    assert result == []


def test_get_volume_ids_no_mappings() -> None:
    instance: dict[str, Any] = {}
    result = get_volume_ids(instance)
    assert result == []


class TestTagValue:
    def test_found(self) -> None:
        tags = [{"Key": "Name", "Value": "edcloud"}, {"Key": "env", "Value": "lab"}]
        assert tag_value(tags, "env") == "lab"

    def test_not_found(self) -> None:
        tags = [{"Key": "Name", "Value": "edcloud"}]
        assert tag_value(tags, "missing") is None

    def test_none_tags(self) -> None:
        assert tag_value(None, "any") is None

    def test_empty_tags(self) -> None:
        assert tag_value([], "any") is None
