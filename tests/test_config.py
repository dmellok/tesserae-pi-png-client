from __future__ import annotations

from pathlib import Path

import pytest

from tesserae_pi_png_client.config import (
    DEFAULT_TOML,
    Config,
    RestConfig,
    load_config,
    parse_toml,
    render_config_toml,
    render_from_config,
    save_config,
    with_rest_updates,
)


def test_default_toml_parses() -> None:
    cfg = parse_toml(DEFAULT_TOML)
    assert isinstance(cfg, Config)
    assert cfg.mqtt.host == "192.168.1.10"
    assert cfg.mqtt.port == 1883
    assert cfg.mqtt.username == ""
    assert cfg.mqtt.password == ""
    assert cfg.mqtt.client_id == "pi-impression-png-1"
    assert cfg.mqtt.device_id == "pi_png"
    assert cfg.mqtt.keepalive == 60
    assert cfg.http.download_timeout_s == 30
    assert cfg.http.max_frame_bytes == 16_000_000
    assert cfg.logging.level == "INFO"


def test_default_toml_has_no_panel_section() -> None:
    # Auto-detection via HAT EEPROM means no [panel] in the file.
    assert "[panel]" not in DEFAULT_TOML


def test_load_config_creates_default_on_first_run(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    assert not path.exists()
    cfg = load_config(path)
    assert path.exists()
    assert cfg.mqtt.client_id == "pi-impression-png-1"


def test_bad_port_rejected() -> None:
    bad = DEFAULT_TOML.replace("port = 1883", "port = 0")
    with pytest.raises(ValueError, match="out of range"):
        parse_toml(bad)


def test_port_above_max_rejected() -> None:
    bad = DEFAULT_TOML.replace("port = 1883", "port = 65536")
    with pytest.raises(ValueError, match="out of range"):
        parse_toml(bad)


def test_negative_keepalive_rejected() -> None:
    bad = DEFAULT_TOML.replace("keepalive = 60", "keepalive = -1")
    with pytest.raises(ValueError, match="keepalive"):
        parse_toml(bad)


def test_missing_client_id_rejected() -> None:
    bad = DEFAULT_TOML.replace(
        'client_id = "pi-impression-png-1"\n', ""
    )
    with pytest.raises(ValueError, match="client_id"):
        parse_toml(bad)


def test_unknown_log_level_rejected() -> None:
    bad = DEFAULT_TOML.replace('level = "INFO"', 'level = "VERBOSE"')
    with pytest.raises(ValueError, match="level"):
        parse_toml(bad)


def test_non_positive_timeout_rejected() -> None:
    bad = DEFAULT_TOML.replace("download_timeout_s = 30", "download_timeout_s = 0")
    with pytest.raises(ValueError, match="download_timeout_s"):
        parse_toml(bad)


def test_non_positive_max_frame_bytes_rejected() -> None:
    bad = DEFAULT_TOML.replace("max_frame_bytes = 16000000", "max_frame_bytes = -1")
    with pytest.raises(ValueError, match="max_frame_bytes"):
        parse_toml(bad)


def test_overrides_take_effect() -> None:
    bad = DEFAULT_TOML.replace(
        'host = "192.168.1.10"', 'host = "broker.local"'
    ).replace("port = 1883", "port = 8883")
    cfg = parse_toml(bad)
    assert cfg.mqtt.host == "broker.local"
    assert cfg.mqtt.port == 8883


def test_render_no_args_matches_default() -> None:
    assert render_config_toml() == DEFAULT_TOML


def test_render_overrides_round_trip() -> None:
    body = render_config_toml(
        mqtt_host="broker.lan",
        mqtt_port=8883,
        mqtt_username="alice",
        mqtt_password="hunter2",
        mqtt_client_id="kitchen-display",
    )
    cfg = parse_toml(body)
    assert cfg.mqtt.host == "broker.lan"
    assert cfg.mqtt.port == 8883
    assert cfg.mqtt.username == "alice"
    assert cfg.mqtt.password == "hunter2"
    assert cfg.mqtt.client_id == "kitchen-display"


def test_render_escapes_quote_in_string_value() -> None:
    body = render_config_toml(mqtt_password='abc"def')
    cfg = parse_toml(body)
    assert cfg.mqtt.password == 'abc"def'


def test_render_escapes_backslash_in_string_value() -> None:
    body = render_config_toml(mqtt_password=r"a\b")
    cfg = parse_toml(body)
    assert cfg.mqtt.password == r"a\b"


def test_bool_value_for_int_field_rejected() -> None:
    bad = DEFAULT_TOML.replace("port = 1883", "port = true")
    with pytest.raises(ValueError, match="port"):
        parse_toml(bad)


def test_device_id_defaults_when_missing() -> None:
    # An older config.toml predating device_id should parse with the new default.
    without = DEFAULT_TOML.replace('device_id = "pi_png"  # MQTT topic prefix\n', "")
    assert "device_id" not in without
    cfg = parse_toml(without)
    assert cfg.mqtt.device_id == "pi_png"


def test_device_id_override_round_trips() -> None:
    body = render_config_toml(device_id="pi_lounge")
    cfg = parse_toml(body)
    assert cfg.mqtt.device_id == "pi_lounge"


def test_invalid_device_id_uppercase_rejected() -> None:
    bad = DEFAULT_TOML.replace('device_id = "pi_png"', 'device_id = "Pi_PNG"')
    with pytest.raises(ValueError, match="device_id"):
        parse_toml(bad)


def test_invalid_device_id_leading_digit_rejected() -> None:
    bad = DEFAULT_TOML.replace('device_id = "pi_png"', 'device_id = "1pi"')
    with pytest.raises(ValueError, match="device_id"):
        parse_toml(bad)


def test_invalid_device_id_too_short_rejected() -> None:
    bad = DEFAULT_TOML.replace('device_id = "pi_png"', 'device_id = "p"')
    with pytest.raises(ValueError, match="device_id"):
        parse_toml(bad)


# --- transport_mode + [rest] section ------------------------------------------


def test_default_transport_mode_is_rest() -> None:
    # REST is the new default for fresh installs — the install prompt also
    # picks it. Existing configs that predate the key still fall back to
    # MQTT via the parser fallback; see test_missing_transport_mode_*.
    cfg = parse_toml(DEFAULT_TOML)
    assert cfg.transport_mode == "rest"
    assert cfg.rest.server_url == "http://tesserae.local:8765"
    assert cfg.rest.device_token == ""
    assert cfg.rest.pairing_code == ""
    assert cfg.rest.last_frame_etag == ""
    assert cfg.rest.poll_interval_s == 60


def test_unknown_transport_mode_rejected() -> None:
    bad = DEFAULT_TOML.replace(
        'transport_mode = "rest"', 'transport_mode = "carrier-pigeon"'
    )
    with pytest.raises(ValueError, match="transport_mode"):
        parse_toml(bad)


def test_rest_mode_requires_server_url() -> None:
    # DEFAULT_TOML already has rest mode with a placeholder server_url —
    # strip the value to trigger the missing-url validation.
    bad = DEFAULT_TOML.replace(
        'server_url = "http://tesserae.local:8765"', 'server_url = ""'
    )
    with pytest.raises(ValueError, match="server_url"):
        parse_toml(bad)


def test_rest_mode_with_custom_server_url_parses() -> None:
    body = render_config_toml(
        transport_mode="rest",
        rest_server_url="http://192.168.1.20:8765",
        rest_pairing_code="ABC123",
    )
    cfg = parse_toml(body)
    assert cfg.transport_mode == "rest"
    assert cfg.rest.server_url == "http://192.168.1.20:8765"
    assert cfg.rest.pairing_code == "ABC123"


def test_missing_transport_mode_defaults_to_mqtt() -> None:
    # A config predating the REST split (no transport_mode key) keeps the
    # MQTT behaviour without prompting the user to re-pair.
    body = "\n".join(
        line for line in DEFAULT_TOML.splitlines() if "transport_mode" not in line
    ) + "\n"
    cfg = parse_toml(body)
    assert cfg.transport_mode == "mqtt"


def test_mqtt_mode_explicit_still_works() -> None:
    body = render_config_toml(transport_mode="mqtt")
    cfg = parse_toml(body)
    assert cfg.transport_mode == "mqtt"


def test_negative_poll_interval_rejected() -> None:
    body = render_config_toml(rest_poll_interval_s=60)
    bad = body.replace("poll_interval_s = 60", "poll_interval_s = 0")
    with pytest.raises(ValueError, match="poll_interval_s"):
        parse_toml(bad)


# --- save_config round-trip ---------------------------------------------------


def test_save_config_round_trips_rest_state(tmp_path: Path) -> None:
    """The daemon persists device_token + last_frame_etag through save_config;
    re-loading must give back the exact same values (no quote escaping bugs)."""
    path = tmp_path / "config.toml"
    body = render_config_toml(
        transport_mode="rest",
        rest_server_url="http://srv:8765",
    )
    path.write_text(body, encoding="utf-8")
    cfg = parse_toml(path.read_text())

    updated = with_rest_updates(
        cfg,
        device_token="TOK_xyz123",
        last_frame_etag='"sha-deadbeef"',  # ETag quotes must survive
    )
    save_config(updated, path)

    reloaded = parse_toml(path.read_text())
    assert reloaded.rest.device_token == "TOK_xyz123"
    assert reloaded.rest.last_frame_etag == '"sha-deadbeef"'
    # All other fields preserved.
    assert reloaded.mqtt.device_id == cfg.mqtt.device_id


def test_save_config_atomic_writes_via_temp_file(tmp_path: Path) -> None:
    """A save leaves only the canonical file — no stray .tmp."""
    path = tmp_path / "config.toml"
    body = render_config_toml()
    path.write_text(body, encoding="utf-8")
    cfg = parse_toml(path.read_text())
    save_config(cfg, path)
    # Only the canonical file remains; no .tmp leftovers.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml"]


def test_render_from_config_round_trip() -> None:
    base = parse_toml(DEFAULT_TOML)
    cfg = Config(
        mqtt=base.mqtt,
        http=base.http,
        logging=base.logging,
        transport_mode="rest",
        rest=RestConfig(
            server_url="http://h:1",
            device_token="t",
            pairing_code="",
            last_frame_etag="e",
            poll_interval_s=120,
        ),
    )
    body = render_from_config(cfg)
    rt = parse_toml(body)
    assert rt.transport_mode == "rest"
    assert rt.rest.server_url == "http://h:1"
    assert rt.rest.device_token == "t"
    assert rt.rest.poll_interval_s == 120
