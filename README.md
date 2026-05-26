# tesserae-pi-png-client

Raspberry Pi-side daemon that subscribes to a [Tesserae](https://github.com/dmellok/tesserae)
server over MQTT, downloads PNG frames, applies the message's
rotate / scale / bg / saturation hints, and paints them onto a Pimoroni
e-ink panel via the official [`inky`](https://github.com/pimoroni/inky)
library.

This is the **PNG path** counterpart to
[`tesserae-pi-bin-client`](https://github.com/dmellok/tesserae-pi-bin-client).
Most installs only need ONE of the two.

| | tesserae-pi-bin-client | tesserae-pi-png-client (this) |
|---|---|---|
| Wire format | 4-bpp packed `.bin` | RGB `.png` |
| Quantise / dither done by | Tesserae server | inky lib, on the Pi |
| Hardware | Inky Impression (Spectra 6 / Waveshare E6) | any inky-supported panel |
| Speed per frame | fast (no PIL roundtrip) | slower (per-frame quantise) |
| Inky version pin | exact | range (`>=2.0,<3`) |

Pick the **png** client if any of the following is true:
- you have an Inky pHAT, Inky wHAT, or any Inky Impression
- you're migrating from an inky-dash v3/v4 setup
- you want the broadest possible hardware support

---

## Install

### One-shot (recommended)

```bash
git clone https://github.com/dmellok/tesserae-pi-png-client
cd tesserae-pi-png-client
./scripts/install.sh
```

Run as your normal user (NOT root, NOT via sudo). The script invokes sudo
internally for the privileged bits and runs pip in a venv owned by you.
It does, idempotently:

1. `apt-get install` the build prereqs (`python3-dev`, `build-essential`,
   `libopenjp2-7`, `libtiff6`)
2. `raspi-config nonint do_spi 0` to enable SPI
3. `usermod -aG gpio,spi $USER` for HAT access (needs a re-login to take effect)
4. Create `.venv` in the repo dir
5. `pip install -e .` — pulls `inky[rpi]`, `paho-mqtt`, `Pillow`
6. Prompt for MQTT host/port/user/pass/client-id → write
   `~/.config/tesserae-pi-png-client/config.toml` (skipped if it exists, unless
   `--reconfigure`)
7. Symlink `.venv/bin/tesserae-pi-png-client` to `/usr/local/bin/`
8. Install + enable + start the systemd service (skip with `--no-service`)

Useful flags:

```
--no-service       skip systemd unit install
--paint-test       run --paint-test after install
--skip-apt         skip apt-get
--non-interactive  never prompt — write default config if none exists
--reconfigure      overwrite existing config
--bookworm         also pip install rpi-lgpio (Pi 5 / Bookworm needs it
                   because RPi.GPIO does not work on those boards)
--user USER        user the systemd unit runs as (default: $USER)
```

### Manual (if you prefer step-by-step)

```bash
sudo apt update
sudo apt install -y python3-pip python3-dev build-essential \
                    libopenjp2-7 libtiff6
sudo raspi-config nonint do_spi 0
sudo usermod -aG gpio,spi "$USER"          # log out + back in after this

git clone https://github.com/dmellok/tesserae-pi-png-client
cd tesserae-pi-png-client
python3 -m venv .venv
.venv/bin/pip install -e .                 # pulls inky[rpi]
# Pi 5 / Bookworm only:
.venv/bin/pip install rpi-lgpio
```

### Verify hardware

Before wiring up MQTT, paint a colour-stripe test pattern to confirm
the SPI path and panel orientation:

```bash
tesserae-pi-png-client --paint-test
```

If you see a refresh and a coloured stripe pattern, the hardware path is good.
If you get `could not auto-detect inky panel`, the README troubleshooting
section below is for you.

### Configure

The first run writes `~/.config/tesserae-pi-png-client/config.toml` with
sensible defaults. Edit `mqtt.host` to point at your broker, then re-run:

```toml
[mqtt]
host = "192.168.1.10"
port = 1883
username = ""
password = ""
client_id = "pi-impression-png-1"
keepalive = 60

[http]
download_timeout_s = 30
max_frame_bytes = 16_000_000

[logging]
level = "INFO"
```

Note: there is no `[panel]` section — the panel is auto-detected from the
HAT EEPROM. If detection fails the daemon refuses to start (see
troubleshooting).

### Install as a service (if you used the manual path)

`scripts/install.sh` already does this. If you installed manually:

```bash
sudo ./scripts/install-service.sh        # uses $SUDO_USER by default
sudo journalctl -fu tesserae-pi-png-client
```

The unit runs as your user with `gpio` + `spi` group membership.

---

## MQTT contract

### Subscribe

Topic: `tesserae/pi/frame/png` (QoS 1, not retained)

Payload (all five fields required):

```json
{
  "url": "http://192.168.1.10:8000/renders/3f7a91b2c4e5d6f8.png",
  "rotate": 0,
  "scale": "fit",
  "bg": "white",
  "saturation": 0.5
}
```

| Field | Type | Meaning |
|---|---|---|
| `url` | string | HTTP URL to GET the PNG. No auth. |
| `rotate` | int 0..3 | Quarter-turns CW to apply *before* scaling. Stacks with whatever rotation the server already baked in. |
| `scale` | string | One of `fit`, `fill`, `stretch`, `center`. |
| `bg` | string | Letterbox colour when `fit`/`center` leaves bars. One of `white`, `black`, `red`, `green`, `blue`, `yellow`, `orange`. Unknown names fall back to white. |
| `saturation` | float 0.0..1.0 | Passed straight to `inky.set_image(saturation=...)`. |

### Publish

Topic: `tesserae/pi/status` (QoS 1, retained, also the LWT topic)

```json
{
  "state": "idle",
  "last_paint_at": 1734567890.123,
  "last_error": null,
  "last_digest": "3f7a91b2c4e5d6f8",
  "uptime_s": 3601,
  "fw_version": "0.1.0",
  "panel": "inky_impression_13_3"
}
```

`state` is one of `idle`, `rendering`, `error`, `offline` (LWT).
Heartbeat: republished on every state change and at least every 60 s.

---

## Transform pipeline

For each arriving frame:

1. Download the PNG via plain HTTP (size capped at `max_frame_bytes`).
2. Decode with PIL, coerce to mode `RGB`.
3. Rotate by `rotate * 90°` clockwise (`expand=True` so nothing is cropped).
4. Scale per `scale` mode:
   - `fit` — preserve aspect, letterbox with `bg` colour
   - `fill` — preserve aspect, crop to cover
   - `stretch` — `resize` straight to panel size, distorts
   - `center` — paste at native size, letterbox, crop overflow
5. Hand to `panel.set_image(img, saturation=...)`, then `panel.show()`.

---

## Troubleshooting

**`could not auto-detect inky panel`**
- `sudo raspi-config nonint do_spi 0` — enable SPI
- `ls /proc/device-tree/hat` — confirm the HAT EEPROM is readable
- check your user is in `gpio` and `spi` groups: `groups`
- reboot after enabling SPI

**Frame never paints, state stays `idle`**
- Is the broker reachable from the Pi? `mosquitto_sub -h <broker> -t '#'`
- Is the URL reachable from the Pi? `curl -I <url>`
- Tail the journal: `sudo journalctl -fu tesserae-pi-png-client`

**`state: error` with `last_error` ending in `URLError` / `TimeoutError`**
- The Tesserae server's render directory is unreachable from the Pi —
  check firewall, port, and that the server is actually serving on the
  URL the message advertised.

**Colours look washed out / oversaturated**
- Tune the `saturation` field on the Tesserae server side. The Pi just
  passes whatever value arrives straight to `inky.set_image()`.

---

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check src tests
mypy src
```

The `paho-mqtt` and `Pillow` deps install everywhere; `inky[rpi]` is
Linux-only and won't install on macOS — that's fine, `paint.py` lazy-
imports it so the unit tests run on any host.

### Layout

```
src/tesserae_pi_png_client/
  transforms.py    # pure rotate/scale/bg — fully tested
  config.py        # TOML load + defaults
  paint.py         # inky wrapper (lazy-imported)
  mqtt_loop.py     # paho client + frame dispatcher
  heartbeat.py     # retained status + LWT
  main.py          # CLI entry point, signal handlers
```

License: MIT.
