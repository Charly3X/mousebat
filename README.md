# battary

A tray indicator for Logitech wireless mouse battery level, for KDE Plasma.

The kernel creates no `power_supply` entry for mice paired to a Logi Bolt receiver,
so Plasma's stock "Battery and Brightness" widget cannot show them. `battary` reads
the charge itself — over HID++ 2.0 through the receiver's `/dev/hidraw` node — and
draws an icon in the tray.

Read-only: nothing is ever written to the mouse, so a
[logiops](https://github.com/PixlOne/logiops) configuration (`/etc/logid.cfg`) stays
untouched. It coexists with a running `logid`, telling its own replies apart by
`software_id`.

## What it shows

- A battery icon filled in proportion to the charge; amber below 20%, red below 10%.
- A lightning bolt cut out of the fill while charging.
- Tooltip: the device name and `73% — discharging`.
- Lost link (mouse asleep, receiver unplugged): the icon dims and the tooltip reads
  `no connection`. Recovery is picked up automatically.
- Right-click menu: Refresh, Quit.

Polling runs every 5 minutes, or every minute while the link is down.

No device is hard-coded: the first pointing device on any Logitech receiver is used.

## Install

```sh
sudo apt install python3-pyqt6

sudo cp packaging/42-battary-hidraw.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=hidraw

mkdir -p ~/.config/systemd/user
cp packaging/battary.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now battary.service
```

The udev rule tags Logitech hidraw nodes with `uaccess`, granting access to the user
of the active local session — no groups, no root daemon.

The unit assumes the project lives in `~/projects/battary`; for any other location,
adjust `WorkingDirectory` and `PYTHONPATH` in `packaging/battary.service`.

Logs: `journalctl --user -u battary -f`

## Diagnostics

```sh
python3 tools/spike_probe.py
```

Prints every HID++ node found, the replies for indices 1–6, device names and types,
battery feature indices and the raw bytes of the charge reply. Start here if the icon
says `no connection`.

A contact sheet of every icon state:

```sh
QT_QPA_PLATFORM=offscreen python3 tools/preview_icon.py /tmp/preview.png
```

## Tests

```sh
python3 -m pytest
```

No hardware required: the transport is replaced by recorded bytes and `/sys` by a
temporary directory. Icon tests are skipped when PyQt6 is not installed.

## Layout

| Module | Responsibility |
|---|---|
| `battary/hidpp.py` | HID++ packets, filtering foreign replies, both error schemes |
| `battary/discovery.py` | finding HID++ nodes and the mice behind them |
| `battary/battery.py` | charge via feature `0x1004`, falling back to `0x1000` |
| `battary/icon.py` | icon rendering |
| `battary/tray.py` | icon, menu, polling on a worker thread |

Polling lives on its own thread: with the link lost, walking receivers and indices
takes seconds, which would freeze the interface on the main thread.

Design notes: [`docs/superpowers/specs/2026-07-31-mouse-battery-tray-design.md`](docs/superpowers/specs/2026-07-31-mouse-battery-tray-design.md)
