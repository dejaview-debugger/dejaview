import atexit
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from functools import cache
from pathlib import Path
from textwrap import dedent
from typing import List, Optional

import pexpect  # type: ignore[import-untyped]

_TEMP_DIRS: list[Path] = []


# Clean up garbage at exit
@atexit.register
def _cleanup_temp_dirs() -> None:
    for path in _TEMP_DIRS:
        shutil.rmtree(path, ignore_errors=True)


class DebugCommand(Enum):
    STEP = "n"
    STEP_INTO = "s"
    STEP_OUT = "r"
    CONTINUE = "c"
    BACK = "back"
    QUIT = "quit"
    WHERE = "where"
    LIST = "l"


@dataclass
class DebuggerState:
    """
    The captured debugger state at a specific point
    """

    line_number: Optional[int] = None
    filename: Optional[str] = None
    function_name: Optional[str] = None
    where_output: str = ""  # Output from 'where' command
    console_output: str = ""  # Console output from the last command
    # Adding local variable values here would be ideal,
    # but I think we should do this once snapshot details are finalized


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
        msg = self.expect_end()
        assert "Traceback (most recent call last):" not in msg, msg
        self.close()
        assert self.exitstatus == 0, f"Process exited with status {self.exitstatus}"

    def send_command(self, cmd: DebugCommand | str) -> str:
        """
        Send a command and return the output.
        """
        if isinstance(cmd, DebugCommand):
            cmd = cmd.value
        self.sendline(cmd)
        return self.expect_prompt()

    def capture_state(self) -> DebuggerState:
        """
        Capture the current debugger state by querying where and locals.
        Returns a DebuggerState object with location and variable info.
        """
        state = DebuggerState()

        # Get current location from 'where' command
        where_output = self.send_command("where")
        state.where_output = where_output

        # Parse location from where output
        # Typical format: "> /path/to/file.py(42)function_name()"
        # Another example: "> /tmp/tmp5f5n5p1c.py(2)<module>()"
        location_match = re.search(r">\s+(.+?)\((\d+)\)(.+)\(\)", where_output)
        if location_match:
            state.filename = location_match.group(1)
            state.line_number = int(location_match.group(2))
            state.function_name = location_match.group(3)

        return state

    def execute_command_sequence(
        self, commands: List[DebugCommand]
    ) -> List[DebuggerState]:
        """
        Execute a sequence of commands and capture state and console output after each.
        Returns a list of DebuggerState objects with console_output populated.
        """
        states = []
        for cmd in commands:
            console_output = self.send_command(cmd)
            state = self.capture_state()
            state.console_output = console_output
            states.append(state)
        return states

    def assert_line_number(self, expected_line_number: int) -> str:
        """
        Consume output from the previous command and assert the current line number.

        This method first calls expect_prompt() to consume any pending output,
        then queries the debugger state to verify the current line number.

        Args:
            expected_line_number: The line number the debugger should be on.

        Outputs:
            str: The string output from the previous command

        Raises:
            AssertionError: If the current line number doesn't match expected.
        """
        output = self.expect_prompt()
        state = self.capture_state()
        assert state.line_number == expected_line_number, (
            f"Expected line {expected_line_number}, found {state.line_number}\n"
            f"Full output:\n{output}"
        )
        return output


@dataclass
class SourceFile:
    name: str
    content: str


def launch_dejaview(
    main: str | SourceFile,
    *rest: SourceFile,
    timeout: float = 10,
    snapshot_interval: int = 2,
    stress_test: bool = True,
) -> DejaViewInstance:
    """
    Launch DejaView with the given program string.

    Args:
        main: The main program to debug.
        *rest: Additional source files if needed.
        timeout: Timeout for expecting outputs, in seconds.
        snapshot_interval: Interval for automatic snapshotting in DejaView.
        stress_test: Whether to enable stress testing mode in DejaView.
    """
    if isinstance(main, str):
        main = SourceFile("main.py", main)
    files = [main, *rest]

    tmpdir = Path(tempfile.mkdtemp())
    _TEMP_DIRS.append(tmpdir)
    for source_file in files:
        path = tmpdir / source_file.name
        path.write_text(dedent(source_file.content).strip(), encoding="utf-8")
    command = [
        "uv",
        "run",
        "python3",
        "-m",
        "dejaview",
        "--snapshot-interval",
        str(snapshot_interval),
        "--testing" if stress_test else "",
        str(tmpdir / main.name),
    ]
    command = [arg for arg in command if arg]  # Remove empty args
    d = DejaViewInstance(
        command[0],
        command[1:],
        cwd=get_repo_root(),
        encoding="utf-8",
        timeout=timeout,
    )
    d.delaybeforesend = None
    return d


class PropertyTester:
    """Test properties and invariants of the debugger."""

    @staticmethod
    def test_determinism_property(
        program: str, command_sequence: List[DebugCommand], num_runs: int = 10
    ) -> None:
        """
        Test that executing the same command sequence produces the same results.

        Args:
            program: The program to test
            command_sequence: Commands to execute
            num_runs: Number of times to run the test

        Raises:
            AssertionError: If any run produces different results than the first run.
        """
        all_states: List[List[DebuggerState]] = []

        for run_idx in range(num_runs):
            d = launch_dejaview(program)
            d.expect_prompt()
            states = d.execute_command_sequence(command_sequence)
            all_states.append(states)
            d.quit()

        # Compare all runs to the first run
        first_run_states = all_states[0]

        for run_idx, run_states in enumerate(all_states[1:], start=1):
            assert len(run_states) == len(first_run_states), (
                f"Run {run_idx}: Number of states differ: "
                f"{len(run_states)} vs {len(first_run_states)}"
            )
            zipped = zip(first_run_states, run_states)
            for step_idx, (state1, state2) in enumerate(zipped):
                # Don't test for filename, since these are different instances
                assert state1.line_number == state2.line_number, (
                    f"Run {run_idx}, Step {step_idx}: Line numbers differ: "
                    f"{state1.line_number} vs {state2.line_number}"
                )
                redacted_console_output_1 = state1.console_output
                redacted_console_output_2 = state2.console_output
                # Remove all occurrences of the filename in the console output
                if state1.filename:
                    redacted_console_output_1 = redacted_console_output_1.replace(
                        state1.filename, "<redacted>"
                    )
                if state2.filename:
                    redacted_console_output_2 = redacted_console_output_2.replace(
                        state2.filename, "<redacted>"
                    )
                assert redacted_console_output_1 == redacted_console_output_2, (
                    f"Run {run_idx}, Step {step_idx}: Console outputs differ:\n"
                    f"First run:\n{state1.console_output}\n\n"
                    f"Run {run_idx}:\n{state2.console_output}"
                )

    @staticmethod
    def test_idempotence_property(d: DejaViewInstance, forward_steps: int = 1) -> None:
        """
        Test that stepping forward -> back -> forward reaches the same state.
        The states at each step should match between the first and second
        forward passes.

        The test instance should NOT reach the end of the program while doing
        forward steps, otherwise the program restart will lead to test fails.

        Raises:
            AssertionError: If states don't match at any step.
        """
        # Step forward and capture state and console output at each step
        first_pass_states = []
        for _ in range(forward_steps):
            console_output = d.send_command(DebugCommand.STEP)
            state = d.capture_state()
            state.console_output = console_output
            first_pass_states.append(state)

        # Step back the same amount of steps
        for _ in range(forward_steps):
            d.send_command(DebugCommand.BACK)

        # Step forward again and capture state and console output at each step
        second_pass_states = []
        for _ in range(forward_steps):
            console_output = d.send_command(DebugCommand.STEP)
            state = d.capture_state()
            state.console_output = console_output
            second_pass_states.append(state)

        # All states and outputs at each step should match
        assert len(first_pass_states) == len(second_pass_states), (
            f"State list lengths differ: "
            f"{len(first_pass_states)} vs {len(second_pass_states)}"
        )

        zipped = zip(first_pass_states, second_pass_states)
        for i, (state1, state2) in enumerate(zipped):
            assert state1.line_number == state2.line_number, (
                f"Step {i}: Line numbers differ: "
                f"{state1.line_number} vs {state2.line_number}"
            )
            assert state1.filename == state2.filename, (
                f"Step {i}: Filenames differ: {state1.filename} vs {state2.filename}"
            )
            assert state1.console_output == state2.console_output, (
                f"Step {i}: Console outputs differ:\n"
                f"First pass:\n{state1.console_output}\n\n"
                f"Second pass:\n{state2.console_output}"
            )
