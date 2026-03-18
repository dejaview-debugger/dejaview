import re
import subprocess
from pathlib import Path

import pytest

from dejaview.tests.util import get_repo_root, launch_dejaview_with_python

# All tests in this file run on the same xdist worker so the session-scoped
# black_python fixture (which runs get_black.sh) executes exactly once.
pytestmark = pytest.mark.xdist_group("black")


BLACK_TEST_DIR = Path(get_repo_root()) / "test" / "black"


@pytest.fixture(scope="session")
def black_python() -> str:
    python = BLACK_TEST_DIR / "black" / ".venv" / "bin" / "python"
    get_black = BLACK_TEST_DIR / "get_black.sh"
    if (
        not python.exists()
        or subprocess.run(
            [str(python), "-c", "import black"],
            check=False,
            capture_output=True,
        ).returncode
    ):
        subprocess.run([str(get_black)], check=True)
    return str(python)


def _extract_black_output(pexpect_output: str) -> str:
    # Strip echoed command and dejaview restart/exit messages; keep program output.
    # Remove the echoed "c\r\n" at the start
    text = re.sub(r"^c\r?\n", "", pexpect_output)
    # Cut at dejaview's restart/exit announcement ("The program finished..." etc.)
    text = re.split(r"\r?\nThe program ", text)[0]
    return text


def test_black_continue_twice_identical_output(black_python: str) -> None:
    target = BLACK_TEST_DIR / "test.py"
    d = launch_dejaview_with_python(
        black_python,
        "-m",
        "black",
        str(target),
        "--diff",
        "--target-version",
        "py312",
        timeout=30,
    )
    d.expect_prompt()

    output1 = d.send_command("c")
    output2 = d.send_command("c")

    black_output1 = _extract_black_output(output1)
    black_output2 = _extract_black_output(output2)

    assert black_output1 == black_output2, (
        f"Black outputs differ between runs:\n"
        f"First run:\n{black_output1}\n\n"
        f"Second run:\n{black_output2}"
    )
    d.quit()


def test_black_continue_to_main(black_python: str) -> None:
    """Decorator frames should be skipped."""
    target = BLACK_TEST_DIR / "test.py"
    d = launch_dejaview_with_python(
        black_python,
        "-m",
        "black",
        str(target),
        "--diff",
        "--target-version",
        "py312",
        timeout=5,
    )
    d.expect_prompt()

    assert "patched_main()" in d.send_command("n")
    assert "def patched_main(" in d.send_command("s")
    assert 'if getattr(sys, "frozen", False):' in d.send_command("s")
    assert "main()" in d.send_command("n")
    assert "def read_pyproject_toml(" in d.send_command("s")
    d.send_command("b 570")  # first line of main() in the pinned version of black
    assert "__init__.py(570)main()" in d.send_command("c")
    d.quit()
