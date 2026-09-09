from __future__ import annotations

import os

import pytest

from mousebat import discovery, hidpp

from .test_hidpp import FakeTransport, link_with, short

HIDPP_DESCRIPTOR = bytes((0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x10, 0x85, 0x11))
PLAIN_DESCRIPTOR = bytes((0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x02))


def make_hidraw(root: str, name: str, hid_id: str, descriptor: bytes) -> None:
    device = os.path.join(root, name, "device")
    os.makedirs(device)
    with open(os.path.join(device, "uevent"), "w", encoding="utf-8") as handle:
        handle.write(f"DEVTYPE=usb_interface\nHID_ID={hid_id}\nHID_NAME=Logitech USB Receiver\n")
    with open(os.path.join(device, "report_descriptor"), "wb") as handle:
        handle.write(descriptor)


class TestParseHidId:
    def test_extracts_vendor_and_product(self) -> None:
        assert discovery.parse_hid_id("0003:0000046D:0000C548") == (0x046D, 0xC548)

    @pytest.mark.parametrize("value", ["", "garbage", "0003:0000046D", "0003:046D:C548"])
    def test_rejects_malformed(self, value: str) -> None:
        assert discovery.parse_hid_id(value) is None


class TestSpeaksHidpp:
    def test_detects_long_report_declaration(self) -> None:
        assert discovery.speaks_hidpp(HIDPP_DESCRIPTOR)

    def test_plain_mouse_interface_is_skipped(self) -> None:
        assert not discovery.speaks_hidpp(PLAIN_DESCRIPTOR)


class TestFindReceivers:
    def test_keeps_only_logitech_hidpp_nodes(self, tmp_path) -> None:
        root = str(tmp_path)
        make_hidraw(root, "hidraw0", "0003:0000046D:0000C548", PLAIN_DESCRIPTOR)
        make_hidraw(root, "hidraw2", "0003:0000046D:0000C548", HIDPP_DESCRIPTOR)
        make_hidraw(root, "hidraw3", "0003:00001532:00000067", HIDPP_DESCRIPTOR)  # not Logitech

        found = discovery.find_receivers(root, dev_root="/dev")
        assert [node.device_path for node in found] == ["/dev/hidraw2"]
        assert found[0].product_id == 0xC548

    def test_orders_nodes_numerically(self, tmp_path) -> None:
        root = str(tmp_path)
        for name in ("hidraw10", "hidraw2", "hidraw1"):
            make_hidraw(root, name, "0003:0000046D:0000C548", HIDPP_DESCRIPTOR)

        found = discovery.find_receivers(root, dev_root="/dev")
        assert [node.device_path for node in found] == [
            "/dev/hidraw1",
            "/dev/hidraw2",
            "/dev/hidraw10",
        ]

    def test_missing_directory_yields_nothing(self, tmp_path) -> None:
        assert discovery.find_receivers(str(tmp_path / "absent")) == []

    def test_node_without_descriptor_is_skipped(self, tmp_path) -> None:
        root = str(tmp_path)
        device = os.path.join(root, "hidraw0", "device")
        os.makedirs(device)
        with open(os.path.join(device, "uevent"), "w", encoding="utf-8") as handle:
            handle.write("HID_ID=0003:0000046D:0000C548\n")
        assert discovery.find_receivers(root) == []


def pong(index: int) -> bytes:
    return short(index, 0x00, 0x1, 0x04, 0x02, hidpp.PING_MARKER)


def name_feature_reply(index: int, feature: int = 0x02) -> bytes:
    return short(index, 0x00, 0x0, feature, 0x00, 0x00)


def type_reply(index: int, kind: int, feature: int = 0x02) -> bytes:
    return short(index, feature, 0x2, kind, 0x00, 0x00)


def name_count_reply(index: int, length: int, feature: int = 0x02) -> bytes:
    return short(index, feature, 0x0, length, 0x00, 0x00)


def name_chunk_reply(index: int, text: bytes, feature: int = 0x02) -> bytes:
    head = bytes((hidpp.LONG_REPORT_ID, index, feature, (0x1 << 4) | hidpp.SOFTWARE_ID))
    return head + text.ljust(16, b"\x00")


class TestDeviceName:
    def test_assembles_name_from_chunks(self) -> None:
        link, _ = link_with(
            name_count_reply(1, 18),
            name_chunk_reply(1, b"MX Master 3S Mou"),  # exactly 16 bytes — a full chunk
            name_chunk_reply(1, b"se"),
        )
        assert discovery.device_name(link, 1, 0x02) == "MX Master 3S Mouse"

    def test_stops_at_declared_length(self) -> None:
        link, _ = link_with(
            name_count_reply(1, 5),
            name_chunk_reply(1, b"Mouse extra tail"),
        )
        assert discovery.device_name(link, 1, 0x02) == "Mouse"


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
