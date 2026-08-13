"""Run the offline verification suite.

    python tests/run_tests.py

These checks exercise the logic that must be correct before any credential is
involved: CSV ingestion and mapping, prompt composition, task planning, scene
status derivation, progress maths, storage verification, export packaging,
provider error classification and API profile rotation.

They make no network calls and touch no database, so they are safe to run at
any time. End-to-end generation against Google AI and Supabase is a separate,
credentialed step — see README.md.
"""

import os
import subprocess
import sys

TESTS = ["test_pipeline.py", "test_storage.py", "test_provider.py", "test_regressions.py"]


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    failed = []

    for name in TESTS:
        print(f"\n{'=' * 64}\n  {name}\n{'=' * 64}")
        result = subprocess.run([sys.executable, os.path.join(here, name)])
        if result.returncode != 0:
            failed.append(name)

    print(f"\n{'=' * 64}")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print(f"All {len(TESTS)} suites passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
