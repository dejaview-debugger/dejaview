import bdb
import getopt
import pdb
import sys
import traceback
import typing

from dejaview.counting.dejaview import DejaView
from dejaview.counting.error_detection import StreamMismatchError
from dejaview.counting.socket_client import DebugSocketClient
from dejaview.snapshots.snapshots import DEFAULT_SNAPSHOT_INTERVAL


class CustomPdb(DejaView.CustomPdb):
    def run(self, cmd, globals_=None, locals_=None):
        """Debug a statement executed via the exec() function.

        globals defaults to __main__.dict; locals defaults to globals.
        """
        if globals_ is None:
            import __main__  # noqa: PLC0415

            globals_ = __main__.__dict__
        if locals_ is None:
            locals_ = globals_
        self.reset()
        if isinstance(cmd, str):
            cmd = compile(cmd, "<string>", "exec")
        with self.dejaview.context():
            sys.settrace(self.trace_dispatch)
            try:
                exec(cmd, globals_, locals_)
            except bdb.BdbQuit:
                pass
            finally:
                self.quitting = True
                sys.settrace(None)


# copied from pdb.py
@typing.no_type_check
def main():
    opts, args = getopt.getopt(
        sys.argv[1:],
        "mhc:p:",
        ["help", "command=", "port=", "snapshot-interval=", "testing"],
    )

    if not args:
        print(pdb._usage)
        sys.exit(2)

    if any(opt in ["-h", "--help"] for opt, optarg in opts):
        print(pdb._usage)
        sys.exit()

    is_testing = any(opt in ["--testing"] for opt, optarg in opts)
    commands = [optarg for opt, optarg in opts if opt in ["-c", "--command"]]

    # Get port if specified
    port = None
    snapshot_interval = DEFAULT_SNAPSHOT_INTERVAL
    for opt, optarg in opts:
        if opt in ["-p", "--port"]:
            port = int(optarg)
        elif opt == "--snapshot-interval":
            snapshot_interval = int(optarg)

    module_indicated = any(opt in ["-m"] for opt, optarg in opts)
    cls = pdb._ModuleTarget if module_indicated else pdb._ScriptTarget
    target = cls(args[0])

    # This line imports the module which might make it bypass patching.
    # So skip this line here and run it after patching is set up in DejaView.
    # target.check()

    sys.argv[:] = args  # Hide "pdb.py" and pdb options from argument list

    # Note on saving/restoring sys.argv: it's a good idea when sys.argv was
    # modified by the script being debugged. It's a bad idea when it was
    # changed by the user from the command line. There is a "restart" command
    # which allows explicit specification of command line arguments.

    # Create socket client if port is specified
    socket_client = None
    if port is not None:
        socket_client = DebugSocketClient(port=port)
        if not socket_client.connect():
            print(f"Warning: Failed to connect to debug adapter on port {port}")
            socket_client = None

    dejaview = DejaView(
        socket_client=socket_client,
        snapshot_interval=snapshot_interval,
        is_testing=is_testing,
    )
    dejaview.counter.pdb_factory = lambda: CustomPdb(dejaview)
    my_pdb: CustomPdb = dejaview.get_pdb()
    my_pdb.rcLines.extend(commands)

    with dejaview.patching_context():
        target.check()
        is_initial = True
        while True:
            try:
                # Instead of actually restarting, we just rewind to the beginning.
                if is_initial:
                    is_initial = False
                    my_pdb._run(target)
                else:
                    dejaview.restart()
                if my_pdb._user_requested_quit:
                    break
                print("The program finished and will be restarted")
            except StreamMismatchError as e:
                print(f"Replay divergence detected at count {e.count}: {e.message}")
                print("Restarting the debugging session.")
            except pdb.Restart:
                print("Restarting", target, "with arguments:")
                print("\t" + " ".join(sys.argv[1:]))
            except SystemExit as e:
                # Dejaview should still restart on sys.exit() to avoid state loss.
                print("The program exited via sys.exit(). Exit status:", end=" ")
                print(e)
                print("Restarting", target, "with arguments:")
                print("\t" + " ".join(sys.argv[1:]))
            except SyntaxError:
                traceback.print_exc()
                sys.exit(1)
            except BaseException as e:
                traceback.print_exc()
                print("Uncaught exception. Entering post mortem debugging")
                print("Running 'cont' or 'step' will restart the program")
                t = e.__traceback__
                my_pdb.interaction(None, t)
                print(
                    "Post mortem debugger finished. The "
                    + target
                    + " will be restarted"
                )

    # Clean up socket connection
    if socket_client:
        socket_client.disconnect()
