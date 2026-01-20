"""
To run this test, execute

    ledit uv run python3 -m dejaview dejaview/tests/programs/count_tests/driver.py

Credit: Patrick Lam
"""

import count_tests

def main():
    rv = count_tests.tests_in_file_contents(["TEST_CASE", "// nothing", "SCENARIO"])
    print (rv)

if __name__ == "__main__":
    main()
