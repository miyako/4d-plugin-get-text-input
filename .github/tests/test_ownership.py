"""Compile production code and the whole unchanged SDK against a synthetic host.

macOS only. No 4D process, keyboard monitor, release build or publication.
The source root can be an immutable git-archive baseline.
"""

import argparse
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[2]
BASELINE = "7cd105517c7ff7a7c1ee4ae45c7886479a022329"
SCENARIOS = (
    "callback-return-owner",
    "json-setter-overwritten-empty-owner",
    "json-getter-owned-result",
    "json-command-failure-sentinel",
    "json-getter-failed-output",
    "json-stringify-success",
    "array-contract",
    "scalar-content-contract",
    "dispatcher-stubbed-input",
)
BASELINE_FAILURES = {
    "callback-return-owner": "FAIL: callback-return-owner",
    "json-setter-overwritten-empty-owner": "FAIL: json-setter-overwritten-empty-owner",
    "json-getter-owned-result": "FAIL: json-getter-owned-result",
    "json-command-failure-sentinel": "FAIL: never read raw-kind-zero/failed output",
    "json-getter-failed-output": "not a valid value for type 'PA_VariableKind'",
}


def verify_preserved(root, baseline):
    archive = subprocess.run(["git", "archive", BASELINE], cwd=ROOT,
                             capture_output=True, check=True).stdout
    exceptions = set() if baseline else {
        "4DPlugin-GET-TEXT-INPUT.cpp", "support/4DPlugin-JSON.cpp",
        "support/4DPlugin-JSON.h", "README.md",
    }
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        for member in files:
            if member.isfile() and member.name not in exceptions:
                actual = root / member.name
                assert actual.read_bytes() == files.extractfile(member).read(), member.name
    print("Preserved all baseline tracked files outside exact source/doc exemptions")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--expect-baseline-defects", action="store_true")
    args = parser.parse_args()
    root = args.source_root.resolve()
    verify_preserved(root, args.expect_baseline_defects)
    with tempfile.TemporaryDirectory(prefix="text-input-ownership-") as directory:
        binary = Path(directory) / "ownership"
        subprocess.run([
            "clang++", "-std=c++17", "-x", "objective-c++", "-g", "-O1",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-Wno-deprecated-declarations", "-Wno-multichar",
            "-I", str(root), "-I", str(root / "4D Plugin API"),
            "-I", str(root / "4D Plugin API/Classes"), "-I", str(root / "support"),
            str(ROOT / ".github/tests/ownership.mm"),
            str(root / "4D Plugin API/4DPluginAPI.c"),
            str(root / "4D Plugin API/Classes/C_TEXT.cpp"),
            str(root / "4D Plugin API/Classes/C_LONGINT.cpp"),
            str(root / "4D Plugin API/Classes/ARRAY_TEXT.cpp"),
            str(root / "4D Plugin API/Classes/C_BLOB.cpp"),
            str(root / "support/4DPlugin-JSON.cpp"),
            "-framework", "Cocoa", "-framework", "CoreGraphics",
            "-o", str(binary),
        ], check=True)
        failed = []
        env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0:halt_on_error=1",
               "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"}
        for scenario in SCENARIOS:
            result = subprocess.run([str(binary), scenario], env=env, text=True,
                                    capture_output=True, timeout=30)
            print(result.stdout + result.stderr, end="")
            if result.returncode:
                failed.append(scenario)
                if args.expect_baseline_defects:
                    marker = BASELINE_FAILURES.get(scenario)
                    assert marker and marker in result.stdout + result.stderr, scenario
            if not args.expect_baseline_defects and scenario == "json-getter-failed-output":
                assert result.stderr.count("PA_GetObjectProperty failed (-108)") == 10
            if not args.expect_baseline_defects and scenario == "json-command-failure-sentinel":
                assert result.stderr.count("JSON Stringify failed (-108)") == 4
        expected = list(SCENARIOS[:5]) if args.expect_baseline_defects else []
        if failed != expected:
            raise SystemExit(f"Unexpected failing scenarios: {failed}; expected: {expected}")
        print("Verified exact baseline failures" if expected else "All ownership scenarios passed")


if __name__ == "__main__":
    main()
