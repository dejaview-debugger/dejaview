import os
import random
import multiprocessing
import time

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
      manager.capture_snapshot()
  
  print(pid, "random number:", random.randint(0, 100))
  print(pid, res)
  manager.resume_snapshot()

class Snapshot:
  def __init__(self, queue, pid):
    assert pid != 0
    self.queue = queue
    self.pid = pid
  
  def resume(self):
    # TODO: we also need to pass recorded information from patched functions to the snapshot process
    self.queue.put("some information")
    # TODO: what to do with the current process?
    _, status = os.waitpid(self.pid, 0)
    exit(status >> 8)


class SnapshotManager:
  def __init__(self):
    self.snapshots = []

  def capture_snapshot(self):
    print("capturing snapshot")
    queue = multiprocessing.SimpleQueue()
    pid = os.fork()
    if pid == 0: # child process
      # suspend the child process
      print("snapshot paused")
      info = queue.get()
      queue.close()
      print("snapshot resumed with:", info)
    else: # parent process
      # stores the id of the fork
      self.snapshots.append(Snapshot(queue, pid))

  def resume_snapshot(self):
    # TODO: decide if we want to keep a copy of original snapshot after they have been resumed 
    if len(self.snapshots) > 0:
      snapshot = self.snapshots.pop()
      snapshot.resume()
    else:
      print("no snapshot to resume")

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
