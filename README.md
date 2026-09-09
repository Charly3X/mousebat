# mousebat

A tray indicator for Logitech wireless device battery levels, for KDE Plasma.

The kernel creates no `power_supply` entry at all for mice paired to a Logi Bolt
receiver, and for an MX Keys on Unifying it records only a coarse level — `Low`,
`Normal`, `Full` — which UPower turns into an invented percentage, flagged internally
as "should be ignored" and then rendered anyway. Either way Plasma's stock "Battery and
Brightness" widget cannot show the real figure. `mousebat` reads the charge itself —
over HID++ 2.0 through the receiver's `/dev/hidraw` node — and draws one tray icon per
device.

Read-only: nothing is ever written to the device, so a
[logiops](https://github.com/PixlOne/logiops) configuration (`/etc/logid.cfg`) stays
untouched. It coexists with a running `logid`, telling its own replies apart by
`software_id`.

<img src="docs/images/in-panel.png" alt="The icon in the Plasma panel, magnified" width="126">

*In the Plasma panel (magnified 6×).*

![Every icon state](docs/images/icon-states.png)

## What it shows

- An icon filled in proportion to the charge: green from 20% up, amber below 20%, red
  below 10%. The keyboard gets a nubless rounded slab and the mouse the classic
  battery, so two items side by side are told apart at a glance.
- A lightning bolt while charging, sitting in its own gap in the fill so it stays
  legible at any level. The battery body itself is never broken by it.
- Tooltip: the device name and `73% — discharging`.
- Lost link (device asleep, receiver unplugged): the icon dims and the tooltip reads
  `no connection`. Recovery is picked up automatically, and a replugged receiver is
  followed to its new `/dev/hidraw` node.
- Right-click menu: Refresh, Start at login, Quit.
- Each tray item is named after its device, so Plasma's collapsed-items list reads
  `MX Master 3S` and `MX Keys Wireless Keyboard` rather than `mousebat`.

Polling runs every 5 minutes per device, or every minute while a link is down. Each
device polls on its own thread, so one asleep does not hold up the other.

No device is hard-coded: every keyboard, mouse and trackball found on any Logitech
receiver gets its own tray item. The set is decided once at startup, so a device paired
later shows up after a restart (`systemctl --user restart mousebat`).

## Requirements

- Linux with systemd and a tray that speaks StatusNotifierItem (KDE Plasma does).
- Python 3.11+ and PyQt6 — the installer pulls `python3-pyqt6` on Debian/Ubuntu.
- A Logitech receiver speaking HID++ 2.0: Logi Bolt and Unifying both work.

Developed and verified on Debian 13 with Plasma 6 on Wayland, against an MX Master 3S
on a Logi Bolt receiver and an MX Keys on Unifying.

## Install

```sh
./install.sh
```

That is all: it installs `python3-pyqt6` if missing, the udev rule, the systemd user
unit, then starts the applet and enables autostart. Only the udev rule needs `sudo`,
and the script asks for it at that point — do not run the whole thing as root.

```sh
./install.sh --no-autostart   # install and run now, but do not start at login
./install.sh --uninstall      # remove the unit and the udev rule
./install.sh --help
```

The project may live anywhere: the installer substitutes its actual path into the unit.

The udev rule tags Logitech hidraw nodes with `uaccess`, granting access to the user
of the active local session — no groups, no root daemon.

Logs: `journalctl --user -u mousebat -f`

## Autostart

Toggle it from the tray menu: **Start at login**. The checkmark reflects the systemd
unit itself, so it stays truthful even if you change things from a terminal:

```sh
systemctl --user enable mousebat.service
systemctl --user disable mousebat.service
```

The menu entry is hidden when no unit is installed — for instance when the applet was
started by hand with `python3 -m mousebat`.

## Diagnostics

```sh
python3 tools/spike_probe.py
```

Prints every HID++ node found, the replies for indices 1–6, device names and types,
battery feature indices and the raw bytes of the charge reply. Start here if the icon
says `no connection`.

Regenerate the contact sheet at the top of this file:

```sh
QT_QPA_PLATFORM=offscreen python3 tools/preview_icon.py
```

It writes `docs/images/icon-states.png` straight from the rendering code, so the
documentation cannot drift away from what the applet actually draws. Pass a path to
write somewhere else.

## Tests

```sh
python3 -m pytest
```

No hardware required: the transport is replaced by recorded bytes and `/sys` by a
temporary directory. Icon tests are skipped when PyQt6 is not installed.

## Layout

| Module | Responsibility |
|---|---|
| `mousebat/hidpp.py` | HID++ packets, filtering foreign replies, both error schemes |
| `mousebat/discovery.py` | finding HID++ nodes and the devices behind them |
| `mousebat/battery.py` | charge via feature `0x1004`, falling back to `0x1000` |
| `mousebat/icon.py` | icon rendering, one silhouette per device kind |
| `mousebat/tray.py` | an item per device, plus the coordinator that discovers them |
| `mousebat/autostart.py` | reads and flips the unit's enablement |

Each device polls on a thread of its own: with a link lost, walking receivers and
indices takes seconds, which would freeze the interface on the main thread and would
let one sleeping device hold up the other. Those walks are serialised against each
other, though — two at once ask the same indices the same questions with the same
`software_id` and read each other's answers.

Qt freezes a tray item's title when the item is created, and re-creating the item makes
it disappear from the tray for good — both verified against Plasma. So discovery runs
first and names every device before a single icon exists. That is also why the device
set is fixed at startup: a newly paired device needs
`systemctl --user restart mousebat.service` to get an item of its own.

Design notes:
[`2026-07-31-mouse-battery-tray-design.md`](docs/superpowers/specs/2026-07-31-mouse-battery-tray-design.md)
for the original single-device applet, and
[`2026-09-09-keyboard-battery-multi-device-design.md`](docs/superpowers/specs/2026-09-09-keyboard-battery-multi-device-design.md)
for keyboard support and the tray item per device.

## License

MIT — see [LICENSE](LICENSE).
