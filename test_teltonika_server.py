import struct
import unittest

from teltonika_server import handle_client


class FakeSocket:
    def __init__(self, incoming: bytes):
        self.incoming = bytearray(incoming)
        self.sent = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def recv(self, size: int) -> bytes:
        if not self.incoming:
            return b""
        chunk = self.incoming[:size]
        del self.incoming[:size]
        return bytes(chunk)

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)


def imei_frame(imei: str) -> bytes:
    encoded = imei.encode("ascii")
    return struct.pack(">H", len(encoded)) + encoded


def avl_packet(avl_data: bytes) -> bytes:
    return b"\x00\x00\x00\x00" + struct.pack(">I", len(avl_data)) + avl_data + b"\x00\x00\x00\x00"


class AlwaysAckDecodeErrorsTest(unittest.TestCase):
    def test_decode_errors_are_acknowledged_with_advertised_record_count(self) -> None:
        unsupported_codec_packet = avl_packet(b"\x09\x03")
        fake = FakeSocket(imei_frame("123456789012345") + unsupported_codec_packet)

        with self.assertRaises(ConnectionError):
            handle_client(fake, ("127.0.0.1", 8090))

        self.assertEqual(fake.sent, b"\x01" + struct.pack(">I", 3))


if __name__ == "__main__":
    unittest.main()
