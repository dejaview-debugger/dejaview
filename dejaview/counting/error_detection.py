from dataclasses import dataclass
from hashlib import blake2b


@dataclass
class RecordMode:
    reference: bytearray
    verbose_log: list[bytes]


@dataclass
class VerifyMode:
    reference: bytes
    target_count: int
    last_expected: bytes
    full_digest: bytes
    verbose_log: list[bytes]


@dataclass
class StreamMismatchError(ValueError):
    count: int
    expected: bytes | None  # None if this is past the end of the reference
    actual: bytes | None  # None if this is a position mismatch
    message: str = ""


class StreamErrorDetector:
    """
    Utility for detecting replay divergence due to non-determinism.

    Note:
        A small digest size allows for more frequent checks at the same overhead.

        For example, a 1 byte digest every 100 events has the same memory overhead and
        detection probability as a 16 byte digest every 1600 events, but with much
        smaller expected detection latency when the program diverges.
    """

    # For now set it to a small value to catch divergence as soon as possible.
    # As the patching coverage expands, we can increase this to reduce the overhead.
    DEFAULT_PERIOD = 2
    DEFAULT_DIGEST_SIZE = 1
    FULL_DIGEST_SIZE = 32

    def __init__(
        self,
        *,
        period: int = DEFAULT_PERIOD,
        digest_size: int = DEFAULT_DIGEST_SIZE,
        salt: bytes | bytearray = b"",
        verbose: bool = False,
    ):
        self.digest_size = digest_size
        self._verbose = verbose
        self._state = blake2b(digest_size=self.FULL_DIGEST_SIZE, salt=salt)
        self._mode: RecordMode | VerifyMode = RecordMode(bytearray(), [])
        self._period = period
        self._count = 0
        self._last = b""

    def switch_to_verify(self, verify_mode: VerifyMode):
        """
        Switch a record mode instance to verify mode using a new reference.
        """
        match self._mode:
            case RecordMode():
                self._mode = verify_mode
            case VerifyMode():
                raise ValueError("Already in verify mode")

    def as_verify_mode(self) -> VerifyMode:
        """
        Get the VerifyMode object using the current RecordMode state as the reference.
        """
        match self._mode:
            case RecordMode(reference, verbose_log):
                return VerifyMode(
                    reference=bytes(reference),
                    target_count=self._count,
                    last_expected=bytes(self._last),
                    full_digest=self._state.digest(),
                    verbose_log=verbose_log,
                )
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
        self._last = data
        if self._count % self._period == 0:
            digest = self._state.digest()[: self.digest_size]
            match self._mode:
                case RecordMode(reference, verbose_log):
                    reference.extend(digest)
                    if self._verbose:
                        verbose_log.append(data)
                case VerifyMode(reference, verbose_log=verbose_log):
                    i = (self._count // self._period - 1) * self.digest_size
                    expected = (
                        reference[i : i + self.digest_size]
                        if i < len(reference)
                        else None
                    )
                    if digest != expected:
                        msg = f"Digest does not match at count {self._count}"
                        if self._verbose:
                            idx = self._count // self._period - 1
                            expected_data = (
                                verbose_log[idx] if idx < len(verbose_log) else None
                            )
                            msg += f"\nactual:   {data!r}"
                            msg += f"\nexpected: {expected_data!r}"
                        raise StreamMismatchError(
                            count=self._count,
                            expected=expected,
                            actual=digest,
                            message=msg,
                        )

    def assert_no_remaining_reference(self) -> None:
        """
        In verify mode, raise StreamMismatchError if there are unverified
        checkpoints in the reference — i.e., the replay ended before the root did.
        """
        match self._mode:
            case RecordMode():
                pass
            case VerifyMode(target_count=target_count, full_digest=full_digest):
                if self._count != target_count:
                    raise StreamMismatchError(
                        count=self._count,
                        expected=b"",
                        actual=b"",
                        message=(
                            "Ended at different count\n"
                            f"actual: {self._count}, expected: {target_count}\n"
                            f"actual last:   {self._last!r}\n"
                            f"expected last: {self._mode.last_expected!r}"
                        ),
                    )
                if self._state.digest() != full_digest:
                    raise StreamMismatchError(
                        count=self._count,
                        expected=full_digest[: self.digest_size],
                        actual=self._state.digest()[: self.digest_size],
                        message="Final digest does not match",
                    )
