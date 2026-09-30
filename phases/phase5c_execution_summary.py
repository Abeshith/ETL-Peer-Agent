"""
Phase 5C: Execution Summary
-----------------------------
Collects Phase 5B execution results and produces a consolidated summary:

  Executed Tests : 7
  Passed         : 5
  Failed         : 1
  Skipped        : 1
  Duration       : 12.4s
  Final Risk     : MEDIUM

Also emits a single TestResult (SUMMARY record) and a structured dict
consumed by Phase 6 and the report generator.
"""

from typing import List, Dict, Any
from utils.models import TestResult


class ExecutionSummaryAgent:
    """
    Phase 5C: Consumes Phase 5B TestResult objects and produces:
      1. A console-ready summary string
      2. A summary TestResult for the report
      3. A structured dict for Phase 6 risk analysis
    """

    def __init__(self, phase5b_results: List[TestResult], metadata: dict):
        self.phase5b_results = phase5b_results
        self.metadata = metadata
        self.summary_dict: Dict[str, Any] = {}
        self.test_results: List[TestResult] = []

    def run(self) -> List[TestResult]:
        self._compute_summary()
        # Only include executed tests (PASS/FAIL/SKIPPED) — not GENERATED placeholders
        self.test_results = [r for r in self.phase5b_results if r.status != "GENERATED"]
        return self.test_results

    def get_summary_dict(self) -> Dict[str, Any]:
        """Return structured summary for use by Phase 6."""
        return self.summary_dict

    def get_console_summary(self) -> str:
        """Return a formatted console-ready summary string (ASCII-safe for Windows)."""
        s = self.summary_dict
        lines = [
            "",
            "  +------------------------------------------+",
            "  |        PHASE 5 -- EXECUTION SUMMARY      |",
            "  +------------------------------------------+",
            f"  |  Executed Tests : {str(s.get('executed', 0)):<24}|",
            f"  |  Passed         : {str(s.get('passed', 0)):<24}|",
            f"  |  Failed         : {str(s.get('failed', 0)):<24}|",
            f"  |  Skipped        : {str(s.get('skipped', 0)):<24}|",
            f"  |  Duration       : {str(s.get('duration_str', 'N/A')):<24}|",
            f"  |  Final Risk     : {str(s.get('risk', 'UNKNOWN')):<24}|",
            "  +------------------------------------------+",
        ]
        if s.get("failed_tests"):
            lines.append("  Failed tests:")
            for t in s["failed_tests"]:
                lines.append(f"    [FAIL] {t}")
        return "\n".join(lines)

    # ─── Internal helpers ──────────────────────────────────────────────────────

    def _compute_summary(self):
        results = self.phase5b_results
        executed = len(results)
        passed = sum(1 for r in results if r.status == "PASS")
        failed = sum(1 for r in results if r.status == "FAIL")
        skipped = sum(1 for r in results if r.status == "SKIPPED")

        failed_tests = [r.test_name for r in results if r.status == "FAIL"]

        # Derive risk level from failure rate
        if executed == 0:
            risk = "UNKNOWN"
        elif failed == 0:
            risk = "LOW"
        elif failed / executed >= 0.5:
            risk = "HIGH"
        elif failed / executed >= 0.25:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        # Collect durations from finding strings if available (embedded as "Xms")
        import re
        total_ms = 0
        for r in results:
            match = re.search(r"(\d+)ms", r.finding or "")
            if match:
                total_ms += int(match.group(1))
        duration_str = f"{total_ms / 1000:.1f}s" if total_ms else "N/A"

        self.summary_dict = {
            "executed": executed,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "duration_str": duration_str,
            "risk": risk,
            "failed_tests": failed_tests,
            "pass_rate": f"{(passed / executed * 100):.0f}%" if executed else "N/A",
        }


