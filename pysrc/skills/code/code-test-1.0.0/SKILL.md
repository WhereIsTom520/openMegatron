---
name: code_test
version: 1.0.0
category: code
description: Test generation, coverage analysis, test quality scoring, and mutation test suggestions.
risk: low
actions:
  - test
  - coverage
  - suggest_tests
  - test_quality
  - find_untested
---

# Code Test v1.0.0

Test generation and analysis. Suggests missing tests, evaluates test quality,
and identifies untested code paths.

## Actions

- **test**: Run the project's test suite. Returns pass/fail, exit code, output summary.

- **coverage**: Run tests with coverage (if tools available). Returns coverage percentage
  and per-file breakdown.

- **suggest_tests** `<file>`: Analyze a source file and suggest test cases for uncovered
  functions. Heuristically identifies edge cases, error paths, and boundary conditions.
  Returns a list of suggested test function stubs.

- **test_quality** `<test_file>`: Evaluate test quality — checks for assertions per test,
  mock usage, edge case coverage, and suggests improvements.

- **find_untested**: Compare source files against test files to find modules with
  missing or insufficient test coverage. Returns a prioritized list.

## Suggested Test Output Format

Each suggestion includes:
- `function`: The function to test
- `test_name`: Suggested test function name
- `scenario`: What is being tested
- `input_desc`: Description of test inputs
- `expected`: Expected behavior
- `stub`: Python/pytest stub code

## Notes

- Coverage requires `pytest-cov` (Python), `c8` or `istanbul` (JS), or `cargo-tarpaulin` (Rust).
- Test suggestions are heuristic — they identify patterns but don't execute the code.
- The tool runs locally and does not send code to external services.
