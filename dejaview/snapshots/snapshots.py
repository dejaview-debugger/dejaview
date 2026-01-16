import atexit
import os
import random
import signal
import struct
from typing import Any

# Try cloudpickle first for dynamic-function coverage
try:
    import cloudpickle  # type: ignore[import-untyped]

    _serializer_lib: Any = cloudpickle
    _USE_CLOUDPICKLE = True
except Exception:
    import pickle as _serializer_lib

    _USE_CLOUDPICKLE = False


# --- HybridQueue: pipe + cloudpickle/pickle ---
class HybridQueue:
    """
    Simple blocking one-way queue implemented with an os.pipe and a length-prefixed
    serialized payload. Uses cloudpickle when available to support serialization of
    dynamic Python objects like nested functions, lambdas, and dynamically defined
    classes. Falls back to standard pickle if cloudpickle is not available.

    The HybridQueue decouples the transport layer (os.pipe) from the serialization
    layer (cloudpickle/pickle), providing byte-level control over interprocess
    communication. Messages are length-prefixed with 4 bytes to delimit boundaries
    in the byte stream.
    """

    # used for length prefix for each message sent in pipe
    _HDR = struct.Struct("!I")  # 4 byte big-endian length

    def __init__(self):
        # file descriptors to access bytes in pipe
        self._r_fd, self._w_fd = os.pipe()
        self._closed = False

        # writer/read wrappers: use serializer library dumps/loads
        self._serializer = _serializer_lib.dumps
        self._deserializer = _serializer_lib.loads

    # add a new item to the queue
    def put(self, obj):
        # Serialize the object using cloudpickle or pickle
        data = self._serializer(obj)
        # Write 4-byte length prefix
        hdr = HybridQueue._HDR.pack(len(data))
        self._write_all(hdr)
        # Write serialized data
        self._write_all(data)

    # get an item from the queue
    def get(self):
        # Read 4-byte header containing message length
        hdr = self._read_n(HybridQueue._HDR.size)
        if not hdr:
            raise EOFError("pipe closed")
        (nbytes,) = HybridQueue._HDR.unpack(hdr)  # Extract length
        payload = self._read_n(nbytes)  # Read exact number of bytes
        obj = self._deserializer(payload)  # Deserialize back to object
        return obj

    # close the queue
    def close(self):
        if not self._closed:
            try:
                os.close(self._w_fd)
            except Exception:
                pass
            try:
                os.close(self._r_fd)
            except Exception:
                pass
            self._closed = True

    # internal write all bytes helper
    def _write_all(self, data: bytes):
        off = 0  # how many bytes written so far
        while off < len(data):  # while not all bytes written
            n = os.write(self._w_fd, data[off:])  # write remaining bytes
            if n == 0:  # no bytes written
                raise BrokenPipeError("write to pipe failed")
            off += n  # update offset

    # internal read n bytes helper
    def _read_n(self, n):
        chunks: list[bytes] = []  # collected chunks in each call
        got = 0  # how many bytes read so far
        while got < n:  # while not all bytes read
            chunk = os.read(self._r_fd, n - got)  # read remaining bytes
            if not chunk:
                # EOF
                return b"".join(chunks)  # return combined bytes
            chunks.append(chunk)  # else append chunk
            got += len(chunk)  # increment byte count
        return b"".join(chunks)  # return combined bytes


# Test program for debug
def test():
    manager = SnapshotManager()
    res = [0, 1]

    for i in range(10):
        f_1 = res[-1]
        f_2 = res[-2]
        f_next = f_1 + f_2
        res.append(f_next)
        pid = os.getpid()
        print(pid, f_next)
        if i == 5:
            state = manager.capture_snapshot()
            if state is not None:
                print("got state:", state)

    input("input: ")
    print(pid, "random state:", hash(random.getstate()))
    print(pid, "random number:", random.randint(0, 100))
    print(pid, res)
    manager.resume_snapshot("message from fork")


# --- Snapshot class ---
class Snapshot:
    def __init__(self, queue, pid):
        assert pid != 0
        self.queue = queue
        self.pid = pid

    # resume the snapshot by sending state to the child process
    def resume(self, state: Any):
        # send the resume state to the child snapshot
        self.queue.put(state)
        # wait for the child process to finish then exit with its status
        _, status = os.waitpid(self.pid, 0)
        exit(status >> 8)


# --- SnapshotManager class ---
class SnapshotManager:
    def __init__(self):
        self.snapshots = []
        self.children = []

        # register `cleanup` routine to terminate unkilled child processes
        atexit.register(self.cleanup)

    def cleanup(self):
        # cleans up child processes on exit
        unkilled_children = []
        for pid in self.children:
            try:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)
            except OSError:
                unkilled_children.append(pid)
        print(f"Possible unkilled child process: {unkilled_children}")

    # capture a snapshot by forking the process
    def capture_snapshot(self):
        # use HybridQueue instead of multiprocessing.SimpleQueue()
        queue = HybridQueue()
        random_state = random.getstate()
        pid = os.fork()
        # restore random state in both sides so sequences remain identical
        random.setstate(random_state)
        if pid == 0:  # child process
            # suspend the child process until parent resumes it
            # SIGTERM handler for child to exit cleanly if parent kills it
            def handle_sigterm(signum, frame):
                print(f"[Child {os.getpid()}] Received SIGTERM, exiting")
                os._exit(0)

            signal.signal(signal.SIGTERM, handle_sigterm)

            state = queue.get()
            queue.close()
            # create another fork to use when we resume again
            new_state = self.capture_snapshot()
            return new_state or state
        else:  # parent process
            # stores the id of the fork
            self.snapshots.append(Snapshot(queue, pid))

            # keep track of pid so `SnapshotManager.cleanup` can terminate it later
            self.children.append(pid)
            return None

    # resume a snapshot by sending state to the child process
    def resume_snapshot(self, state: Any):
        # TODO: decide if we want to keep a copy of original snapshot
        # after they have been resumed
        if len(self.snapshots) > 0:
            snapshot = self.snapshots.pop()

            # remove child's pid that will run its own `cleanup` routine from the parent
            # to avoid cleaning it up twice
            self.children = [c for c in self.children if c != snapshot.pid]
            snapshot.resume(state)
        else:
            # no snapshot to resume
            pass


"""
snapshots contains:
- instruction count
- variable state
- standard library functions

global snapshots contains:
- highest instruction count
"""

if __name__ == "__main__":
    test()
