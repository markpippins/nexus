#!/usr/bin/env python3
"""run_all.py — Run all Nexus audit test suites.

Usage:
    python3 nexus/tests/run_all.py          # run all suites
    python3 nexus/tests/run_all.py db       # run only DB integrity tests
    python3 nexus/tests/run_all.py vocabulary  # run only vocabulary tests
"""
import importlib.util
import sys
import os

NEXUS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(NEXUS_ROOT)

SUITES = ["db", "vocabulary", "guards", "pipeline", "scheduled", "nightshift", "migrations", "bin"]

def run_suite(name):
    # The bin suite is directory-based: every tests/bin/*_checks.py runs and
    # results are aggregated. Other suites keep the single checks.py contract.
    if name == "bin":
        bin_dir = os.path.join(NEXUS_ROOT, "tests", "bin")
        files = sorted(
            f for f in os.listdir(bin_dir)
            if f == "checks.py" or f.endswith("_checks.py")
        )
        if not files:
            return (0, 0, 0)
        tp = tf = ts = 0
        for fname in files:
            mod_path = os.path.join(bin_dir, fname)
            spec = importlib.util.spec_from_file_location(
                f"tests.bin.{fname[:-3]}", mod_path
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            p, f, s = mod.run()
            tp += p
            tf += f
            ts += s
        return (tp, tf, ts)
    mod_path = os.path.join(NEXUS_ROOT, "tests", name, "checks.py")
    spec = importlib.util.spec_from_file_location(f"tests.{name}.checks", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run()

def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else SUITES
    results = {}
    total_pass = total_fail = total_skip = 0

    for name in targets:
        print(f"\n{'='*60}")
        print(f"  Suite: {name}")
        print(f"{'='*60}")
        try:
            passed, failed, skipped = run_suite(name)
            results[name] = (passed, failed, skipped)
            total_pass += passed
            total_fail += failed
            total_skip += skipped
        except Exception as e:
            print(f"  SUITE ERROR: {e}")
            import traceback
            traceback.print_exc()
            results[name] = (0, 1, 0)
            total_fail += 1

    print(f"\n{'='*60}")
    print(f"  TOTAL: {total_pass} passed, {total_fail} failed, {total_skip} skipped")
    print(f"{'='*60}")
    return 1 if total_fail > 0 else 0

if __name__ == "__main__":
    sys.exit(main())
