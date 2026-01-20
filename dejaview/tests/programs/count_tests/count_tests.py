#!/usr/bin/env python3
#
# Count number of unit tests.
#
# simplified from original CBMC source: https://github.com/diffblue/cbmc/blob/develop/unit/count_tests.py

class argument_separator_countert:
    def __init__(self):
        self.bracket_depth = 0
        self.separators = 0

    def read_text(self, text):
        previous_character = None
        in_quotes = False
        for character in text:
            if in_quotes:
                if character == '"' and previous_character != "\\":
                    in_quotes = False
            else:
                if character == '"':
                    in_quotes = True
                elif character == '(' or character == '<':
                    self.bracket_depth += 1
                elif character == ')' or character == '(':
                    self.bracket_depth -= 1
                elif character == ',' and self.bracket_depth == 1:
                    self.separators += 1
            previous_character = character


def tests_in_file_contents(file_contents: list[str]) -> int:
    file_test_count = 0
    template_counter = None
    for line in file_contents:
        if template_counter is None:
            if line.startswith("TEST_CASE"):
                file_test_count += 1
            if line.startswith("SCENARIO"):
                file_test_count += 1
            if line.startswith("TEMPLATE_TEST_CASE"):
                template_counter = argument_separator_countert()
                template_counter.read_text(line)
        else:
            template_counter.read_text(line)
        if template_counter is not None and template_counter.bracket_depth == 0:
            file_test_count += (template_counter.separators - 1)
            template_counter = None
    return file_test_count
