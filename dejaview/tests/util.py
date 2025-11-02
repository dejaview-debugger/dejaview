import subprocess
import tempfile
from functools import cache
from pathlib import Path
from textwrap import dedent

import pexpect


@cache
def get_repo_root() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        cwd=Path(__file__).resolve().parent,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


class DejaViewInstance(pexpect.spawn):
    prompt = "(Pdb) "

    def expect_prompt(self) -> str:
        """
        Expect a Pdb prompt and return the output before it.
        """
        self.expect_exact(self.prompt)
        return self.before

    def expect_end(self, timeout: float = 2) -> str:
        """
        Expect EOF and return the output before it.
        """
        i = self.expect_exact([pexpect.EOF, pexpect.TIMEOUT], timeout=timeout)
        assert i == 0, f"Expected EOF, got TIMEOUT with output:\n{self.before}"
        return self.before

    def quit(self):
        """
        Send the quit command and expect EOF.
        """
        self.sendline("quit")
        self.expect_end()
        # TODO: assert that no EOF error happens during quit


def launch_dejaview(program: str, timeout: float = 10) -> DejaViewInstance:
    """
    Launch DejaView with the given program string.
    """
    # Delete on close instead of scope exit
    # since we don't know the lifetime of the DejaViewInstance
    tmpfile = tempfile.NamedTemporaryFile(
        suffix=".py", delete=False, delete_on_close=True
    )
    tmpfile.write(dedent(program).encode("utf-8"))
    tmpfile.flush()
    tmpfile.seek(0)
    command = [
        "uv",
        "run",
        "python3",
        "-m",
        "dejaview",
        tmpfile.name,
    ]
    d = DejaViewInstance(
        command[0],
        command[1:],
        cwd=get_repo_root(),
        encoding="utf-8",
        timeout=timeout,
    )
    d.delaybeforesend = None
    return d
