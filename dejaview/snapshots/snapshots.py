import atexit
import os
import random
import signal
import struct
import io
import socket
import inspect
import types
import warnings
from typing import Any

# Try cloudpickle first for dynamic-function coverage
try:
    import cloudpickle as _serializer_lib
    _USE_CLOUDPICKLE = True
except Exception:
    import pickle as _serializer_lib
    _USE_CLOUDPICKLE = False

# Shadow wrapper registry
class ShadowRegistry:
    """
    Register handlers for types that cannot be reliably serialized.
    Handler must implement (serialize(obj) -> serializable_proxy, deserialize(proxy) -> obj).
    A proxy is a serializable representation of the original object, which stores enough info 
    to reconstruct the object upon deserialization.
    """
    def __init__(self):
        self._handlers = []

    # helper method to register a handler 
    def register(self, type_check, serializer_fn, deserializer_fn):
        # typecheck: function that takes an object and returns True if this handler can serialize it 
        # serializer_fn: function that takes an object and returns a serializable proxy
        # deserializer_fn: function that takes a serializable proxy and returns the original object
        self._handlers.append((type_check, serializer_fn, deserializer_fn))

    # serialize an object using registered handlers
    # returns: shadow proxy if handled, else original object
    def serialize(self, obj):
        for type_check, s_fn, _ in self._handlers:
            try:
                if type_check(obj):
                    proxy = s_fn(obj)
                    # mark proxy for shadow deserialization
                    return {"__shadow__": True, "__type__": s_fn.__name__, "data": proxy}
            except Exception:
                # handler failed, try next
                continue
        return obj  # no handler — return original (may still fail serialization)

    # deserialize an object using registered handlers
    # returns: original object if handled, else input candidate
    def deserialize(self, candidate):
        # if candidate is a dictionary and marked as shadow proxy
        if isinstance(candidate, dict) and candidate.get("__shadow__"):
            type_name = candidate.get("__type__")
            data = candidate.get("data")
            for _, _, d_fn in self._handlers:
                if d_fn.__name__ == type_name:
                    try:
                        return d_fn(data) # reconstruct original object
                    except Exception:
                        warnings.warn(f"shadow deserializer {type_name} failed")
                        return candidate
            # no matching deserializer found
            return candidate
        return candidate

# initialize global shadow registry
_shadow_registry = ShadowRegistry()

# --- File shadow handler ---
def _is_file(obj):
    return isinstance(obj, io.IOBase) 

def _serialize_file(f):
    # Only support named files that can be reopened; otherwise provide metadata fallback.
    name = getattr(f, "name", None)
    mode = getattr(f, "mode", None)
    try:
        pos = f.tell() 
    except Exception:
        pos = None
    if name and isinstance(name, str):
        return {"kind": "file", "name": name, "mode": mode, "pos": pos}
    else:
        # unnamed file (e.g., BytesIO) -> capture contents
        try:
            f.seek(0)
            content = f.read()
            return {"kind": "memoryfile", "content": content, "pos": pos}
        except Exception:
            return {"kind": "file_unserializable", "repr": repr(f)}

def _deserialize_file(meta):
    kind = meta.get("kind")
    if kind == "file":
        name = meta.get("name")
        mode = meta.get("mode") or "rb"
        pos = meta.get("pos") or 0
        try:
            f = open(name, mode)
            try:
                f.seek(pos)
            except Exception:
                pass
            return f
        except Exception:
            warnings.warn(f"could not reopen file {name}")
            return None
    elif kind == "memoryfile":
        content = meta.get("content", b"")
        bio = io.BytesIO(content)
        pos = meta.get("pos") or 0
        try:
            bio.seek(pos)
        except Exception:
            pass
        return bio
    else:
        return None

# register file shadow handler
_shadow_registry.register(_is_file, _serialize_file, _deserialize_file)

# --- Socket shadow handler ---
def _is_socket(obj):
    return isinstance(obj, socket.socket)

def _serialize_socket(s):
    try:
        sockname = s.getsockname()
    except Exception:
        sockname = None
    try:
        peer = s.getpeername()
    except Exception:
        peer = None
    fam = getattr(s, "family", None)
    typ = getattr(s, "type", None)
    return {"kind": "socket", "family": fam, "type": typ, "sockname": sockname, "peer": peer}

def _deserialize_socket(meta):
    # Best-effort: only attempt for TCP sockets with a peer address
    kind = meta.get("kind")
    if kind != "socket":
        return None
    fam = meta.get("family")
    peer = meta.get("peer")
    typ = meta.get("type")
    if peer and (fam in (socket.AF_INET, socket.AF_INET6) and typ & socket.SOCK_STREAM):
        # try reconnect
        try:
            host, port = peer[:2]
            s = socket.create_connection((host, port))
            return s
        except Exception:
            warnings.warn(f"could not reconnect to socket peer {peer}")
            return None
    return None

# register socket shadow handler
_shadow_registry.register(_is_socket, _serialize_socket, _deserialize_socket)

# --- HybridQueue: pipe + cloudpickle/pickle + shadow proxies ---
class HybridQueue:
    """
    Simple blocking one-way queue implemented with an os.pipe and a length-prefixed
    serialized payload. Uses cloudpickle when available to maximize function coverage.
    If an object cannot be serialized, the ShadowRegistry is consulted to produce a
    serializable proxy which is reconstructed on receive.
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
        # Try direct serialization
        try:
            data = self._serializer(obj)
        except Exception:
            # attempt shadow serialization
            proxy = _shadow_registry.serialize(obj)
            try:
                data = self._serializer(proxy)
            except Exception:
                # as a last resort, send a repr proxy
                fallback = {"__shadow__": True, "__type__": "repr_fallback", "data": repr(obj)}
                data = self._serializer(fallback)
        # write length of data 
        hdr = HybridQueue._HDR.pack(len(data))
        self._write_all(hdr)
        # write data 
        self._write_all(data)

    # get an item from the queue
    def get(self):
        # read 4 bytes header
        hdr = self._read_n(HybridQueue._HDR.size)
        if not hdr:
            raise EOFError("pipe closed")
        (nbytes,) = HybridQueue._HDR.unpack(hdr) # read length 
        payload = self._read_n(nbytes) # read data 
        obj = self._deserializer(payload) # deserialize data 
        # check if deserialized object is shadow proxy, reconstruct if necessary
        obj = _shadow_registry.deserialize(obj)
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
        off = 0 # how many bytes written so far
        while off < len(data): # while not all bytes written
            n = os.write(self._w_fd, data[off:]) # write remaining bytes
            if n == 0: # no bytes written 
                raise BrokenPipeError("write to pipe failed")
            off += n # update offset

    # internal read n bytes helper
    def _read_n(self, n):
        chunks = [] # collected chunks in each call 
        got = 0 # how many bytes read so far 
        while got < n: # while not all bytes read
            chunk = os.read(self._r_fd, n - got) # read remaining bytes
            if not chunk: 
                # EOF
                return b"".join(chunks) # return combined bytes
            chunks.append(chunk) # else append chunk 
            got += len(chunk) # increment byte count
        return b"".join(chunks) # return combined bytes

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
