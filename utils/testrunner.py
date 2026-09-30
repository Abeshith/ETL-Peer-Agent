import sys
import subprocess
from pathlib import Path
from typing import List
from utils.models import TestResult


def run_generated_tests(test_file_path: str) -> List[TestResult]:
    """Execute a generated test file using unittest.
    Returns a list of TestResult objects (one per test method).
    """
    test_path = Path(test_file_path)
    if not test_path.is_file():
        return []

    # Run with -v so each test is printed on its own line
    cmd = [sys.executable, "-m", "unittest", "-v", test_path.stem]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(test_path.parent),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        return [TestResult(
            test_name="Generated Test Execution",
            phase="Phase5",
            status="FAIL",
            finding=str(e),
            recommendation="Check generated test file for syntax errors",
            check_type="Generated Test",
            severity="High",
            database_target="N/A",
            stage2_relevance="Important",
        )]

    # unittest -v outputs to stderr in format: "test_001 (GeneratedTests.test_001) ... ok"
    output = proc.stderr or proc.stdout
    results: List[TestResult] = []
    idx = 0
    for line in output.splitlines():
        line = line.strip()
        if " ... " in line:
            idx += 1
            parts = line.rsplit(" ... ", 1)
            test_label = parts[0].strip()
            outcome = parts[1].strip().lower() if len(parts) > 1 else "fail"
            status = "PASS" if outcome == "ok" else "FAIL"
            results.append(TestResult(
                test_id=f"GEN_{idx:03d}",
                test_name=test_label,
                phase="Phase5",
                status=status,
                finding="Test passed" if status == "PASS" else f"Test failed: {outcome}",
                recommendation="None" if status == "PASS" else "Review test scenario",
                check_type="Generated Test",
                severity="Info" if status == "PASS" else "Medium",
                database_target="N/A",
                stage2_relevance="Required",
            ))

    # If nothing was parsed but the file ran, create one summary result
    if not results:
        summary_pass = proc.returncode == 0
        results.append(TestResult(
            test_id="GEN_001",
            test_name="Generated Test Suite",
            phase="Phase5",
            status="PASS" if summary_pass else "FAIL",
            finding="All generated tests passed" if summary_pass else f"Test failures detected\n{output}",
            recommendation="None" if summary_pass else "Review generated test output",
            check_type="Generated Test",
            severity="Info" if summary_pass else "High",
            database_target="N/A",
            stage2_relevance="Required",
        ))

    return results
