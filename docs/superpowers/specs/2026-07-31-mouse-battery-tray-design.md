# mousebat — wireless mouse battery tray indicator

Date: 2026-07-31
Status: design approved; implemented and verified on the target machine

## Problem

Show the battery level of the connected Logitech wireless mouse in the KDE Plasma
system tray.

Plasma's stock "Battery and Brightness" widget cannot do this because the data does
not exist: the kernel creates a `power_supply` entry only for the MX Keys (paired to
a Unifying receiver), not for the MX Master 3S on Logi Bolt. Verified on the target
machine:

```
$ upower -e
/org/freedesktop/UPower/devices/battery_hidpp_battery_0   <- MX Keys
/org/freedesktop/UPower/devices/headset_dev_2C_4D_79_B6_DA_6F
/org/freedesktop/UPower/devices/DisplayDevice

$ ls /sys/class/power_supply/
hidpp_battery_0                                           <- MX Keys
```

So the substance of the work is obtaining the charge over HID++ directly. The icon is
a layer on top.

## Target environment

- Debian 13 (trixie), kernel 6.12
- KDE Plasma 6, Wayland
- Mouse: Logitech MX Master 3S via Logi Bolt (`046d:c548`)
- Keyboard: Logitech MX Keys via Unifying (`046d:c52b`) — out of scope
- `logid` (logiops) running, with mouse settings in `/etc/logid.cfg`
- Python 3, PyQt6 from the Debian archive (`python3-pyqt6`)

## Constraints

1. **Solaar is not used.** On startup it applies its own settings to the device
   (smartshift, hi-res scroll, DPI) — exactly what `logid.cfg` already governs, so the
   two would overwrite each other.
2. **Nothing is written to the mouse.** HID++ reads only; the `logid` configuration is
   untouchable.
3. **No root at runtime.** The single `sudo` action is a one-off udev rule install.

## Reading the charge

### Transport

Requests go to the **receiver's** `/dev/hidraw` node rather than the mouse's own:
paired devices are addressed by the `device_index` field inside the packet.

Short report, 7 bytes:

| byte | meaning |
|---|---|
| 0 | `0x10` (report id) |
| 1 | `device_index`: `1..6` for a paired device, `0xFF` for the receiver itself |
| 2 | `feature_index` |
| 3 | `(function << 4) \| software_id` |
| 4–6 | parameters |

A long report is the same with report id `0x11` and 20 bytes (parameters in bytes
4–19). A reply may arrive as either form.

Errors: HID++ 2.0 uses report id `0xFF` —
`0xFF, index, feature_index, function|sw, error_code`; HID++ 1.0 uses
`0x10, index, 0x8F, sub_id, function|sw, error_code`. Both are recognised and turned
into exceptions.

### Coexisting with logid

`logid` holds the same hidraw node open. The kernel broadcasts incoming HID reports to
**every** open descriptor, so neither side steals the other's replies — but we do
receive foreign replies as well.

The remedy: our own `software_id` — fixed at `0x0E` (4 bits, `1..15` allowed) — and we
discard anything whose `software_id`, `device_index` or `feature_index` does not match
the request in flight. This is a precondition for working alongside `logid`.

### Request sequence

1. **Ping** — root feature `0x0000`, function `0x1`, third parameter `0xAA` as an echo
   marker. The reply carries the protocol version and the echo. This is how live
   `device_index` values are identified.
2. **`getFeature`** — root feature `0x0000`, function `0x0`, parameters = the wanted
   feature id. Reply `params[0]` is the feature index; `0` means unsupported.
3. **Device type** — feature `0x0005` (DEVICE_NAME), function `0x2` (`getDeviceType`).
   `params[0]`: `0` keyboard, `3` mouse, `5` trackball, `7` receiver. We accept `3` and `5`.
4. **Name** — feature `0x0005`, function `0x1` (`getDeviceName`) at an offset, joining
   16-byte ASCII chunks up to the length from function `0x0` (`getDeviceNameCount`).
5. **Charge** — feature `0x1004` (UNIFIED_BATTERY), function `0x1` (`getStatus`):
   - `params[0]` — state of charge (0–100)
   - `params[2]` — charging status: `0` discharging, `1` charging, `2` slow charging,
     `3` complete, `4` error

   When `0x1004` is absent, fall back to `0x1000` (BATTERY_STATUS), function `0x0`:
   `params[0]` — discrete level as a percentage, `params[2]` — status.

One request times out after 500 ms. Discovery pings each index up to 3 times, since a
sleeping mouse may miss the first one.

### Finding the receivers' hidraw nodes

Walk `/sys/class/hidraw/hidraw*`:

- `device/uevent` → `HID_ID` starting with `0003:0000046D` (Logitech vendor);
- `device/report_descriptor` declaring report id `0x11` (bytes `85 11`) — the mark of a
  HID++ interface as opposed to the receiver's plain mouse/keyboard interfaces.

Candidates are then pinged; whichever answers is the one to use.

### Device access

`/etc/udev/rules.d/42-mousebat-hidraw.rules`:

```
ACTION=="add|change", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="046d", TAG+="uaccess"
```

`ATTRS{}` matches parent devices too, so the receiver's USB vendor id is visible from
the hidraw node. `TAG+="uaccess"` hands access to the user of the active local session
via systemd-logind — no groups, no root daemon.

Both `add` and `change` are matched: with `add` alone the rule does not apply to
already-connected devices on `udevadm trigger`, and access would only appear after
physically replugging the receiver.

Installed once, by hand:

```
sudo cp packaging/42-mousebat-hidraw.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=hidraw
```

## Components

| Module | Responsibility | Depends on |
|---|---|---|
| `hidpp.py` | transport: open hidraw, send a request, await a reply bearing our `software_id`, timeout, error decoding. Knows nothing about batteries | `os` |
| `discovery.py` | find receivers' hidraw nodes, ping indices `1..6`, keep devices typed as mouse/trackball | `hidpp` |
| `battery.py` | produce `BatteryReading(percent, status, name)`: resolve the feature index, read the charge, fall back to `0x1000` when `0x1004` is absent | `hidpp` |
| `icon.py` | render a `QIcon` battery from percentage and status | PyQt6 |
| `tray.py` | `QSystemTrayIcon`, poll timer, context menu, the lost-link state | everything above |
| `main.py` | entry point: `QApplication`, start the tray | `tray` |

Boundaries: `hidpp` knows nothing of batteries, `battery` nothing of Qt, `icon` nothing
of devices. Each module is testable without the others.

## Behaviour

- **Icon** 22×22: a battery outline filled in proportion to the charge. The theme's
  regular colour, turning **amber below 20%** and **red below 10%**. While charging, a
  lightning bolt sits over the fill.
- **Tooltip**: two lines — the device name and `73% — discharging`.
- **Polling** every 5 minutes: the charge drifts slowly, and extra requests wake the
  mouse.
- **Context menu**: Refresh (poll immediately), Quit.
- **Lost link** (mouse asleep, receiver unplugged, device silent): the icon dims and the
  tooltip reads `no connection`. Polling speeds up to once a minute so recovery is
  noticed sooner. No restart needed.
- **Multiple mice**: the first in walk order (receivers by hidraw number, then by
  `device_index`). No device is hard-coded: swapping mice needs no code change.
- **No low-battery notifications** — a deliberate decision.

The device name and feature indices are cached for the lifetime of the link and dropped
when the link is lost.

## Testing

- `hidpp`, `battery`, `discovery` — against a fake transport: a file-like object serving
  bytes recorded from real hardware. Covered: packet assembly, `software_id` filtering
  (including discarding a foreign reply), timeouts, error decoding for both protocols,
  the `0x1004` → `0x1000` fallback. No hardware required.
- `discovery` — against a fake `/sys` tree in a temporary directory.
- `icon` — render into a `QImage` and inspect pixels: the fill ratio tracks the
  percentage, the colour changes at the thresholds, the bolt appears only while charging.
- `tray` — state transitions (online / offline) against a stubbed data source, with no
  real polling.

## Order of work

The first step was a spike retiring the main risk: whether the mouse answers HID++
alongside a running `logid`. A minimal script installed the udev rule, pinged indices
`1..6` on both receivers, printed the raw replies and read `0x1004`.

Outcome: it does. The MX Master 3S answered on `/dev/hidraw2`, index 2, HID++ 4.5, with
`0x1004` at feature index 8 and raw parameters `05 01 00` — 5%, critical level,
discharging. The MX Keys answered through `0x1000` with 50%, matching what UPower
reports, which confirms the byte-level interpretation. Filtering by `software_id` proved
sufficient to coexist with `logid`.

The modules then followed bottom-up: `hidpp` → `battery` → `discovery` → `icon` →
`tray`, each with tests, finishing with a `systemd --user` unit bound to
`graphical-session.target` and logs via `journalctl --user -u mousebat`.
