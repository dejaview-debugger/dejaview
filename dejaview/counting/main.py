import bdb
import getopt
import pdb
import sys
import traceback
import typing

from dejaview.counting.dejaview import DejaView
from dejaview.counting.socket_client import DebugSocketClient


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
        with self.dejaview:
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
    opts, args = getopt.getopt(sys.argv[1:], "mhc:p:", ["help", "command=", "port="])

    if not args:
        print(pdb._usage)
        sys.exit(2)

    if any(opt in ["-h", "--help"] for opt, optarg in opts):
        print(pdb._usage)
        sys.exit()

    commands = [optarg for opt, optarg in opts if opt in ["-c", "--command"]]

    # Get port if specified
    port = None
    for opt, optarg in opts:
        if opt in ["-p", "--port"]:
            port = int(optarg)
            break

    module_indicated = any(opt in ["-m"] for opt, optarg in opts)
    cls = pdb._ModuleTarget if module_indicated else pdb._ScriptTarget
    target = cls(args[0])

    target.check()

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

    dejaview = DejaView(socket_client=socket_client)
    dejaview.counter.pdb_factory = lambda: CustomPdb(dejaview)
    my_pdb: CustomPdb = dejaview.get_pdb()
    my_pdb.rcLines.extend(commands)
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
        except pdb.Restart:
            print("Restarting", target, "with arguments:")
            print("\t" + " ".join(sys.argv[1:]))
        except SystemExit as e:
            # In most cases SystemExit does not warrant a post-mortem session.
            print("The program exited via sys.exit(). Exit status:", end=" ")
            print(e)
            raise
        except SyntaxError:
            traceback.print_exc()
            sys.exit(1)
        except BaseException as e:
            traceback.print_exc()
            print("Uncaught exception. Entering post mortem debugging")
            print("Running 'cont' or 'step' will restart the program")
            t = e.__traceback__
            my_pdb.interaction(None, t)
            print("Post mortem debugger finished. The " + target + " will be restarted")

    # Clean up socket connection
    if socket_client:
        socket_client.disconnect()
