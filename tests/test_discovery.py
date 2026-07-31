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


class TestProbeMice:
    def test_returns_pointer_devices_only(self) -> None:
        link, _ = link_with(
            # index 1 — a keyboard, skipped
            pong(1),
            name_feature_reply(1),
            type_reply(1, 0x00),
            # index 2 — the mouse
            pong(2),
            name_feature_reply(2),
            type_reply(2, discovery.DEVICE_TYPE_MOUSE),
            name_count_reply(2, 12),
            name_chunk_reply(2, b"MX Master 3S"),
            # indices 3..6 stay silent
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        mice = discovery.probe_mice(link, "/dev/hidraw2")
        assert [(mouse.device_index, mouse.name) for mouse in mice] == [(2, "MX Master 3S")]

    def test_trackball_counts_as_pointer(self) -> None:
        replies: list[bytes | None] = [
            pong(1),
            name_feature_reply(1),
            type_reply(1, discovery.DEVICE_TYPE_TRACKBALL),
            name_count_reply(1, 9),
            name_chunk_reply(1, b"Trackball"),
        ]
        replies += [None] * 18  # indices 2..6 stay silent, three ping attempts each
        link, _ = link_with(*replies)
        mice = discovery.probe_mice(link, "/dev/hidraw2")
        assert [mouse.device_index for mouse in mice] == [1]

    def test_silent_receiver_yields_nothing(self) -> None:
        link, _ = link_with(*([None] * 18))
        assert discovery.probe_mice(link, "/dev/hidraw2") == []

    def test_device_without_name_feature_is_skipped(self) -> None:
        replies: list[bytes | None] = [pong(1), name_feature_reply(1, feature=0x00)]
        replies += [None] * 18
        link, _ = link_with(*replies)
        assert discovery.probe_mice(link, "/dev/hidraw2") == []


class TestFindFirstMouse:
    def test_takes_first_receiver_that_answers(self, tmp_path, monkeypatch) -> None:
        root = str(tmp_path)
        make_hidraw(root, "hidraw2", "0003:0000046D:0000C548", HIDPP_DESCRIPTOR)
        make_hidraw(root, "hidraw4", "0003:0000046D:0000C52B", HIDPP_DESCRIPTOR)

        opened: list[str] = []

        def fake_transport(path: str) -> FakeTransport:
            opened.append(path)
            return FakeTransport()

        monkeypatch.setattr(discovery.hidpp, "Transport", fake_transport)
        monkeypatch.setattr(
            discovery,
            "probe_mice",
            lambda link, path: (
                [discovery.MouseDevice(path, 2, "MX Master 3S", (4, 2))]
                if path.endswith("hidraw4")
                else []
            ),
        )

        mouse = discovery.find_first_mouse(root, dev_root="/dev")
        assert mouse is not None
        assert (mouse.device_path, mouse.device_index) == ("/dev/hidraw4", 2)
        assert opened == ["/dev/hidraw2", "/dev/hidraw4"]

    def test_returns_none_when_nothing_found(self, tmp_path) -> None:
        assert discovery.find_first_mouse(str(tmp_path)) is None
