import re
from typing import List, Dict, Any
from utils.models import TestResult


class Phase11TestCoverageValidator:
    """
    Peer review phase: Validates test coverage against best practices.
    Checks if generated tests cover edge cases, negative scenarios, and critical paths.
    Works dynamically with any ETL code.
    """
    
    def __init__(self, python_code: str, metadata: dict, all_test_results: List[TestResult],
                 pattern_context: dict = None):
        self.code = python_code
        self.metadata = metadata
        self.all_test_results = all_test_results
        self.pattern_context = pattern_context or {}
        self.test_results = []
        self.findings = []
        
    def run(self) -> List[TestResult]:
        """Execute test coverage validation and return findings."""
        self._check_operation_coverage()
        self._check_edge_case_coverage()
        self._check_error_scenario_coverage()
        self._check_boundary_conditions()
        self._check_test_diversity()
        
        return self.test_results
    
    def get_findings(self) -> List[Dict[str, str]]:
        """Return raw findings for integration into reports."""
        return self.findings
    
    # ─────────────────────────────────────────────────────────────────────────
    # OPERATION COVERAGE
    # ─────────────────────────────────────────────────────────────────────────
    
    def _check_operation_coverage(self):
        """Verify that all detected operations have test coverage."""
        findings = []
        
        # Get operations from pattern context
        operations = self.pattern_context.get("operations", [])
        detected_operations = []
        
        # Detect operations in code
        if re.search(r'TRUNCATE\s+TABLE', self.code, re.IGNORECASE):
            detected_operations.append("TRUNCATE")
        if re.search(r'COPY\s+INTO', self.code, re.IGNORECASE):
            detected_operations.append("COPY")
        if re.search(r'INSERT\s+INTO', self.code, re.IGNORECASE):
            detected_operations.append("INSERT")
        if re.search(r'MERGE\s+INTO', self.code, re.IGNORECASE):
            detected_operations.append("MERGE")
        if re.search(r'CREATE\s+TABLE', self.code, re.IGNORECASE):
            detected_operations.append("CREATE")
        if re.search(r'LATERAL\s+FLATTEN', self.code, re.IGNORECASE):
            detected_operations.append("LATERAL")
        if re.search(r'CALL\s+\w+\s*\(', self.code, re.IGNORECASE):
            detected_operations.append("PROC")
        if re.search(r'DELETE\s+FROM', self.code, re.IGNORECASE):
            detected_operations.append("DELETE")
        if re.search(r'UPDATE\s+\w+\s+SET', self.code, re.IGNORECASE):
            detected_operations.append("UPDATE")
        
        # Check if operations have corresponding tests
        test_names = [r.test_name.lower() for r in self.all_test_results]
        
        for op in detected_operations:
            op_tests = [t for t in test_names if op.lower() in t]
            if not op_tests:
                findings.append({
                    "severity": "WARNING",
                    "check": "Operation Coverage",
                    "finding": f"No tests found for detected {op} operation"
                })
            else:
                findings.append({
                    "severity": "PASS",
                    "check": "Operation Coverage",
                    "finding": f"{op} operation has {len(op_tests)} test(s)"
                })
        
        if not detected_operations:
            findings.append({
                "severity": "INFO",
                "check": "Operation Coverage",
                "finding": "No SQL operations detected in code"
            })
        
        self.findings.extend(findings)
        self._create_test_results(findings, "Coverage")
    
    # ─────────────────────────────────────────────────────────────────────────
    # EDGE CASE COVERAGE
    # ─────────────────────────────────────────────────────────────────────────
    
    def _check_edge_case_coverage(self):
        """Check if edge cases are covered in tests."""
        findings = []
        
        # Look for edge case tests
        edge_case_keywords = ['empty', 'null', 'zero', 'duplicate', 'large', 'extreme', 'boundary', 'edge']
        has_edge_case_tests = any(
            any(keyword in r.test_name.lower() for keyword in edge_case_keywords)
            for r in self.all_test_results
        )
        
        if not has_edge_case_tests:
            findings.append({
                "severity": "WARNING",
                "check": "Edge Cases",
                "finding": "No edge case tests detected (empty data, null values, large datasets)"
            })
        else:
            edge_tests = [r for r in self.all_test_results if any(k in r.test_name.lower() for k in edge_case_keywords)]
            findings.append({
                "severity": "PASS",
                "check": "Edge Cases",
                "finding": f"{len(edge_tests)} edge case test(s) detected"
            })
        
        # Check for empty data handling
        if re.search(r'(empty|length|count|len)\s*==\s*0', self.code):
            findings.append({
                "severity": "PASS",
                "check": "Data Validation",
                "finding": "Empty data validation logic detected"
            })
        else:
            findings.append({
                "severity": "INFO",
                "check": "Data Validation",
                "finding": "Consider adding explicit empty data validation"
            })
        
        # Check for null/None handling
        if re.search(r'(None|null|is\s+None|!=\s*None)', self.code, re.IGNORECASE):
            findings.append({
                "severity": "PASS",
                "check": "Null Handling",
                "finding": "Null/None value handling detected"
            })
        
        self.findings.extend(findings)
        self._create_test_results(findings, "EdgeCase")
    
    # ─────────────────────────────────────────────────────────────────────────
    # ERROR SCENARIO COVERAGE
    # ─────────────────────────────────────────────────────────────────────────
    
    def _check_error_scenario_coverage(self):
        """Check if error scenarios are tested (negative tests)."""
        findings = []
        
        # Look for negative/error scenario tests
        negative_keywords = ['fail', 'error', 'invalid', 'exception', 'negative', 'missing', 'unauthorized', 'timeout']
        has_negative_tests = any(
            any(keyword in r.test_name.lower() for keyword in negative_keywords)
            for r in self.all_test_results
        )
        
        if not has_negative_tests:
            findings.append({
                "severity": "WARNING",
                "check": "Error Scenarios",
                "finding": "No negative/error scenario tests detected"
            })
        else:
            negative_tests = [r for r in self.all_test_results if any(k in r.test_name.lower() for k in negative_keywords)]
            findings.append({
                "severity": "PASS",
                "check": "Error Scenarios",
                "finding": f"{len(negative_tests)} error scenario test(s) detected"
            })
        
        # Check for exception types being tested
        exception_patterns = ['ConnectionError', 'TimeoutError', 'ValueError', 'KeyError', 'IndexError', 'FileNotFoundError']
        checked_exceptions = [e for e in exception_patterns if re.search(e, self.code)]
        
        if checked_exceptions:
            findings.append({
                "severity": "PASS",
                "check": "Exception Testing",
                "finding": f"Code handles {len(checked_exceptions)} exception type(s)"
            })
        else:
            findings.append({
                "severity": "INFO",
                "check": "Exception Testing",
                "finding": "Consider adding explicit exception type handling"
            })
        
        self.findings.extend(findings)
        self._create_test_results(findings, "Error")
    
    # ─────────────────────────────────────────────────────────────────────────
    # BOUNDARY CONDITIONS
    # ─────────────────────────────────────────────────────────────────────────
    
    def _check_boundary_conditions(self):
        """Check if boundary conditions are tested."""
        findings = []
        
        # Look for boundary tests
        boundary_keywords = ['boundary', 'min', 'max', 'limit', 'threshold', 'overflow']
        has_boundary_tests = any(
            any(keyword in r.test_name.lower() for keyword in boundary_keywords)
            for r in self.all_test_results
        )
        
        if not has_boundary_tests:
            findings.append({
                "severity": "INFO",
                "check": "Boundary Testing",
                "finding": "No explicit boundary condition tests detected"
            })
        else:
            findings.append({
                "severity": "PASS",
                "check": "Boundary Testing",
                "finding": "Boundary condition tests detected"
            })
        
        # Check for timeout/performance boundaries
        if re.search(r'timeout|timeout_sec|wait_time|sleep|delay', self.code, re.IGNORECASE):
            findings.append({
                "severity": "PASS",
                "check": "Timeout Handling",
                "finding": "Timeout/delay handling detected"
            })
        
        # Check for data size limits
        if re.search(r'(max_rows|limit|chunk|batch|window)', self.code, re.IGNORECASE):
            findings.append({
                "severity": "PASS",
                "check": "Data Size",
                "finding": "Data size/chunking limits detected"
            })
        
        self.findings.extend(findings)
        self._create_test_results(findings, "Boundary")
    
    # ─────────────────────────────────────────────────────────────────────────
    # TEST DIVERSITY
    # ─────────────────────────────────────────────────────────────────────────
    
    def _check_test_diversity(self):
        """Check for test type diversity (functional, integration, unit, etc.)."""
        findings = []
        
        # Categorize test types by phase
        test_categories = {}
        for result in self.all_test_results:
            phase = result.phase or "Unknown"
            test_categories[phase] = test_categories.get(phase, 0) + 1
        
        if len(test_categories) >= 3:
            findings.append({
                "severity": "PASS",
                "check": "Test Diversity",
                "finding": f"Good test diversity: {len(test_categories)} phases ({', '.join(list(test_categories.keys())[:3])}...)"
            })
        elif len(test_categories) == 1:
            findings.append({
                "severity": "WARNING",
                "check": "Test Diversity",
                "finding": "Limited test diversity - tests only cover one phase"
            })
        
        # Check pass/fail ratio
        total_tests = len(self.all_test_results)
        passed = sum(1 for r in self.all_test_results if r.status == "PASS")
        failed = sum(1 for r in self.all_test_results if r.status == "FAIL")
        
        if failed > 0 and passed > 0:
            findings.append({
                "severity": "PASS",
                "check": "Test Balance",
                "finding": f"Good test coverage: {passed} passing and {failed} failing tests"
            })
        elif failed == 0 and total_tests > 5:
            findings.append({
                "severity": "INFO",
                "check": "Test Balance",
                "finding": "All tests passing - consider adding negative/edge case tests"
            })
        
        # Check test count adequacy
        if total_tests < 10:
            findings.append({
                "severity": "INFO",
                "check": "Test Adequacy",
                "finding": f"Low test count ({total_tests} tests) - consider adding more comprehensive tests"
            })
        elif total_tests >= 50:
            findings.append({
                "severity": "PASS",
                "check": "Test Adequacy",
                "finding": f"Comprehensive test coverage ({total_tests} tests)"
            })
        
        self.findings.extend(findings)
        self._create_test_results(findings, "Diversity")
    
    # ─────────────────────────────────────────────────────────────────────────
    # HELPER METHODS
    # ─────────────────────────────────────────────────────────────────────────
    
    def _create_test_results(self, findings: List[Dict[str, str]], category: str):
        """Convert findings into TestResult objects."""
        for i, finding in enumerate(findings):
            severity = finding.get("severity", "INFO")
            check = finding.get("check", "Unknown")
            message = finding.get("finding", "")
            
            # Determine status based on severity
            if severity == "PASS":
                status = "PASS"
            elif severity in ["CRITICAL", "WARNING"]:
                status = "FAIL"
            else:
                status = "PASS"  # INFO level doesn't fail
            
            result = TestResult(
                test_id=f"PEER-{category[:3].upper()}-{i+1:02d}",
                test_name=f"[Coverage] {check}",
                phase="Phase 11",
                status=status,
                finding=message,
                recommendation="",
                check_type=check,
                severity=severity
            )
            self.test_results.append(result)
