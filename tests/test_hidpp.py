"""The transport is exercised against a fake hidraw node — no hardware needed."""

from __future__ import annotations

import pytest

from battary import hidpp


class FakeTransport:
    """Serves prepared reports and records everything written."""

    def __init__(self, replies: list[bytes | None] | None = None) -> None:
        self.written: list[bytes] = []
        self.replies: list[bytes | None] = list(replies or [])
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def read(self, timeout: float) -> bytes | None:
        if not self.replies:
            return None
        return self.replies.pop(0)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeTransport":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def short(device_index: int, feature: int, function: int, *params: int, sw: int | None = None) -> bytes:
    software_id = hidpp.SOFTWARE_ID if sw is None else sw
    body = bytes((hidpp.SHORT_REPORT_ID, device_index, feature, (function << 4) | software_id))
    return body + bytes(params).ljust(3, b"\x00")


def link_with(*replies: bytes | None) -> tuple[hidpp.Link, FakeTransport]:
    """`None` among the replies means "nothing arrived this time"."""
    transport = FakeTransport(list(replies))
    return hidpp.Link(transport, timeout=0.05), transport


class TestBuild:
    def test_short_report_for_small_params(self) -> None:
        packet = hidpp.build(1, 0x00, 0x1, bytes((0x00, 0x00, 0xAA)))
        assert packet == bytes((0x10, 0x01, 0x00, 0x10 | hidpp.SOFTWARE_ID, 0x00, 0x00, 0xAA))

    def test_long_report_when_params_do_not_fit(self) -> None:
        packet = hidpp.build(2, 0x06, 0x2, bytes(range(8)))
        assert packet[0] == hidpp.LONG_REPORT_ID
        assert len(packet) == hidpp.LONG_LEN

    def test_software_id_is_encoded_in_low_nibble(self) -> None:
        packet = hidpp.build(1, 0x00, 0x3)
        assert packet[3] & 0x0F == hidpp.SOFTWARE_ID
        assert packet[3] >> 4 == 0x3

    def test_rejects_oversized_params(self) -> None:
        with pytest.raises(ValueError):
            hidpp.build(1, 0x00, 0x0, bytes(17))


class TestParse:
    def test_ignores_foreign_report_ids(self) -> None:
        assert hidpp.parse(bytes((0x02, 0x01, 0x00, 0x00))) is None

    def test_accepts_error_report(self) -> None:
        reply = hidpp.parse(bytes((0xFF, 0x01, 0x00, 0x1E, 0x09, 0x00)))
        assert reply is not None
        assert reply.report_id == hidpp.ERROR_REPORT_ID

    def test_splits_address_into_function_and_software_id(self) -> None:
        reply = hidpp.parse(bytes((0x10, 0x01, 0x06, 0x2E, 0x00, 0x00, 0x00)))
        assert reply is not None
        assert (reply.function, reply.software_id) == (0x2, 0x0E)


class TestRequest:
    def test_returns_params_of_matching_reply(self) -> None:
        link, _ = link_with(short(1, 0x06, 0x1, 0x55, 0x0F, 0x00))
        assert link.request(1, 0x06, 0x1)[:3] == bytes((0x55, 0x0F, 0x00))

    def test_skips_reply_with_foreign_software_id(self) -> None:
        """A reply from logid must be dropped and ours accepted."""
        link, _ = link_with(
            short(1, 0x06, 0x1, 0xDE, sw=0x02),  # foreign
            short(1, 0x06, 0x1, 0x42),  # ours
        )
        assert link.request(1, 0x06, 0x1)[0] == 0x42

    def test_skips_reply_for_another_device(self) -> None:
        link, _ = link_with(
            short(2, 0x06, 0x1, 0xDE),
            short(1, 0x06, 0x1, 0x42),
        )
        assert link.request(1, 0x06, 0x1)[0] == 0x42

    def test_skips_reply_for_another_feature(self) -> None:
        link, _ = link_with(
            short(1, 0x07, 0x1, 0xDE),
            short(1, 0x06, 0x1, 0x42),
        )
        assert link.request(1, 0x06, 0x1)[0] == 0x42

    def test_timeout_when_nothing_matches(self) -> None:
        link, _ = link_with(short(1, 0x06, 0x1, 0xDE, sw=0x02))
        with pytest.raises(hidpp.HidppTimeout):
            link.request(1, 0x06, 0x1)

    def test_raises_on_hidpp20_error(self) -> None:
        link, _ = link_with(bytes((0xFF, 0x01, 0x06, 0x10 | hidpp.SOFTWARE_ID, 0x07, 0x00)))
        with pytest.raises(hidpp.HidppError) as caught:
            link.request(1, 0x06, 0x1)
        assert caught.value.protocol == 2
        assert caught.value.code == 0x07

    def test_raises_on_hidpp10_error(self) -> None:
        raw = bytes((0x10, 0x01, 0x8F, 0x06, 0x10 | hidpp.SOFTWARE_ID, 0x03, 0x00))
        link, _ = link_with(raw)
        with pytest.raises(hidpp.HidppError) as caught:
            link.request(1, 0x06, 0x1)
        assert caught.value.protocol == 1
        assert caught.value.code == 0x03

    def test_absent_device_reported_separately(self) -> None:
        raw = bytes((0x10, 0x03, 0x8F, 0x00, 0x10 | hidpp.SOFTWARE_ID, 0x09, 0x00))
        link, _ = link_with(raw)
        with pytest.raises(hidpp.DeviceNotConnected):
            link.request(3, 0x00, 0x1)

    def test_error_for_another_request_is_ignored(self) -> None:
        link, _ = link_with(
            bytes((0xFF, 0x01, 0x09, 0x10 | hidpp.SOFTWARE_ID, 0x07, 0x00)),  # a different feature
            short(1, 0x06, 0x1, 0x42),
        )
        assert link.request(1, 0x06, 0x1)[0] == 0x42


class TestPing:
    def test_returns_protocol_version(self) -> None:
        link, transport = link_with(short(1, 0x00, 0x1, 0x04, 0x02, hidpp.PING_MARKER))
        assert link.ping(1) == (4, 2)
        assert transport.written[0][4:7] == bytes((0x00, 0x00, hidpp.PING_MARKER))

    def test_retries_until_device_wakes_up(self) -> None:
        link, transport = link_with(
            None,  # first attempt: a sleeping mouse stays silent
            short(1, 0x00, 0x1, 0x04, 0x02, hidpp.PING_MARKER),
        )
        assert link.ping(1, attempts=3) == (4, 2)
        assert len(transport.written) == 2

    def test_rejects_wrong_echo(self) -> None:
        link, _ = link_with(short(1, 0x00, 0x1, 0x04, 0x02, 0x11))
        with pytest.raises(hidpp.HidppTimeout):
            link.ping(1, attempts=1)


class TestFeatureIndex:
    def test_returns_index(self) -> None:
        link, transport = link_with(short(1, 0x00, 0x0, 0x06, 0x00, 0x00))
        assert link.feature_index(1, hidpp.FEATURE_UNIFIED_BATTERY) == 6
        assert transport.written[0][4:6] == bytes((0x10, 0x04))

    def test_zero_index_means_unsupported(self) -> None:
        link, _ = link_with(short(1, 0x00, 0x0, 0x00, 0x00, 0x00))
        assert link.feature_index(1, hidpp.FEATURE_UNIFIED_BATTERY) is None
