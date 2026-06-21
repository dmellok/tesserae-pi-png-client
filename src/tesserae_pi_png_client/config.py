from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "tesserae-pi-png-client" / "config.toml"

DEFAULT_DEVICE_ID = "pi_png"

# Matches the instance-id grammar the Tesserae server accepts: lowercase,
# 2-32 chars, must start with a letter. The device_id becomes the MQTT topic
# prefix, so it has to be broker-path-safe.
_DEVICE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

TRANSPORT_MODES = {"mqtt", "rest"}


def _toml_str(value: str) -> str:
    """Render a TOML basic-string literal with the bare-minimum escaping."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_config_toml(
    mqtt_host: str = "192.168.1.10",
    mqtt_port: int = 1883,
    mqtt_username: str = "",
    mqtt_password: str = "",
    mqtt_client_id: str = "pi-impression-png-1",
    device_id: str = DEFAULT_DEVICE_ID,
    mqtt_keepalive: int = 60,
    download_timeout_s: int = 30,
    max_frame_bytes: int = 16_000_000,
    log_level: str = "INFO",
    transport_mode: str = "rest",
    rest_server_url: str = "http://tesserae.local:8765",
    rest_device_token: str = "",
    rest_pairing_code: str = "",
    rest_last_frame_etag: str = "",
    rest_poll_interval_s: int = 60,
) -> str:
    """Build a config.toml body. No-args == DEFAULT_TOML.

    There is intentionally no [panel] section — inky.auto() picks the panel
    up from the HAT EEPROM. If detection fails we want the client to surface
    that loudly at startup rather than paint onto whatever the config guessed.
    """
    return (
        f"transport_mode = {_toml_str(transport_mode)}  # mqtt | rest\n"
        "\n"
        "[mqtt]\n"
        f"host = {_toml_str(mqtt_host)}\n"
        f"port = {mqtt_port}\n"
        f"username = {_toml_str(mqtt_username)}\n"
        f"password = {_toml_str(mqtt_password)}\n"
        f"client_id = {_toml_str(mqtt_client_id)}\n"
        f"device_id = {_toml_str(device_id)}  # MQTT topic prefix\n"
        f"keepalive = {mqtt_keepalive}\n"
        "\n"
        "[rest]\n"
        f"server_url = {_toml_str(rest_server_url)}"
        "      # e.g. http://tesserae.local:8765 (rest mode only)\n"
        f"device_token = {_toml_str(rest_device_token)}"
        "    # auto-populated after pair/discover; do not edit by hand\n"
        f"pairing_code = {_toml_str(rest_pairing_code)}"
        "    # single-use; wiped after first successful register\n"
        f"last_frame_etag = {_toml_str(rest_last_frame_etag)}"
        " # auto-populated for If-None-Match short-circuit\n"
        f"poll_interval_s = {rest_poll_interval_s}"
        "          # fallback wake interval if server omits next_poll_s\n"
        "\n"
        "[http]\n"
        f"download_timeout_s = {download_timeout_s}\n"
        f"max_frame_bytes = {max_frame_bytes}\n"
        "\n"
        "[logging]\n"
        f"level = {_toml_str(log_level)}\n"
    )


DEFAULT_TOML = render_config_toml()


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    username: str
    password: str
    client_id: str
    keepalive: int
    device_id: str = DEFAULT_DEVICE_ID


@dataclass(frozen=True)
class HttpConfig:
    download_timeout_s: int
    max_frame_bytes: int


@dataclass(frozen=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True)
class RestConfig:
    """REST-transport state. Most fields are runtime-managed: device_token
    and last_frame_etag are written back by the daemon, pairing_code is
    single-use and wiped after a successful register. Only server_url and
    poll_interval_s are user-facing defaults."""
    server_url: str = ""
    device_token: str = ""
    pairing_code: str = ""
    last_frame_etag: str = ""
    poll_interval_s: int = 60


@dataclass(frozen=True)
class Config:
    mqtt: MqttConfig
    http: HttpConfig
    logging: LoggingConfig
    transport_mode: str = "mqtt"
    rest: RestConfig = RestConfig()


def _require(section: dict[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in section:
        raise ValueError(f"missing [{where}].{key}")
    value = section[key]
    # bool is a subclass of int in Python, but a config file with `port = true`
    # is clearly bogus — reject it.
    if kind is int and isinstance(value, bool):
        raise ValueError(
            f"[{where}].{key} must be int, got bool"
        )
    if not isinstance(value, kind):
        raise ValueError(
            f"[{where}].{key} must be {kind.__name__}, got {type(value).__name__}"
        )
    return value


def _parse(raw: dict[str, Any]) -> Config:
    mqtt_section = raw.get("mqtt", {})
    if not isinstance(mqtt_section, dict):
        raise ValueError("[mqtt] must be a table")
    device_id = mqtt_section.get("device_id", DEFAULT_DEVICE_ID)
    if not isinstance(device_id, str):
        raise ValueError(
            f"[mqtt].device_id must be str, got {type(device_id).__name__}"
        )
    if not _DEVICE_ID_RE.match(device_id):
        raise ValueError(
            f"[mqtt].device_id must match {_DEVICE_ID_RE.pattern!r} "
            f"(lowercase, 2-32 chars, starts with a letter), got {device_id!r}"
        )
    mqtt = MqttConfig(
        host=_require(mqtt_section, "host", str, "mqtt"),
        port=_require(mqtt_section, "port", int, "mqtt"),
        username=mqtt_section.get("username", ""),
        password=mqtt_section.get("password", ""),
        client_id=_require(mqtt_section, "client_id", str, "mqtt"),
        keepalive=_require(mqtt_section, "keepalive", int, "mqtt"),
        device_id=device_id,
    )
    if not 1 <= mqtt.port <= 65535:
        raise ValueError(f"[mqtt].port out of range: {mqtt.port}")
    if mqtt.keepalive <= 0:
        raise ValueError(f"[mqtt].keepalive must be positive, got {mqtt.keepalive}")

    http_section = raw.get("http", {})
    if not isinstance(http_section, dict):
        raise ValueError("[http] must be a table")
    http = HttpConfig(
        download_timeout_s=_require(http_section, "download_timeout_s", int, "http"),
        max_frame_bytes=_require(http_section, "max_frame_bytes", int, "http"),
    )
    if http.download_timeout_s <= 0:
        raise ValueError("[http].download_timeout_s must be positive")
    if http.max_frame_bytes <= 0:
        raise ValueError("[http].max_frame_bytes must be positive")

    log_section = raw.get("logging", {})
    if not isinstance(log_section, dict):
        raise ValueError("[logging] must be a table")
    logging_cfg = LoggingConfig(level=log_section.get("level", "INFO"))
    if logging_cfg.level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"[logging].level unknown: {logging_cfg.level!r}")

    # transport_mode is a top-level key so it can switch out the whole
    # transport without touching the section it belongs to. Default "mqtt"
    # preserves existing behaviour for configs predating the REST split.
    transport_mode = raw.get("transport_mode", "mqtt")
    if not isinstance(transport_mode, str):
        raise ValueError(
            f"transport_mode must be str, got {type(transport_mode).__name__}"
        )
    if transport_mode not in TRANSPORT_MODES:
        raise ValueError(
            f"transport_mode unknown: {transport_mode!r} "
            f"(expected one of {sorted(TRANSPORT_MODES)})"
        )

    rest_section = raw.get("rest", {})
    if not isinstance(rest_section, dict):
        raise ValueError("[rest] must be a table")
    rest = RestConfig(
        server_url=rest_section.get("server_url", ""),
        device_token=rest_section.get("device_token", ""),
        pairing_code=rest_section.get("pairing_code", ""),
        last_frame_etag=rest_section.get("last_frame_etag", ""),
        poll_interval_s=rest_section.get("poll_interval_s", 60),
    )
    for name, value in (
        ("server_url", rest.server_url),
        ("device_token", rest.device_token),
        ("pairing_code", rest.pairing_code),
        ("last_frame_etag", rest.last_frame_etag),
    ):
        if not isinstance(value, str):
            raise ValueError(
                f"[rest].{name} must be str, got {type(value).__name__}"
            )
    if not isinstance(rest.poll_interval_s, int) or rest.poll_interval_s <= 0:
        raise ValueError("[rest].poll_interval_s must be a positive integer")
    if transport_mode == "rest" and not rest.server_url:
        raise ValueError(
            "[rest].server_url is required when transport_mode = 'rest' "
            "(e.g. server_url = \"http://tesserae.local:8765\")"
        )

    return Config(
        mqtt=mqtt,
        http=http,
        logging=logging_cfg,
        transport_mode=transport_mode,
        rest=rest,
    )


def parse_toml(text: str) -> Config:
    return _parse(tomllib.loads(text))


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_TOML, encoding="utf-8")
    return parse_toml(path.read_text(encoding="utf-8"))


def render_from_config(cfg: Config) -> str:
    """Re-render a Config back to TOML using the canonical layout.

    Used by save_config() when the daemon writes back runtime state
    (device_token after first pair, last_frame_etag after each paint).
    Round-trips every field; user comments are not preserved.
    """
    return render_config_toml(
        mqtt_host=cfg.mqtt.host,
        mqtt_port=cfg.mqtt.port,
        mqtt_username=cfg.mqtt.username,
        mqtt_password=cfg.mqtt.password,
        mqtt_client_id=cfg.mqtt.client_id,
        device_id=cfg.mqtt.device_id,
        mqtt_keepalive=cfg.mqtt.keepalive,
        download_timeout_s=cfg.http.download_timeout_s,
        max_frame_bytes=cfg.http.max_frame_bytes,
        log_level=cfg.logging.level,
        transport_mode=cfg.transport_mode,
        rest_server_url=cfg.rest.server_url,
        rest_device_token=cfg.rest.device_token,
        rest_pairing_code=cfg.rest.pairing_code,
        rest_last_frame_etag=cfg.rest.last_frame_etag,
        rest_poll_interval_s=cfg.rest.poll_interval_s,
    )


def save_config(cfg: Config, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Atomically persist Config back to disk (temp file + rename).

    The REST loop calls this after first-pair (to save device_token) and
    after each successful paint (to save last_frame_etag). Atomic write
    means a power-cut mid-write leaves the previous config intact rather
    than a truncated half-written file.
    """
    body = render_from_config(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same directory so the rename is atomic
    # within a single filesystem.
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def with_rest_updates(cfg: Config, **rest_fields: Any) -> Config:
    """Return a new Config with the named RestConfig fields replaced."""
    return replace(cfg, rest=replace(cfg.rest, **rest_fields))


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DEVICE_ID",
    "DEFAULT_TOML",
    "TRANSPORT_MODES",
    "Config",
    "HttpConfig",
    "LoggingConfig",
    "MqttConfig",
    "RestConfig",
    "load_config",
    "parse_toml",
    "render_config_toml",
    "render_from_config",
    "save_config",
    "with_rest_updates",
]
