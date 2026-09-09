# mousebat — keyboard support and one tray item per device

Date: 2026-09-09
Status: design approved; not yet implemented

Extends [the original design](2026-07-31-mouse-battery-tray-design.md), which put the
keyboard out of scope.

## Problem

The MX Keys keyboard reports a real charge percentage over HID++, but nothing on the
desktop shows it. The number is lost on the way through the kernel.

Measured on the target machine at 11:31 on 2026-09-09. The kernel does create a
`power_supply` entry for the keyboard, yet it carries no percentage at all — only a
coarse level, and at that moment the level was unset:

```
$ cat /sys/class/power_supply/hidpp_battery_0/uevent
POWER_SUPPLY_NAME=hidpp_battery_0
POWER_SUPPLY_STATUS=Discharging
POWER_SUPPLY_MODEL_NAME=MX Keys Wireless Keyboard
POWER_SUPPLY_CAPACITY_LEVEL=Unknown       <- no `capacity` attribute exists
```

UPower fills the gap with a placeholder and says so itself:

```
$ upower -i /org/freedesktop/UPower/devices/battery_hidpp_battery_0
    battery-level:  unknown
    percentage:     50% (should be ignored)
```

Desktop indicators render the `50` and drop the caveat, so a fully charged keyboard
reads as half empty. UPower's own history confirms there was never a measurement to
render. Its `history-charge-Logitech_MX_Keys-*.dat`, covering 2026-09-02 to 2026-09-09,
held 159 samples spanning three distinct values:

| value | samples | meaning |
|---|---|---|
| 10% | 4 | the `Low` level bucket |
| 55% | 75 | the `Normal` level bucket — inferred: neither `Low` nor the placeholder |
| 50% | 80 | the "no data" placeholder, per the `should be ignored` flag above |

Meanwhile the device itself answers precisely. Probed with `tools/spike_probe.py`
through the Unifying receiver:

```
/dev/hidraw4  (PID 0xC52B)
  index 6: HID++ 4.5
    name: 'MX Keys Wireless Keyboard', type: 0x00 (other)
    0x1004 UNIFIED_BATTERY: unsupported
    0x1000 BATTERY_STATUS: index 7
      raw params: 64 32 00
    RESULT: 100% — discharging (via 0x1000)
```

`0x64` is 100 — the discharge level in percent. `0x32` is the *next* discharge level,
not the current charge. `0x00` is the status byte: discharging, meaning off the charger
and running on its own battery, which at 100% is exactly the expected state. The
`(other)` next to the type is the probe's own wording: it classifies against the present
pointer-only filter, and `0x00` is the keyboard code.

So the chain is: the keyboard knows the exact figure, the kernel driver keeps only which
bucket it falls into, and UPower invents a number for the bucket. Reading `0x1000`
ourselves — which `battery.py` already does — skips all three losses.

Running that probe demonstrated the point from the other side. Waking the HID++ link was
enough for the kernel to finally learn the level: at 11:41 `capacity_level` read `Full`,
and UPower moved to `state: fully-charged`, `percentage: 100%` — a fourth value in a
history file that had held only three across its whole week-long span. But the `capacity` attribute still does not exist and
the `should be ignored` flag is still set — the number is a label for the bucket either
way. It is right now only because `Full` happens to coincide with the truth, where
`Normal` → 55% did not. This also means the kernel's level is only as fresh as the last
thing that woke the link, which is why polling has to be ours.

Correction to the original spec: its "Order of work" section reports that the MX Keys
answered with 50% "matching what UPower reports, which confirms the byte-level
interpretation". The match was a coincidence — UPower's 50 is the placeholder above, not
a reading. The byte-level interpretation is correct, but that observation did not
establish it.

## Scope

Show the keyboard alongside the mouse, as **two independent tray items**.

Non-goals, carried over or decided here:

- No low-battery notifications (the original spec's deliberate choice).
- No automatic rescanning: the device set is fixed at startup.
- Nothing is written to any device; HID++ reads only.
- Solaar stays out, for the reason the original spec already gives: on startup it writes
  its own settings to the device — smartshift, hi-res scroll, DPI — which is exactly
  what `logid.cfg` governs, so the two overwrite each other. It would also duplicate
  this applet. Note that sharing a hidraw node is *not* the problem: the kernel
  broadcasts incoming reports to every open descriptor, so no client steals another's
  replies, and `software_id` filtering is enough to coexist.
- The project keeps the name `mousebat` — the systemd unit, the udev rule and the
  installed autostart state all depend on it, and renaming buys nothing here.

## Design

### Discovery — `discovery.py`

Accept keyboards. `POINTER_TYPES` becomes `BATTERY_DEVICE_TYPES = {0x00, 0x03, 0x05}` —
keyboard, mouse, trackball — following the type codes of feature `0x0005` function `0x2`
already documented in the original spec.

Return every device rather than the first: `probe_mice` → `probe_devices`,
`find_first_mouse` → `find_devices()` returning a list. `MouseDevice` becomes
`HidppDevice` and gains a `kind` field — the icon needs the device type.

**Reject paired-device nodes.** `find_receivers` currently accepts any Logitech hidraw
node whose report descriptor declares report `0x11`, which includes the node the DJ
driver creates for the keyboard itself. The keyboard therefore answers twice and would
get two tray items. The two cases are distinguishable in sysfs alone: a receiver's
hidraw node hangs off a USB interface, a paired device's node hangs off another HID
device. Verified:

```
hidraw4 -> .../3-4:1.2/0003:046D:C52B.0007         parent 3-4:1.2          receiver
hidraw5 -> .../0003:046D:C52B.0007/0003:046D:408A.0008
                                                   parent 0003:046D:C52B.0007
                                                                           paired device
```

The test is whether the parent directory contains `report_descriptor`. Skipping these
nodes also removes about 7.5 seconds of startup. Indices 1–5 on the keyboard's own node
answer nothing, and `ping` retries three times before giving up, so each index costs
three 0.5 s timeouts. Measured: 1.51 s per index, 7.54 s for the five.

### Charge — `battery.py`

No changes. `_read_legacy` already reads `params[0]` as a percentage, which is what
produced the 100% above. The `0x1004` → `0x1000` fallback covers both devices: the mouse
supports `0x1004`, the keyboard only `0x1000`.

### Icon — `icon.py`

Two identical battery outlines in one panel are indistinguishable whenever the two
charges are close. `render_pixmap` selects a silhouette: keyboard, mouse, or the current
battery as the fallback.

It selects on a `Shape` enum of the icon module's own, not on a HID++ device type. This
module's contract is that it knows nothing about devices — percentage and status in,
`QIcon` out — so the device-type-to-shape mapping lives in `tray.py` instead.

Fill ratio, colour thresholds, the charging bolt and the offline dimming stay shared —
only the outline and the geometry of the fillable area differ per shape.

Draw the preview first. `tools/preview_icon.py` gains a row per shape, and the result is
judged at 22 px before the shapes are committed to. If the keyboard silhouette does not
read at panel size, fall back to distinguishing by orientation: horizontal battery for
the keyboard, vertical for the mouse.

### Tray — `tray.py`

Split into two classes.

**`DeviceItem(QObject)`** owns one device: its `Poller`, `QThread`, `QSystemTrayIcon`,
`QMenu` and poll timer. This is the present `Tray` logic narrowed to a single device
that is already known — same intervals (5 minutes online, 1 minute after a lost link),
same online/offline rendering.

**`Tray`** becomes a coordinator. At startup it runs discovery once on a short-lived
worker thread of its own — the scan blocks for seconds and must not touch the GUI thread
— and on the result creates one `DeviceItem` per device found. When nothing is found it
creates a single placeholder item, titled `mousebat` and rendered offline, because the
context menu lives on the tray icon: with no items there would be no way to quit or to
toggle autostart. The placeholder polls nothing; picking up a device later needs a
restart, per the decision above.

Each item carries its own menu. `Refresh` polls that item's device; `Start at login` and
`Quit` remain process-wide — `Autostart` is bound to one unit per process.

`NAME_WAIT_MS` goes away. It existed because polling and naming happened together, so
the icon had to be created before the name was known. Discovery now completes first, so
every item is created with its device's name already in hand. The mechanism is
unchanged: `setApplicationName` before creating each item, since Qt copies the
application name into the item's title at creation and it cannot be changed afterwards.
The application name is restored to `mousebat` once all items exist.

### Reconnection

A sleeping device or a moved receiver dims its item and speeds polling to once a minute,
as today. The item is never removed — Plasma drops a re-created tray item for good.

Reconnection does not reuse the stored `/dev/hidrawN` path: hidraw numbering can change
when a receiver is replugged. The device is located again by identity — name,
`device_index` and type — across a fresh node scan. The set of tray items does not
change either way.

### Errors

Unchanged. Any exception inside a poll is caught, drops the link, and surfaces as an
offline icon with the cause in the tooltip. A poll never kills its thread.

## Naming

Project, module, unit and udev rule keep `mousebat`. Internal identifiers stop lying:

| now | after |
|---|---|
| `MouseDevice` | `HidppDevice` |
| `find_first_mouse` | `find_devices` |
| `probe_mice` | `probe_devices` |
| `POINTER_TYPES` | `BATTERY_DEVICE_TYPES` |
| `DEVICE_TYPE_MOUSE`, `DEVICE_TYPE_TRACKBALL` | unchanged, plus `DEVICE_TYPE_KEYBOARD` |

## Testing

- `discovery` — paired-device nodes rejected against a fake `/sys` tree (the existing
  fixture style); `probe_devices` returns both a keyboard and a mouse; the type filter
  admits `0x00`, `0x03`, `0x05` and rejects others.
- `icon` — the existing pixel tests extended per shape: fill ratio tracks the
  percentage, colours change at the thresholds, the bolt appears only while charging.
- `tray` — `DeviceItem` online/offline transitions against a stubbed source; `Tray`
  creates one item per discovered device, and exactly one placeholder for an empty list.
- `battery`, `hidpp` — untouched.

No hardware required, as before: `hidpp`, `battery` and `discovery` run against a fake
transport serving bytes recorded from real hardware. The keyboard's `0x1000` reply is
recorded in this document — parameters `64 32 00` followed by zero padding to the long
report's 16 parameter bytes.

## Known limitations

- **Tray order** follows the walk order — receivers by hidraw number, then
  `device_index`. Replugging a receiver can renumber the nodes and so reorder the items.
- **A newly paired device** appears only after a restart, by the decision above.
- **Distinguishing items** rests on the silhouettes. Two devices of the same type get
  the same shape and are told apart by tooltip alone.

## Order of work

1. `discovery` — type filter, paired-node rejection, `find_devices`, the renames.
   Tests first; the probe output above is the expected shape of the result.
2. `icon` — silhouettes, preview rendered and judged at 22 px before going further.
3. `tray` — `DeviceItem` extracted from `Tray`, then `Tray` as coordinator.
4. Verify on the target machine: both items present, the keyboard showing a real
   percentage rather than 50, `logid` and the mouse item unaffected.
5. README — refresh the icon contact sheet and the description.
