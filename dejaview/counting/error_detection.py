from dataclasses import dataclass
from hashlib import blake2b


@dataclass
class RecordMode:
    reference: bytearray


@dataclass
class VerifyMode:
    reference: bytes


@dataclass
class StreamMismatchError(ValueError):
    count: int
    expected: bytes | None  # None if this is past the end of the reference
    actual: bytes


class StreamErrorDetector:
    """
    Utility for detecting replay divergence due to non-determinism.

    Note:
        A small digest size allows for more frequent checks at the same overhead.

        For example, a 1 byte digest every 100 events has the same memory overhead and
        detection probability as a 16 byte digest every 1600 events, but with much
        smaller expected detection latency when the program diverges.
    """

    DEFAULT_PERIOD = 100
    DEFAULT_DIGEST_SIZE = 1

    def __init__(
        self,
        *,
        period: int = DEFAULT_PERIOD,
        digest_size: int = DEFAULT_DIGEST_SIZE,
        salt: bytes = b"",
    ):
        self._state = blake2b(digest_size=digest_size, salt=salt)
        self._mode: RecordMode | VerifyMode = RecordMode(bytearray())
        self._period = period
        self._count = 0

    def switch_to_verify(self, reference: bytes):
        """
        Switch a record mode instance to verify mode using a new reference.
        """
        match self._mode:
            case RecordMode():
                self._mode = VerifyMode(reference)
            case VerifyMode():
                raise ValueError("Already in verify mode")

    def serialize_reference(self) -> bytes:
        """
        Get the reference as bytes. Only valid in record mode.
        """
        match self._mode:
            case RecordMode(reference):
                return bytes(reference)
            case VerifyMode():
                raise ValueError("Cannot serialize reference in verify mode")

    def update(self, data: bytes | bytearray):
        """
        Update the state with new bytes, and periodically:
            - In record mode, append the digest to the reference.
            - In verify mode, check the digest against the reference.

        Raises StreamMismatchError if the digest does not match
        the reference in verify mode.
        """
        self._state.update(data)
        self._count += 1
        if self._count % self._period == 0:
            digest = self._state.digest()
            match self._mode:
                case RecordMode(reference):
                    reference.extend(digest)
                case VerifyMode(reference):
                    size = self._state.digest_size
                    i = (self._count // self._period - 1) * size
                    expected = reference[i : i + size] if i < len(reference) else None
                    if digest != expected:
                        raise StreamMismatchError(
                            count=self._count,
                            expected=expected,
                            actual=digest,
                        )
