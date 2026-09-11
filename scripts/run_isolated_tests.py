"""Run every unittest module in a fresh process to isolate native Qt contexts."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=Path("build/validation/isolated"))
parser.add_argument("--timeout", type=int, default=180)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
results = []
jobs = []
for file in sorted(Path("tests").glob("test_*.py")):
    module = "tests." + file.stem
    if module == "tests.test_ui":
        # This suite creates many native OpenGL contexts; isolate each case too.
        tree = ast.parse(file.read_text(encoding="utf-8"))
        jobs.extend((module + "." + cls.name + "." + method.name, file.stem + "-" + method.name)
                    for cls in tree.body if isinstance(cls, ast.ClassDef)
                    for method in cls.body if isinstance(method, ast.FunctionDef) and method.name.startswith("test_"))
    else:
        jobs.append((module, file.stem))
for module, label in jobs:
    try:
        completed = subprocess.run([sys.executable, "-X", "faulthandler", "-m", "unittest", module, "-v"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=args.timeout)
        output = completed.stdout.decode("utf-8", errors="replace")
        code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", errors="replace") + "\nMODULE TIMEOUT\n"
        code = -1
    (args.output / (label + ".log")).write_text(output, encoding="utf-8")
    total = re.search(r"Ran (\d+) tests?", output)
    failures = re.findall(r"^(?:FAIL|ERROR): (.+)$", output, re.M)
    result = dict(module=module, exit_code=code, tests=int(total[1]) if total else None, failures=failures)
    results.append(result)
    print(json.dumps(result), flush=True)
(args.output / "summary.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
raise SystemExit(any(item["exit_code"] for item in results))
