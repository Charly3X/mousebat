# Keyboard Support and One Tray Item per Device — Implementation Plan

**Status: executed on 2026-09-09.** All seven tasks are done and verified on the target
machine; the checkboxes below are left as written rather than ticked, since the record
that matters is the commit series on `multi-device-tray`. Two things went differently
from the plan and are noted inline: `tools/spike_probe.py` also used the renamed
symbols (Task 1), and verification caught a concurrency bug that changed the
reconnection design (Task 7, and the spec's Reconnection section).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the MX Keys keyboard's real battery percentage next to the mouse, as two independent KDE tray items.

**Architecture:** `discovery` stops filtering out non-pointing devices and returns every battery-bearing device instead of the first, rejecting the duplicate hidraw nodes that paired devices expose. `icon` gains a shape parameter so each device draws a recognisable silhouette. `tray` splits into a `DeviceItem` owning one device's icon, menu, worker thread and timer, and a `Tray` that discovers devices once at startup and creates one item per device.

**Tech Stack:** Python 3.11+, PyQt6, pytest. No new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-09-keyboard-battery-multi-device-design.md`. Read it before starting.
- Branch: `multi-device-tray`, already created with the spec committed.
- Run tests with `./.venv/bin/python -m pytest`. Never `python3 -m pytest` — PyQt6 resolution differs.
- HID++ reads only. Nothing is ever written to a device.
- The project name `mousebat` does not change: not the module, the systemd unit, the udev rule, nor `pyproject.toml`.
- No low-battery notifications. No automatic rescanning. The device set is fixed at startup.
- Tray items are never removed once created — Plasma drops a re-created item permanently.
- `icon.py` must not import `discovery`, and must not learn HID++ device-type codes. It takes its own `Shape` enum; the device-type-to-shape mapping lives in `tray.py`.
- Python style follows the existing modules: `from __future__ import annotations`, keyword-only flags, docstrings that explain *why*, lines under 100 characters.

---

### Task 1: Discovery accepts keyboards and returns every device

**Files:**
- Modify: `mousebat/discovery.py`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `discovery.DEVICE_TYPE_KEYBOARD = 0x00`
  - `discovery.BATTERY_DEVICE_TYPES: frozenset[int]` — `{0x00, 0x03, 0x05}`
  - `discovery.HidppDevice(device_path: str, device_index: int, name: str, protocol: tuple[int, int], kind: int)` — frozen dataclass, replacing `MouseDevice`
  - `discovery.probe_devices(link: hidpp.Link, device_path: str) -> list[HidppDevice]` — replacing `probe_mice`
  - `discovery.find_devices(sys_hidraw: str = SYS_HIDRAW, dev_root: str = "/dev", *, timeout: float = 0.5) -> list[HidppDevice]` — replacing `find_first_mouse`
  - `discovery.relocate(device: HidppDevice, sys_hidraw: str = SYS_HIDRAW, dev_root: str = "/dev", *, timeout: float = 0.5) -> HidppDevice | None`

- [ ] **Step 1: Write the failing tests**

Replace the `TestProbeMice` and `TestFindFirstMouse` classes in `tests/test_discovery.py` with the following. Keep the module's existing imports and helpers (`make_hidraw`, `pong`, `name_feature_reply`, `type_reply`, `name_count_reply`, `name_chunk_reply`) exactly as they are.

```python
class TestProbeDevices:
    def test_returns_both_keyboard_and_mouse(self) -> None:
        replies: list[bytes | None] = [
            # index 1 — the keyboard, previously discarded
            pong(1),
            name_feature_reply(1),
            type_reply(1, discovery.DEVICE_TYPE_KEYBOARD),
            name_count_reply(1, 7),
            name_chunk_reply(1, b"MX Keys"),
            # index 2 — the mouse
            pong(2),
            name_feature_reply(2),
            type_reply(2, discovery.DEVICE_TYPE_MOUSE),
            name_count_reply(2, 12),
            name_chunk_reply(2, b"MX Master 3S"),
        ]
        replies += [None] * 12  # indices 3..6 stay silent, three ping attempts each
        link, _ = link_with(*replies)

        found = discovery.probe_devices(link, "/dev/hidraw4")
        assert [(device.device_index, device.name, device.kind) for device in found] == [
            (1, "MX Keys", discovery.DEVICE_TYPE_KEYBOARD),
            (2, "MX Master 3S", discovery.DEVICE_TYPE_MOUSE),
        ]

    def test_trackball_is_accepted(self) -> None:
        replies: list[bytes | None] = [
            pong(1),
            name_feature_reply(1),
            type_reply(1, discovery.DEVICE_TYPE_TRACKBALL),
            name_count_reply(1, 9),
            name_chunk_reply(1, b"Trackball"),
        ]
        replies += [None] * 18
        link, _ = link_with(*replies)
        assert [device.device_index for device in discovery.probe_devices(link, "/dev/x")] == [1]

    def test_receiver_type_is_rejected(self) -> None:
        """Type 0x07 is the receiver answering about itself — it has no battery."""
        replies: list[bytes | None] = [pong(1), name_feature_reply(1), type_reply(1, 0x07)]
        replies += [None] * 18
        link, _ = link_with(*replies)
        assert discovery.probe_devices(link, "/dev/x") == []

    def test_silent_receiver_yields_nothing(self) -> None:
        link, _ = link_with(*([None] * 18))
        assert discovery.probe_devices(link, "/dev/hidraw2") == []

    def test_device_without_name_feature_is_skipped(self) -> None:
        replies: list[bytes | None] = [pong(1), name_feature_reply(1, feature=0x00)]
        replies += [None] * 18
        link, _ = link_with(*replies)
        assert discovery.probe_devices(link, "/dev/hidraw2") == []


def fake_devices(path: str) -> list[discovery.HidppDevice]:
    """Two devices behind hidraw4, none anywhere else."""
    if not path.endswith("hidraw4"):
        return []
    return [
        discovery.HidppDevice(path, 6, "MX Keys", (4, 5), discovery.DEVICE_TYPE_KEYBOARD),
        discovery.HidppDevice(path, 2, "MX Master 3S", (4, 5), discovery.DEVICE_TYPE_MOUSE),
    ]


class TestFindDevices:
    def test_collects_across_every_receiver(self, tmp_path, monkeypatch) -> None:
        root = str(tmp_path)
        make_hidraw(root, "hidraw2", "0003:0000046D:0000C548", HIDPP_DESCRIPTOR)
        make_hidraw(root, "hidraw4", "0003:0000046D:0000C52B", HIDPP_DESCRIPTOR)

        opened: list[str] = []

        def fake_transport(path: str) -> FakeTransport:
            opened.append(path)
            return FakeTransport()

        monkeypatch.setattr(discovery.hidpp, "Transport", fake_transport)
        monkeypatch.setattr(discovery, "probe_devices", lambda link, path: fake_devices(path))

        found = discovery.find_devices(root, dev_root="/dev")
        assert [device.name for device in found] == ["MX Keys", "MX Master 3S"]
        # Every receiver is visited: unlike before, we do not stop at the first hit.
        assert opened == ["/dev/hidraw2", "/dev/hidraw4"]

    def test_returns_empty_when_nothing_found(self, tmp_path) -> None:
        assert discovery.find_devices(str(tmp_path)) == []


class TestRelocate:
    def test_finds_the_same_device_at_a_new_path(self, tmp_path, monkeypatch) -> None:
        """hidraw numbering changes when a receiver is replugged; identity does not."""
        root = str(tmp_path)
        make_hidraw(root, "hidraw4", "0003:0000046D:0000C52B", HIDPP_DESCRIPTOR)
        monkeypatch.setattr(discovery.hidpp, "Transport", lambda path: FakeTransport())
        monkeypatch.setattr(discovery, "probe_devices", lambda link, path: fake_devices(path))

        stale = discovery.HidppDevice(
            "/dev/hidraw9", 6, "MX Keys", (4, 5), discovery.DEVICE_TYPE_KEYBOARD
        )
        found = discovery.relocate(stale, root, dev_root="/dev")
        assert found is not None
        assert found.device_path == "/dev/hidraw4"

    def test_returns_none_when_the_device_is_gone(self, tmp_path, monkeypatch) -> None:
        root = str(tmp_path)
        make_hidraw(root, "hidraw4", "0003:0000046D:0000C52B", HIDPP_DESCRIPTOR)
        monkeypatch.setattr(discovery.hidpp, "Transport", lambda path: FakeTransport())
        monkeypatch.setattr(discovery, "probe_devices", lambda link, path: [])

        stale = discovery.HidppDevice(
            "/dev/hidraw9", 6, "MX Keys", (4, 5), discovery.DEVICE_TYPE_KEYBOARD
        )
        assert discovery.relocate(stale, root, dev_root="/dev") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_discovery.py -q`
Expected: FAIL with `AttributeError: module 'mousebat.discovery' has no attribute 'DEVICE_TYPE_KEYBOARD'` (and similar for `probe_devices`, `find_devices`, `relocate`, `HidppDevice`).

- [ ] **Step 3: Rewrite the device half of `discovery.py`**

In `mousebat/discovery.py`, replace the type constants:

```python
DEVICE_TYPE_KEYBOARD = 0x00
DEVICE_TYPE_MOUSE = 0x03
DEVICE_TYPE_TRACKBALL = 0x05

#: Device types worth a tray item. A receiver (0x07) has no battery of its own.
BATTERY_DEVICE_TYPES = frozenset(
    {DEVICE_TYPE_KEYBOARD, DEVICE_TYPE_MOUSE, DEVICE_TYPE_TRACKBALL}
)
```

Replace the `MouseDevice` dataclass:

```python
@dataclass(frozen=True)
class HidppDevice:
    """A battery-bearing device found behind a receiver."""

    device_path: str
    device_index: int
    name: str
    protocol: tuple[int, int]
    kind: int
```

Replace `probe_mice` with `probe_devices` — the body is the old one with the filter and the names widened:

```python
def probe_devices(link: hidpp.Link, device_path: str) -> list[HidppDevice]:
    """Walk indices 1..6 and return every device that carries a battery."""
    devices: list[HidppDevice] = []
    for index in DEVICE_INDICES:
        try:
            protocol = link.ping(index)
        except (hidpp.HidppTimeout, hidpp.HidppError, hidpp.DeviceNotConnected):
            continue
        try:
            name_feature = link.feature_index(index, hidpp.FEATURE_DEVICE_NAME)
            if name_feature is None:
                continue
            kind = device_type(link, index, name_feature)
            if kind not in BATTERY_DEVICE_TYPES:
                continue
            name = device_name(link, index, name_feature)
        except (hidpp.HidppTimeout, hidpp.HidppError, hidpp.DeviceNotConnected):
            continue
        devices.append(
            HidppDevice(
                device_path=device_path,
                device_index=index,
                name=name or "Device",
                protocol=protocol,
                kind=kind,
            )
        )
    return devices
```

Replace `find_first_mouse` with `find_devices`, which no longer stops at the first hit:

```python
def find_devices(
    sys_hidraw: str = SYS_HIDRAW, dev_root: str = "/dev", *, timeout: float = 0.5
) -> list[HidppDevice]:
    """Every device behind every receiver, in walk order.

    Receivers by hidraw number, then by `device_index` — the order the tray items
    will end up in.
    """
    devices: list[HidppDevice] = []
    for receiver in find_receivers(sys_hidraw, dev_root):
        try:
            with hidpp.Transport(receiver.device_path) as transport:
                link = hidpp.Link(transport, timeout=timeout)
                devices.extend(probe_devices(link, receiver.device_path))
        except OSError:
            continue
    return devices


def relocate(
    device: HidppDevice,
    sys_hidraw: str = SYS_HIDRAW,
    dev_root: str = "/dev",
    *,
    timeout: float = 0.5,
) -> HidppDevice | None:
    """Find a known device again, wherever its hidraw node has moved to.

    Replugging a receiver renumbers the nodes, so a stored path goes stale while the
    device itself is unchanged. Identity is what survives: name, index and type.
    """
    identity = (device.name, device.device_index, device.kind)
    for candidate in find_devices(sys_hidraw, dev_root, timeout=timeout):
        if (candidate.name, candidate.device_index, candidate.kind) == identity:
            return candidate
    return None
```

Also update the module docstring's first line — it says "and the mice paired with them":

```python
"""Locating Logitech receivers and the battery-bearing devices paired with them.
```

`POINTER_TYPES` goes away, and `tools/spike_probe.py` used it — at line 40 for its
`pointing device` / `other` label, and at line 103 for `find_first_mouse`. Update the
tool in this task: it is the verification instrument for Task 7 and must not be left
broken. Print the type by name from a `TYPE_NAMES` mapping and say whether the type is
one we accept, which also retires the misleading `(other)` next to the keyboard. Loop
over `find_devices()` instead of reading a single mouse, and wrap each read in
`try/except`, pinging first: a device that dozed off during the walk answers the
retrying `ping` but not a single-shot feature lookup, and a diagnostic tool must report
that rather than crash.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_discovery.py -q`
Expected: PASS.

Then run the whole suite: `./.venv/bin/python -m pytest`
Expected: PASS, all of it — which is a trap worth naming. `tray.py` still refers to
`discovery.find_first_mouse` and `discovery.MouseDevice`, but only inside
`Poller._connect` and an annotation, and the existing tray tests drive `_apply` directly
without ever polling. Python resolves the attribute at call time, so the breakage is
latent: green tests, and the applet would raise `AttributeError` the moment it polled.
Do not try to fix it here — Task 5 replaces that code wholesale. Just do not mistake the
green suite for a working applet.

Confirm the latent break is the only one left:

```bash
grep -rn "find_first_mouse\|probe_mice\|MouseDevice\|POINTER_TYPES" --include="*.py" \
  mousebat/ tools/ tests/
```

Expected: two hits, both in `mousebat/tray.py`.

- [ ] **Step 5: Commit**

```bash
git add mousebat/discovery.py tests/test_discovery.py
git commit -m "Accept keyboards and return every device from discovery"
```

---

### Task 2: Reject the duplicate nodes that paired devices expose

**Files:**
- Modify: `mousebat/discovery.py`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: Task 1's `find_devices`.
- Produces: `discovery.is_paired_device_node(device_dir: str) -> bool`.

**Why:** `find_receivers` accepts any Logitech node declaring report `0x11`, which includes the node `hid-logitech-dj` creates for the keyboard itself. Without this the keyboard is found twice and gets two tray items. Skipping it also saves 7.54 s at startup: indices 1–5 on that node answer nothing, and `ping` retries three times, so each costs three 0.5 s timeouts.

- [ ] **Step 1: Write the failing test**

Add this helper next to `make_hidraw` in `tests/test_discovery.py`:

```python
def make_paired_hidraw(root: str, name: str, hid_id: str, descriptor: bytes) -> None:
    """A node whose device is nested under another HID device, as paired devices are.

    Mirrors the real topology: `/sys/class/hidraw/hidraw5/device` resolves to
    `.../0003:046D:C52B.0007/0003:046D:408A.0008`, so its parent is the receiver's
    own HID device rather than a USB interface.
    """
    receiver = os.path.join(root, "_tree", "0003:046D:C52B.0007")
    child = os.path.join(receiver, "0003:046D:408A.0008")
    os.makedirs(child, exist_ok=True)
    for directory, ids in ((receiver, "0003:0000046D:0000C52B"), (child, hid_id)):
        with open(os.path.join(directory, "uevent"), "w", encoding="utf-8") as handle:
            handle.write(f"HID_ID={ids}\nHID_NAME=Logitech\n")
        with open(os.path.join(directory, "report_descriptor"), "wb") as handle:
            handle.write(descriptor)
    os.makedirs(os.path.join(root, name))
    os.symlink(child, os.path.join(root, name, "device"))
```

Add these tests to the existing `TestFindReceivers` class:

```python
    def test_paired_device_node_is_skipped(self, tmp_path) -> None:
        """The keyboard answers on its own node too; taking both would double it."""
        root = str(tmp_path)
        make_hidraw(root, "hidraw4", "0003:0000046D:0000C52B", HIDPP_DESCRIPTOR)
        make_paired_hidraw(root, "hidraw5", "0003:0000046D:0000408A", HIDPP_DESCRIPTOR)

        found = discovery.find_receivers(root, dev_root="/dev")
        assert [node.device_path for node in found] == ["/dev/hidraw4"]

    def test_receiver_node_is_kept(self, tmp_path) -> None:
        root = str(tmp_path)
        make_hidraw(root, "hidraw4", "0003:0000046D:0000C52B", HIDPP_DESCRIPTOR)
        assert not discovery.is_paired_device_node(
            os.path.join(root, "hidraw4", "device")
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_discovery.py -k paired_device_node -q`
Expected: FAIL — `hidraw5` is still in the result, and `is_paired_device_node` does not exist.

- [ ] **Step 3: Implement the check**

Add to `mousebat/discovery.py`, above `find_receivers`:

```python
def is_paired_device_node(device_dir: str) -> bool:
    """Whether this hidraw node belongs to a paired device rather than a receiver.

    A receiver's HID device hangs off a USB interface; a paired device's hangs off the
    receiver's own HID device. Only the latter has a HID parent, and a HID device is
    recognisable by its report descriptor. Verified on the target machine:

        hidraw4 -> .../3-4:1.2/0003:046D:C52B.0007                  parent 3-4:1.2
        hidraw5 -> .../0003:046D:C52B.0007/0003:046D:408A.0008      parent 0003:046D:...

    Both kinds answer HID++, so both would otherwise be probed and the same device
    reported twice.
    """
    parent = os.path.dirname(os.path.realpath(device_dir))
    return os.path.exists(os.path.join(parent, "report_descriptor"))
```

In `find_receivers`, add the check immediately after the `speaks_hidpp` guard:

```python
        if not speaks_hidpp(descriptor):
            continue
        if is_paired_device_node(device_dir):
            continue
        found.append(ReceiverNode(device_path=os.path.join(dev_root, name), product_id=ids[1]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_discovery.py -q`
Expected: PASS, including the pre-existing `TestFindReceivers` tests — `make_hidraw` builds `device` as a plain directory whose parent holds no report descriptor, so those nodes still count as receivers.

- [ ] **Step 5: Verify against the real machine**

Run:

```bash
./.venv/bin/python -c "from mousebat import discovery; [print(n.device_path, hex(n.product_id)) for n in discovery.find_receivers()]"
```

Expected: `/dev/hidraw3 0xc548` and `/dev/hidraw4 0xc52b` — exactly two nodes. `/dev/hidraw5` must be gone. If node numbers differ, confirm the count is two and that neither is the keyboard's own `0x408a` node.

- [ ] **Step 6: Commit**

```bash
git add mousebat/discovery.py tests/test_discovery.py
git commit -m "Skip the hidraw nodes of paired devices"
```

---

### Task 3: Give the icon a shape parameter, battery unchanged

**Files:**
- Modify: `mousebat/icon.py`
- Test: `tests/test_icon.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `icon.Shape` — `Enum` with members `BATTERY`, `KEYBOARD`, `MOUSE`
  - `icon.render_pixmap(percent: int | None, *, shape: Shape = Shape.BATTERY, charging: bool = False, offline: bool = False, size: int = 64, color: QColor | None = None) -> QPixmap`
  - `icon.make_icon(percent: int | None, *, shape: Shape = Shape.BATTERY, charging: bool = False, offline: bool = False) -> QIcon`

**Why this is its own task:** it is a pure refactor. The existing pixel tests are the specification — they must pass untouched, which proves the battery still renders identically before any new shape exists.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_icon.py`:

```python
class TestShape:
    def test_battery_is_the_default(self) -> None:
        explicit = icon.render_pixmap(50, shape=icon.Shape.BATTERY, size=220, color=WHITE)
        assert explicit.toImage() == icon.render_pixmap(50, size=220, color=WHITE).toImage()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_icon.py::TestShape -q`
Expected: FAIL with `AttributeError: module 'mousebat.icon' has no attribute 'Shape'`.

- [ ] **Step 3: Restructure the renderer around a silhouette**

In `mousebat/icon.py`, add two stdlib imports above the Qt ones, and add `QPainterPath`
to the **existing** `PyQt6.QtGui` import line rather than writing a second one:

```python
from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPixmap, QPolygonF
```

Add after `ICON_SIZES`:

```python
class Shape(Enum):
    """Which silhouette carries the charge."""

    BATTERY = "battery"
    KEYBOARD = "keyboard"
    MOUSE = "mouse"


@dataclass(frozen=True)
class Silhouette:
    """A shape's geometry in pixels, for one icon size.

    outline — stroked with the charge colour.
    interior — the area the charge fills, already inset by half a pen so the fill
        meets the outline's inner face without a seam.
    solid — painted filled rather than stroked (the battery's nub).
    vertical — fill grows upwards from the bottom instead of rightwards.
    """

    outline: QPainterPath
    interior: QRectF
    solid: QPainterPath | None = None
    vertical: bool = False


def _battery_silhouette(unit: float, pen: float) -> Silhouette:
    body = QRectF(2 * unit, 6 * unit, 16 * unit, 10 * unit)
    outline = QPainterPath()
    outline.addRect(body)
    nose = QPainterPath()
    nose.addRect(
        QRectF(
            body.right() + pen,
            body.top() + body.height() * 0.28,
            1.8 * unit,
            body.height() * 0.44,
        )
    )
    half = pen / 2.0
    return Silhouette(
        outline=outline, interior=body.adjusted(half, half, -half, -half), solid=nose
    )


_SILHOUETTES = {Shape.BATTERY: _battery_silhouette}


def _fill_rect(interior: QRectF, percent: int, pen: float, vertical: bool) -> QRectF:
    """The filled portion of the interior, never thinner than a pen stroke."""
    if vertical:
        height = max(interior.height() * percent / 100.0, pen)
        return QRectF(interior.left(), interior.bottom() - height, interior.width(), height)
    width = max(interior.width() * percent / 100.0, pen)
    return QRectF(interior.left(), interior.top(), width, interior.height())
```

Replace the body of `render_pixmap` with the shape-driven version. Keep the existing signature's `percent`, `charging`, `offline`, `size` and `color`, and add `shape`:

```python
def render_pixmap(
    percent: int | None,
    *,
    shape: Shape = Shape.BATTERY,
    charging: bool = False,
    offline: bool = False,
    size: int = 64,
    color: QColor | None = None,
) -> QPixmap:
    """The device's silhouette, filled in proportion to its charge."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    stroke = color_for(percent) if color is None else QColor(color)
    if offline:
        stroke.setAlphaF(0.45)

    unit = size / 22.0  # proportions are authored for a 22x22 panel icon
    pen_width = max(1.0, round(1.6 * unit))
    silhouette = _SILHOUETTES[shape](unit, pen_width)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        pen = painter.pen()
        pen.setColor(stroke)
        pen.setWidthF(pen_width)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(silhouette.outline)

        if silhouette.solid is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(stroke)
            painter.drawPath(silhouette.solid)

        if not offline and percent is not None and percent > 0:
            interior = silhouette.interior
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(stroke)
            painter.drawRect(_fill_rect(interior, percent, pen_width, silhouette.vertical))

            if charging:
                # Clipping to the interior keeps the cut away from the outline —
                # without it the bolt slices through the walls.
                painter.setClipRect(interior)
                bolt = _bolt(interior)

                # Cut a slightly wider silhouette first, so the bolt reads as a gap
                # in the fill instead of merging with it...
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                gap_pen = painter.pen()
                gap_pen.setColor(QColor(Qt.GlobalColor.black))
                gap_pen.setWidthF(pen_width)
                painter.setPen(gap_pen)
                painter.setBrush(QColor(Qt.GlobalColor.black))
                painter.drawPolygon(bolt)

                # ...then paint the bolt itself, so it stays visible even when the
                # fill is too short to contain it.
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(stroke)
                painter.drawPolygon(bolt)
                painter.setClipping(False)
    finally:
        painter.end()
    return pixmap
```

Widen `make_icon`:

```python
def make_icon(
    percent: int | None,
    *,
    shape: Shape = Shape.BATTERY,
    charging: bool = False,
    offline: bool = False,
) -> QIcon:
    """A QIcon carrying every panel size."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(
            render_pixmap(
                percent, shape=shape, charging=charging, offline=offline, size=size
            )
        )
    return icon
```

- [ ] **Step 4: Run the full icon suite to verify nothing moved**

Run: `./.venv/bin/python -m pytest tests/test_icon.py -q`
Expected: PASS, all of it. `TestOutlineIntegrity` and `TestFill` are the ones that matter: they assert exact opaque-run counts, so if `drawPath` rendered differently from the old `drawRect` they would fail. If any of them fails, the refactor changed the picture — fix the geometry rather than the test.

- [ ] **Step 5: Commit**

```bash
git add mousebat/icon.py tests/test_icon.py
git commit -m "Draw the icon from a silhouette instead of a fixed battery"
```

---

### Task 4: Keyboard and mouse silhouettes

**Files:**
- Modify: `mousebat/icon.py`, `tools/preview_icon.py`
- Test: `tests/test_icon.py`

**Interfaces:**
- Consumes: Task 3's `Shape`, `Silhouette`, `_SILHOUETTES`, `_fill_rect`.
- Produces: `Shape.KEYBOARD` and `Shape.MOUSE` entries in `_SILHOUETTES`; `tools/preview_icon.py` renders every shape including a 22 px strip.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_icon.py`:

```python
def opaque_pixels(shape: icon.Shape, percent: int | None, **kwargs) -> int:
    image = icon.render_pixmap(percent, shape=shape, size=220, color=WHITE, **kwargs).toImage()
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 0
    )


class TestSilhouettes:
    @pytest.mark.parametrize("shape", [icon.Shape.KEYBOARD, icon.Shape.MOUSE])
    def test_fill_grows_with_charge(self, shape: icon.Shape) -> None:
        assert opaque_pixels(shape, 10) < opaque_pixels(shape, 50) < opaque_pixels(shape, 95)

    @pytest.mark.parametrize("shape", [icon.Shape.KEYBOARD, icon.Shape.MOUSE])
    def test_not_empty_at_zero(self, shape: icon.Shape) -> None:
        """At 0% the outline still has to be there."""
        assert opaque_pixels(shape, 0) > 0

    @pytest.mark.parametrize("shape", [icon.Shape.KEYBOARD, icon.Shape.MOUSE])
    def test_charging_changes_the_picture(self, shape: icon.Shape) -> None:
        idle = icon.render_pixmap(60, shape=shape, size=220, color=WHITE).toImage()
        charging = icon.render_pixmap(
            60, shape=shape, charging=True, size=220, color=WHITE
        ).toImage()
        assert idle != charging

    @pytest.mark.parametrize("shape", [icon.Shape.KEYBOARD, icon.Shape.MOUSE])
    def test_offline_is_dimmed(self, shape: icon.Shape) -> None:
        online = icon.render_pixmap(50, shape=shape, size=220, color=WHITE).toImage()
        offline = icon.render_pixmap(
            50, shape=shape, offline=True, size=220, color=WHITE
        ).toImage()
        row = online.height() // 2
        assert max(offline.pixelColor(x, row).alpha() for x in range(offline.width())) < max(
            online.pixelColor(x, row).alpha() for x in range(online.width())
        )

    def test_the_three_shapes_differ(self) -> None:
        """Two devices side by side must not draw the same picture."""
        rendered = [
            icon.render_pixmap(60, shape=shape, size=220, color=WHITE).toImage()
            for shape in (icon.Shape.BATTERY, icon.Shape.KEYBOARD, icon.Shape.MOUSE)
        ]
        assert rendered[0] != rendered[1]
        assert rendered[1] != rendered[2]
        assert rendered[0] != rendered[2]

    def test_keyboard_is_wider_than_tall_and_the_mouse_is_not(self) -> None:
        """The silhouettes must be told apart by proportion alone, at panel size."""
        for shape, expect_wide in ((icon.Shape.KEYBOARD, True), (icon.Shape.MOUSE, False)):
            image = icon.render_pixmap(0, shape=shape, size=220, color=WHITE).toImage()
            columns = [
                x
                for x in range(image.width())
                if any(image.pixelColor(x, y).alpha() > 0 for y in range(image.height()))
            ]
            rows = [
                y
                for y in range(image.height())
                if any(image.pixelColor(x, y).alpha() > 0 for x in range(image.width()))
            ]
            wide = (columns[-1] - columns[0]) > (rows[-1] - rows[0])
            assert wide is expect_wide
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_icon.py::TestSilhouettes -q`
Expected: FAIL with `AttributeError: KEYBOARD` (the enum has no such member yet).

- [ ] **Step 3: Add the two shapes**

Add the members to `Shape` — they are already written in Task 3's enum, so nothing to change there. Add the builders to `mousebat/icon.py` after `_battery_silhouette`:

```python
def _keyboard_silhouette(unit: float, pen: float) -> Silhouette:
    """Wide and shallow, with softened corners — the proportions do the identifying."""
    body = QRectF(1.5 * unit, 7.0 * unit, 19 * unit, 8 * unit)
    outline = QPainterPath()
    outline.addRoundedRect(body, 1.4 * unit, 1.4 * unit)
    half = pen / 2.0
    return Silhouette(outline=outline, interior=body.adjusted(half, half, -half, -half))


def _mouse_silhouette(unit: float, pen: float) -> Silhouette:
    """Tall and narrow with a domed top: the opposite proportion to the keyboard.

    Filling upwards rather than rightwards suits the shape and reinforces the
    difference at panel size.
    """
    body = QRectF(7 * unit, 2.5 * unit, 8 * unit, 17 * unit)
    outline = QPainterPath()
    outline.addRoundedRect(body, 3.6 * unit, 3.6 * unit)
    half = pen / 2.0
    return Silhouette(
        outline=outline,
        interior=body.adjusted(half, half, -half, -half),
        vertical=True,
    )
```

Extend the registry:

```python
_SILHOUETTES = {
    Shape.BATTERY: _battery_silhouette,
    Shape.KEYBOARD: _keyboard_silhouette,
    Shape.MOUSE: _mouse_silhouette,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_icon.py -q`
Expected: PASS, including every pre-existing battery test.

- [ ] **Step 5: Extend the preview tool**

In `tools/preview_icon.py`, widen the legend and give every row a shape, adding a strip at true panel size. Replace `LEGEND = 84` with `LEGEND = 130` and replace the `ROWS` definition with:

```python
ROWS = (
    ("keyboard", icon.Shape.KEYBOARD, False, (*LEVELS, ("no link", None))),
    ("keyboard, chg", icon.Shape.KEYBOARD, True, LEVELS),
    ("mouse", icon.Shape.MOUSE, False, (*LEVELS, ("no link", None))),
    ("mouse, chg", icon.Shape.MOUSE, True, LEVELS),
    ("battery", icon.Shape.BATTERY, False, (*LEVELS, ("no link", None))),
)
```

In the render loop, unpack the shape and pass it through:

```python
        for row, (row_label, shape, charging, states) in enumerate(ROWS):
```

```python
                pixmap = icon.render_pixmap(
                    percent,
                    shape=shape,
                    charging=charging and not offline,
                    offline=offline,
                    size=CELL - 24,
                    color=OFFLINE_COLOR if offline else None,
                )
```

Then add a final strip that renders at exactly 22 px and blows it up without smoothing, because the panel size is the only size that decides whether the shapes are legible. Add after the row loop, still inside the `try`:

```python
            # The panel renders at 22 px. Judging the silhouettes at 64 px flatters
            # them, so the last strip shows the real thing plus a nearest-neighbour
            # magnification of it.
            y = PAD + len(ROWS) * (CELL + LABEL)
            painter.setFont(legend_font)
            painter.setPen(QColor("#8a9099"))
            painter.drawText(
                QRectF(PAD, y, LEGEND - PAD, CELL),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                "22 px, x4",
            )
            for column, shape in enumerate(
                (icon.Shape.KEYBOARD, icon.Shape.MOUSE, icon.Shape.BATTERY)
            ):
                small = icon.render_pixmap(73, shape=shape, size=22, color=None)
                blown = small.scaled(
                    88,
                    88,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
                painter.drawPixmap(int(LEGEND + column * CELL + 12), int(y + 12), blown)
                painter.drawPixmap(int(LEGEND + column * CELL + 12), int(y + 4), small)
```

Grow the sheet to fit the extra strip — replace the `height` computation:

```python
    height = PAD + (len(ROWS) + 1) * (CELL + LABEL) + PAD
```

- [ ] **Step 6: Render the preview and judge it**

> **Outcome, recorded after the fact.** Three rounds of device drawings were rejected —
> a top-view mouse clashed with the keyboard's orientation, key marks inside the slab
> smeared into a dashed line at panel size, and a profile mouse read as an abstract
> wedge. What shipped: the keyboard is a nubless rounded slab, the mouse keeps the
> classic battery, and both lie the same way up. The judging size was wrong at first
> too: the panel renders at about 29 px, not 22, measured off
> `docs/images/in-panel.png`. See the spec's Icon section.

Run:

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/python tools/preview_icon.py /tmp/icon-shapes.png
```

Open `/tmp/icon-shapes.png` and look at the `22 px, x4` strip. The question is only this: **at 22 px, are the keyboard and the mouse distinguishable at a glance?**

If yes, continue to Step 7.

If no, apply the fallback the spec authorises — distinguish by orientation instead of by silhouette. Replace the two builders with:

```python
def _keyboard_silhouette(unit: float, pen: float) -> Silhouette:
    """The battery, horizontal: the keyboard keeps the familiar shape."""
    return _battery_silhouette(unit, pen)


def _mouse_silhouette(unit: float, pen: float) -> Silhouette:
    """The battery stood on end, filling upwards — unmistakable next to the keyboard.

    The body starts at 4 units, not 2: the nub sits above it, and the pen is about
    1.6 units wide, so a higher body would push the nub off the canvas.
    """
    body = QRectF(6 * unit, 4 * unit, 10 * unit, 15 * unit)
    outline = QPainterPath()
    outline.addRect(body)
    nose = QPainterPath()
    nose.addRect(
        QRectF(
            body.left() + body.width() * 0.28,
            body.top() - pen - 1.8 * unit,
            body.width() * 0.44,
            1.8 * unit,
        )
    )
    half = pen / 2.0
    return Silhouette(
        outline=outline,
        interior=body.adjusted(half, half, -half, -half),
        solid=nose,
        vertical=True,
    )
```

Check the geometry after editing: at `size=220` the unit is 10 px and the pen rounds to
16 px, so the nub's top edge lands at `40 - 16 - 18 = 6` px — inside the canvas. Any
higher body puts it at a negative coordinate and the nub silently disappears.

Then re-run Step 4's tests. `test_the_three_shapes_differ` still holds, because the mouse is vertical and the keyboard is not; `test_keyboard_is_wider_than_tall_and_the_mouse_is_not` also still holds. Re-render and re-judge before continuing. Note in the commit message which variant was chosen.

- [ ] **Step 7: Commit**

```bash
git add mousebat/icon.py tools/preview_icon.py tests/test_icon.py
git commit -m "Draw a keyboard and a mouse silhouette"
```

---

### Task 5: One device per tray item

**Files:**
- Modify: `mousebat/tray.py`
- Test: `tests/test_tray.py`

**Interfaces:**
- Consumes: Task 1's `HidppDevice`, `relocate`; Task 4's `Shape`, `make_icon`.
- Produces:
  - `tray.SHAPE_FOR_TYPE: dict[int, icon.Shape]`
  - `tray.shape_for(kind: int) -> icon.Shape`
  - `tray.Poller(device: discovery.HidppDevice)` with signal `sampled(object)` and slots `poll()`, `shutdown()`
  - `tray.ItemMenu(app: QApplication, autostart: Autostart, *, with_refresh: bool)` with signal `refresh_requested()`, attribute `menu: QMenu`, method `sync()`
  - `tray.DeviceItem(app: QApplication, device: discovery.HidppDevice, autostart: Autostart)` with methods `start()`, `stop()` and attributes `_icon`, `_timer`

- [ ] **Step 1: Write the failing tests**

Replace the whole of `tests/test_tray.py` with:

```python
"""Tray state transitions, driven by stubbed samples instead of real polling."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="requires python3-pyqt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mousebat import battery, discovery, icon, tray  # noqa: E402
from mousebat.autostart import Autostart  # noqa: E402

from .test_autostart import FakeSystemctl  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def app() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


KEYBOARD = discovery.HidppDevice(
    "/dev/hidraw4", 6, "MX Keys", (4, 5), discovery.DEVICE_TYPE_KEYBOARD
)
MOUSE = discovery.HidppDevice(
    "/dev/hidraw3", 2, "MX Master 3S", (4, 5), discovery.DEVICE_TYPE_MOUSE
)


def make_item(device: discovery.HidppDevice, fake: FakeSystemctl | None = None) -> tray.DeviceItem:
    runner = fake if fake is not None else FakeSystemctl()
    return tray.DeviceItem(QApplication.instance(), device, Autostart(runner=runner))


def reading(percent: int | None, status: battery.ChargeStatus) -> battery.BatteryReading:
    return battery.BatteryReading(percent=percent, status=status, source="0x1000")


class TestShapeForType:
    def test_keyboard(self) -> None:
        assert tray.shape_for(discovery.DEVICE_TYPE_KEYBOARD) is icon.Shape.KEYBOARD

    def test_mouse_and_trackball_share_a_shape(self) -> None:
        assert tray.shape_for(discovery.DEVICE_TYPE_MOUSE) is icon.Shape.MOUSE
        assert tray.shape_for(discovery.DEVICE_TYPE_TRACKBALL) is icon.Shape.MOUSE

    def test_unknown_type_falls_back_to_a_battery(self) -> None:
        assert tray.shape_for(0x42) is icon.Shape.BATTERY


class TestDeviceItem:
    def test_titled_after_its_device_at_creation(self) -> None:
        """Qt freezes the title when the item is created, so the name must be known."""
        item = make_item(KEYBOARD)
        assert item._icon is not None
        assert QApplication.instance().applicationDisplayName() == "MX Keys"

    def test_online_tooltip_shows_name_and_percent(self) -> None:
        item = make_item(KEYBOARD)
        item._apply(tray.Sample(name="MX Keys", reading=reading(100, battery.ChargeStatus.FULL)))
        assert item._icon.toolTip() == "MX Keys\n100% — fully charged"

    def test_offline_tooltip_keeps_the_device_name(self) -> None:
        item = make_item(MOUSE)
        item._apply(tray.Sample(name=None, reading=None, detail="timeout"))
        assert item._icon.toolTip().startswith("MX Master 3S\nno connection")

    def test_unknown_percent_is_shown_as_a_dash(self) -> None:
        item = make_item(MOUSE)
        item._apply(
            tray.Sample(name="MX Master 3S", reading=reading(None, battery.ChargeStatus.DISCHARGING))
        )
        assert "—" in item._icon.toolTip()

    def test_offline_polls_more_often(self) -> None:
        item = make_item(MOUSE)
        item._apply(tray.Sample(name=None, reading=None, detail="asleep"))
        assert item._timer.interval() == tray.OFFLINE_INTERVAL_MS

    def test_back_online_restores_the_slow_interval(self) -> None:
        item = make_item(MOUSE)
        item._apply(tray.Sample(name=None, reading=None))
        item._apply(
            tray.Sample(name="MX Master 3S", reading=reading(50, battery.ChargeStatus.CHARGING))
        )
        assert item._timer.interval() == tray.POLL_INTERVAL_MS

    def test_the_item_is_never_replaced(self) -> None:
        """Re-creating a tray item drops it from the panel for good."""
        item = make_item(MOUSE)
        first = item._icon
        item._apply(tray.Sample(name=None, reading=None))
        item._apply(
            tray.Sample(name="MX Master 3S", reading=reading(50, battery.ChargeStatus.CHARGING))
        )
        assert item._icon is first


class TestItemMenu:
    def test_refresh_can_be_omitted(self) -> None:
        with_refresh = tray.ItemMenu(
            QApplication.instance(), Autostart(runner=FakeSystemctl()), with_refresh=True
        )
        without = tray.ItemMenu(
            QApplication.instance(), Autostart(runner=FakeSystemctl()), with_refresh=False
        )
        labels = [action.text() for action in with_refresh.menu.actions()]
        assert "Refresh" in labels
        assert "Refresh" not in [action.text() for action in without.menu.actions()]

    def test_checkbox_mirrors_the_unit(self) -> None:
        menu = tray.ItemMenu(
            QApplication.instance(), Autostart(runner=FakeSystemctl(enabled=True)), with_refresh=True
        )
        menu.sync()
        assert menu._autostart_action.isChecked()

    def test_hidden_without_an_installed_unit(self) -> None:
        menu = tray.ItemMenu(
            QApplication.instance(),
            Autostart(runner=FakeSystemctl(installed=False)),
            with_refresh=True,
        )
        menu.sync()
        assert not menu._autostart_action.isVisible()

    def test_toggling_calls_systemctl(self) -> None:
        fake = FakeSystemctl(enabled=False)
        menu = tray.ItemMenu(QApplication.instance(), Autostart(runner=fake), with_refresh=True)
        menu._autostart_action.setChecked(True)
        assert "enable" in fake.verbs
        assert fake.state is True

    def test_syncing_does_not_call_enable_or_disable(self) -> None:
        """Refreshing the checkmark must not flip the unit as a side effect."""
        fake = FakeSystemctl(enabled=True)
        menu = tray.ItemMenu(QApplication.instance(), Autostart(runner=fake), with_refresh=True)
        fake.calls.clear()
        menu.sync()
        assert "enable" not in fake.verbs
        assert "disable" not in fake.verbs

    def test_refusal_reverts_the_checkmark(self) -> None:
        fake = FakeSystemctl(enabled=False)
        fake.refuse = True
        menu = tray.ItemMenu(QApplication.instance(), Autostart(runner=fake), with_refresh=True)
        menu._autostart_action.setChecked(True)
        assert not menu._autostart_action.isChecked()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_tray.py -q`
Expected: FAIL with `AttributeError: module 'mousebat.tray' has no attribute 'shape_for'`.

- [ ] **Step 3: Rewrite `tray.py` up to `DeviceItem`**

Replace everything in `mousebat/tray.py` from the imports down to the end of the old `Poller` class, and replace the old `Tray` class with `ItemMenu` plus `DeviceItem`. The result:

```python
"""Tray items, one per device, each polling on its own worker thread.

Polling blocks: a lost link means re-walking receivers and indices, which takes
seconds, so it lives on a worker thread while the GUI only receives finished
results. One thread per device keeps a sleeping device from delaying its neighbour.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import battery, discovery, hidpp, icon
from .autostart import Autostart
from .icon import make_icon

#: Charge drifts slowly, and every extra request wakes the device up.
POLL_INTERVAL_MS = 5 * 60 * 1000
#: Link lost — check more often so the device is picked up quickly once it returns.
OFFLINE_INTERVAL_MS = 60 * 1000

FALLBACK_TITLE = "mousebat"

#: A trackball is a mouse as far as the panel is concerned.
SHAPE_FOR_TYPE = {
    discovery.DEVICE_TYPE_KEYBOARD: icon.Shape.KEYBOARD,
    discovery.DEVICE_TYPE_MOUSE: icon.Shape.MOUSE,
    discovery.DEVICE_TYPE_TRACKBALL: icon.Shape.MOUSE,
}


def shape_for(kind: int) -> icon.Shape:
    """The silhouette for a HID++ device type; a plain battery for anything unknown."""
    return SHAPE_FOR_TYPE.get(kind, icon.Shape.BATTERY)


@dataclass(frozen=True)
class Sample:
    """The outcome of a single poll."""

    name: str | None
    reading: battery.BatteryReading | None
    detail: str = ""

    @property
    def online(self) -> bool:
        return self.reading is not None


class Poller(QObject):
    """Lives on the worker thread, polling one known device."""

    sampled = pyqtSignal(object)  # Sample

    def __init__(self, device: discovery.HidppDevice) -> None:
        super().__init__()
        self._device = device
        self._transport: hidpp.Transport | None = None
        self._reader: battery.BatteryReader | None = None

    @pyqtSlot()
    def poll(self) -> None:
        try:
            self.sampled.emit(self._sample())
        except Exception as exc:  # noqa: BLE001 — a poll must never kill the thread
            self._drop()
            self.sampled.emit(Sample(name=self._device.name, reading=None, detail=str(exc)))

    def _sample(self) -> Sample:
        if self._reader is None and not self._connect():
            return Sample(name=self._device.name, reading=None, detail="device not found")

        assert self._reader is not None
        try:
            return Sample(name=self._device.name, reading=self._reader.read())
        except (hidpp.HidppTimeout, hidpp.DeviceNotConnected, hidpp.HidppError, OSError) as exc:
            # Asleep, or the receiver moved to another node: start over next time.
            self._drop()
            return Sample(name=self._device.name, reading=None, detail=str(exc))

    def _connect(self) -> bool:
        """Locate the device again and open a link to it.

        The stored path is not reused: hidraw numbering changes when a receiver is
        replugged, so the device is found by identity instead.
        """
        device = discovery.relocate(self._device)
        if device is None:
            return False
        try:
            transport = hidpp.Transport(device.device_path)
        except OSError:
            return False
        self._device = device
        self._transport = transport
        self._reader = battery.BatteryReader(hidpp.Link(transport), device.device_index)
        return True

    def _drop(self) -> None:
        if self._reader is not None:
            self._reader.forget()
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._reader = None

    @pyqtSlot()
    def shutdown(self) -> None:
        self._drop()


class ItemMenu(QObject):
    """The context menu of one tray item.

    `Refresh` belongs to the item that owns the menu and is omitted when there is
    nothing to refresh. `Start at login` and `Quit` are process-wide: `Autostart`
    governs a single unit, so every item's checkbox reflects the same state.
    """

    refresh_requested = pyqtSignal()

    def __init__(self, app: QApplication, autostart: Autostart, *, with_refresh: bool) -> None:
        super().__init__()
        self._autostart = autostart

        menu = QMenu()
        if with_refresh:
            menu.addAction("Refresh").triggered.connect(self.refresh_requested.emit)

        self._autostart_action = menu.addAction("Start at login")
        self._autostart_action.setCheckable(True)
        self._autostart_action.toggled.connect(self._set_autostart)
        # Autostart may have been changed from a terminal meanwhile, so the checkmark
        # is refreshed every time the menu opens rather than once.
        menu.aboutToShow.connect(self.sync)
        self.sync()

        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(app.quit)
        self.menu = menu

    def sync(self) -> None:
        """Mirror the unit's real state into the checkbox.

        Hidden when no unit is installed — the applet was started by hand, and there
        is nothing to enable.
        """
        available = self._autostart.available()
        self._autostart_action.setVisible(available)
        if not available:
            return
        # Assigning setChecked would re-emit toggled and call systemctl again.
        self._autostart_action.blockSignals(True)
        self._autostart_action.setChecked(self._autostart.enabled())
        self._autostart_action.blockSignals(False)

    @pyqtSlot(bool)
    def _set_autostart(self, value: bool) -> None:
        reached = self._autostart.set_enabled(value)
        if reached != value:
            # systemctl refused; show what is actually true.
            self._autostart_action.blockSignals(True)
            self._autostart_action.setChecked(reached)
            self._autostart_action.blockSignals(False)


class DeviceItem(QObject):
    """One device: its tray icon, menu, worker thread and poll timer."""

    poll_requested = pyqtSignal()

    def __init__(
        self, app: QApplication, device: discovery.HidppDevice, autostart: Autostart
    ) -> None:
        super().__init__()
        self._app = app
        self._device = device
        self._shape = shape_for(device.kind)

        self._item_menu = ItemMenu(app, autostart, with_refresh=True)
        self._item_menu.refresh_requested.connect(self.poll_requested.emit)
        self._icon = self._create_icon(device.name)

        self._thread = QThread()
        self._poller = Poller(device)
        self._poller.moveToThread(self._thread)
        self._poller.sampled.connect(self._apply)
        self.poll_requested.connect(self._poller.poll)  # queued: runs on the worker

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll_requested.emit)

    def _create_icon(self, title: str) -> QSystemTrayIcon:
        """Create the tray item, titled after the device.

        Plasma shows this title in the collapsed-items list, and Qt copies it from the
        application name at creation — there is no way to change it afterwards, and
        re-creating the item makes it vanish from the tray for good. Discovery has
        already named the device, so the name is set first and the icon second.
        """
        self._app.setApplicationName(title)
        self._app.setApplicationDisplayName(title)

        item = QSystemTrayIcon()
        item.setIcon(make_icon(None, shape=self._shape, offline=True))
        item.setToolTip(f"{title} — polling…")
        item.setContextMenu(self._item_menu.menu)
        item.show()
        return item

    def start(self) -> None:
        self._thread.start()
        self._timer.start()
        self.poll_requested.emit()

    @pyqtSlot(object)
    def _apply(self, sample: Sample) -> None:
        name = sample.name or self._device.name

        if sample.online:
            reading = sample.reading
            assert reading is not None
            percent_text = "—" if reading.percent is None else f"{reading.percent}%"
            self._icon.setIcon(
                make_icon(reading.percent, shape=self._shape, charging=reading.is_charging)
            )
            self._icon.setToolTip(f"{name}\n{percent_text} — {reading.status.value}")
            self._timer.setInterval(POLL_INTERVAL_MS)
        else:
            self._icon.setIcon(make_icon(None, shape=self._shape, offline=True))
            detail = f"\n{sample.detail}" if sample.detail else ""
            self._icon.setToolTip(f"{name}\nno connection{detail}")
            self._timer.setInterval(OFFLINE_INTERVAL_MS)

    def stop(self) -> None:
        self._timer.stop()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        # The thread has stopped, so closing the transport here is safe.
        self._poller.shutdown()
```

Delete the old `NAME_WAIT_MS` constant along with the old `Tray` class: the name is known before the item exists, so nothing waits for it any more.

At this point `tray.Tray` does not exist and `main.py` is broken. Task 6 restores it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_tray.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mousebat/tray.py tests/test_tray.py
git commit -m "Give each device its own tray item"
```

---

### Task 6: Tray discovers once and owns the items

**Files:**
- Modify: `mousebat/tray.py`
- Test: `tests/test_tray.py`

**Interfaces:**
- Consumes: Task 5's `DeviceItem`, `ItemMenu`; Task 1's `find_devices`.
- Produces:
  - `tray.Scanner` with signal `found(object)` and slot `scan()`
  - `tray.PlaceholderItem(app: QApplication, autostart: Autostart)`
  - `tray.Tray(app: QApplication, autostart: Autostart | None = None)` with `start()` and attributes `_items: list[DeviceItem]`, `_placeholder: PlaceholderItem | None` — the same constructor signature `main.py` already uses

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tray.py`:

```python
def make_tray(fake: FakeSystemctl | None = None) -> tray.Tray:
    runner = fake if fake is not None else FakeSystemctl()
    return tray.Tray(QApplication.instance(), autostart=Autostart(runner=runner))


@pytest.fixture
def no_polling(monkeypatch) -> None:
    """Keep `Tray` tests off the real hardware.

    `Tray._build_items` calls `DeviceItem.start()`, which starts a worker thread that
    would go looking for actual receivers under `/dev`. Creating the items is what is
    under test here; polling them is `DeviceItem`'s own business.
    """
    monkeypatch.setattr(tray.DeviceItem, "start", lambda self: None)


class TestTray:
    def test_one_item_per_device(self, no_polling) -> None:
        widget = make_tray()
        widget._build_items([KEYBOARD, MOUSE])
        assert len(widget._items) == 2
        assert widget._placeholder is None
        widget._stop()

    def test_items_follow_the_discovery_order(self, no_polling) -> None:
        widget = make_tray()
        widget._build_items([KEYBOARD, MOUSE])
        assert [item._device.name for item in widget._items] == ["MX Keys", "MX Master 3S"]
        widget._stop()

    def test_placeholder_when_nothing_is_found(self, no_polling) -> None:
        """The menu lives on a tray icon, so with no items there is no way to quit."""
        widget = make_tray()
        widget._build_items([])
        assert widget._items == []
        assert widget._placeholder is not None
        widget._stop()

    def test_application_name_is_restored_after_building(self, no_polling) -> None:
        """Each item steals the application name to get its title; put it back."""
        widget = make_tray()
        widget._build_items([KEYBOARD, MOUSE])
        assert QApplication.instance().applicationDisplayName() == tray.FALLBACK_TITLE
        widget._stop()

    def test_no_items_before_discovery_finishes(self) -> None:
        widget = make_tray()
        assert widget._items == []
        assert widget._placeholder is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_tray.py::TestTray -q`
Expected: FAIL with `AttributeError: module 'mousebat.tray' has no attribute 'Tray'`.

- [ ] **Step 3: Add the scanner, the placeholder and the coordinator**

Append to `mousebat/tray.py`:

```python
class PlaceholderItem:
    """A tray item shown when no device was found.

    Without it the applet would have no icon at all, and the context menu — the only
    way to quit or to toggle autostart — lives on the icon. It polls nothing: picking
    up a device that appears later takes a restart.
    """

    def __init__(self, app: QApplication, autostart: Autostart) -> None:
        app.setApplicationName(FALLBACK_TITLE)
        app.setApplicationDisplayName(FALLBACK_TITLE)

        self._item_menu = ItemMenu(app, autostart, with_refresh=False)
        self._icon = QSystemTrayIcon()
        self._icon.setIcon(make_icon(None, offline=True))
        self._icon.setToolTip(f"{FALLBACK_TITLE}\nno device found")
        self._icon.setContextMenu(self._item_menu.menu)
        self._icon.show()


class Scanner(QObject):
    """Runs the one blocking discovery pass, off the GUI thread."""

    found = pyqtSignal(object)  # list[discovery.HidppDevice]

    @pyqtSlot()
    def scan(self) -> None:
        try:
            devices = discovery.find_devices()
        except Exception:  # noqa: BLE001 — an empty result still yields a usable tray
            devices = []
        self.found.emit(devices)


class Tray(QObject):
    """Discovers devices once at startup, then owns one item per device.

    The device set is fixed from then on: items are only ever added, never removed,
    because Plasma drops a re-created tray item permanently.
    """

    def __init__(self, app: QApplication, autostart: Autostart | None = None) -> None:
        super().__init__()
        self._app = app
        self._autostart = autostart if autostart is not None else Autostart()
        self._items: list[DeviceItem] = []
        self._placeholder: PlaceholderItem | None = None
        self._scan_thread: QThread | None = None
        self._scanner: Scanner | None = None
        app.aboutToQuit.connect(self._stop)

    def start(self) -> None:
        thread = QThread()
        scanner = Scanner()
        scanner.moveToThread(thread)
        scanner.found.connect(self._build_items)
        thread.started.connect(scanner.scan)
        # Both are kept alive for the lifetime of the tray: a garbage-collected
        # QThread would take the scan down with it.
        self._scan_thread = thread
        self._scanner = scanner
        thread.start()

    @pyqtSlot(object)
    def _build_items(self, devices: list[discovery.HidppDevice]) -> None:
        for device in devices:
            item = DeviceItem(self._app, device, self._autostart)
            item.start()
            self._items.append(item)

        if not self._items:
            self._placeholder = PlaceholderItem(self._app, self._autostart)

        # Every item took the application name to title itself; restore ours.
        self._app.setApplicationName(FALLBACK_TITLE)
        self._app.setApplicationDisplayName(FALLBACK_TITLE)

        if self._scan_thread is not None:
            self._scan_thread.quit()

    def _stop(self) -> None:
        for item in self._items:
            item.stop()
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(3000)
```

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `./.venv/bin/python -m pytest`
Expected: PASS, every test in every file. `main.py` needs no change — `Tray(app)` and `tray.start()` keep their signatures.

- [ ] **Step 5: Commit**

```bash
git add mousebat/tray.py tests/test_tray.py
git commit -m "Discover devices once and hold one item each"
```

---

### Task 7: Verify on the target machine and update the README

**Files:**
- Modify: `README.md`, `docs/images/icon-states.png`
- Modify: `docs/superpowers/specs/2026-09-09-keyboard-battery-multi-device-design.md` (status line only)

- [ ] **Step 1: Run the applet from the checkout**

Stop the installed service first so two instances do not both hold the receivers:

```bash
systemctl --user stop mousebat.service
./.venv/bin/python -m mousebat
```

Expected: two tray items appear. Hovering shows `MX Keys` with a real percentage — anything but a flat 50 — and `MX Master 3S` with its own. Both silhouettes are distinguishable in the panel.

- [ ] **Step 2: Check that nothing else was disturbed**

In another terminal:

```bash
systemctl status logid --no-pager | head -5
./.venv/bin/python tools/spike_probe.py 2>&1 | tail -5
```

Expected: `logid` still `active (running)`, and the probe still reads both devices — proving the applet coexists with it and with `logid` on the same nodes. Then stop the applet with Ctrl+C and restart the service:

```bash
systemctl --user start mousebat.service
```

- [ ] **Step 3: Refresh the icon contact sheet**

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/python tools/preview_icon.py
```

Expected: `written: .../docs/images/icon-states.png`. The README embeds this file, so it now shows every shape.

- [ ] **Step 4: Update the README**

Five exact replacements in `README.md`. The project name and the images stay as they are.

Line 3:

```markdown
A tray indicator for Logitech wireless device battery levels, for KDE Plasma.
```

Lines 5–8 — the keyboard's failure mode is different from the mouse's and worth stating,
since it is the reason this exists:

```markdown
The kernel creates no `power_supply` entry at all for mice paired to a Logi Bolt
receiver, and for an MX Keys on Unifying it records only a coarse level — `Low`,
`Normal`, `Full` — which UPower renders as an invented percentage. Either way Plasma's
stock "Battery and Brightness" widget cannot show the real figure. `mousebat` reads the
charge itself — over HID++ 2.0 through the receiver's `/dev/hidraw` node — and draws one
tray icon per device.
```

Line 10 — `mouse` becomes `device`:

```markdown
Read-only: nothing is ever written to the device, so a
```

Lines 23–24 and 28 in "What it shows":

```markdown
- A silhouette of the device — keyboard or mouse — filled in proportion to the charge:
  green from 20% up, amber below 20%, red below 10%.
```

```markdown
- Lost link (device asleep, receiver unplugged): the icon dims and the tooltip reads
  `no connection`. Recovery is picked up automatically.
```

Line 36 — replace the single-device sentence:

```markdown
No device is hard-coded: every keyboard, mouse and trackball found on any Logitech
receiver gets its own tray item. The set is decided once at startup, so a device paired
later shows up after a restart (`systemctl --user restart mousebat`).
```

- [ ] **Step 5: Mark the spec implemented**

In `docs/superpowers/specs/2026-09-09-keyboard-battery-multi-device-design.md`, change the status line to match the original spec's wording:

```markdown
Status: design approved; implemented and verified on the target machine
```

- [ ] **Step 6: Run everything one last time**

Run: `./.venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/images/icon-states.png docs/superpowers/specs/2026-09-09-keyboard-battery-multi-device-design.md
git commit -m "Document keyboard support"
```
