"""Tests for edcloud.config."""

from edcloud.config import MANAGER_TAG_KEY, MANAGER_TAG_VALUE, InstanceConfig


def test_default_config():
    cfg = InstanceConfig()
    assert cfg.instance_type == "t3a.medium"
    assert cfg.volume_size_gb == 80
    assert cfg.volume_type == "gp3"
    assert cfg.state_volume_size_gb == 10
    assert cfg.state_volume_type == "gp3"
    assert cfg.state_volume_device_name == "/dev/sdf"
    assert cfg.tailscale_hostname == "edcloud"


def test_config_tags():
    cfg = InstanceConfig()
    assert MANAGER_TAG_KEY in cfg.tags
    assert cfg.tags[MANAGER_TAG_KEY] == MANAGER_TAG_VALUE
    assert cfg.name_tag == "edcloud"


def test_custom_config():
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


def test_config_is_frozen():
    cfg = InstanceConfig()
    try:
        cfg.instance_type = "t3a.large"  # type: ignore[misc]
        raise AssertionError("Should have raised AttributeError")
    except AttributeError:
        pass
