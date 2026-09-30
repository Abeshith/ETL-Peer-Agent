import re
from typing import List, Dict, Any
from utils.models import TestResult


class Phase10CodePatternAnalyzer:
    """
    Peer review phase: Analyzes code for patterns, anti-patterns, error handling,
    and security issues. Works dynamically with any ETL code.
    """
    
    def __init__(self, python_code: str, metadata: dict, pattern_context: dict = None):
        self.code = python_code
        self.metadata = metadata
        self.pattern_context = pattern_context or {}
        self.test_results = []
        self.findings = []
        
    def run(self) -> List[TestResult]:
        """Execute peer review checks and return findings as test results."""
        self._check_error_handling()
        self._check_logging_and_debugging()
        self._check_security_patterns()
        self._check_resource_management()
        self._check_code_quality()
        
        return self.test_results
    
    def get_findings(self) -> List[Dict[str, str]]:
        """Return raw findings for integration into reports."""
        return self.findings
    
    # ─────────────────────────────────────────────────────────────────────────
    # ERROR HANDLING CHECKS
    # ─────────────────────────────────────────────────────────────────────────
    
    def _check_error_handling(self):
        """Analyze error handling patterns in code."""
        findings = []
        
        # Check for try-except blocks
        has_try_except = bool(re.search(r'\btry\s*:', self.code))
        
        # Check for specific error handling patterns
        has_exception_handling = bool(re.search(r'except\s+\w+Error', self.code))
        has_generic_except = bool(re.search(r'except\s*:', self.code))
        has_finally = bool(re.search(r'\bfinally\s*:', self.code))
        
        if not has_try_except:
            findings.append({
                "severity": "WARNING",
                "check": "Error Handling",
                "finding": "No try-except blocks detected - code may not handle errors gracefully"
            })
        elif has_generic_except:
            findings.append({
                "severity": "WARNING",
                "check": "Error Handling",
                "finding": "Generic 'except:' clause found - should catch specific exceptions"
            })
        
        if has_try_except and not has_finally:
            findings.append({
                "severity": "INFO",
                "check": "Error Handling",
                "finding": "Consider using 'finally' block for resource cleanup"
            })
        
        # Check for context managers (with statements)
        has_context_managers = bool(re.search(r'\bwith\s+\w+\(', self.code))
        if not has_context_managers and re.search(r'(open|connect|cursor)\s*\(', self.code):
            findings.append({
                "severity": "WARNING",
                "check": "Resource Management",
                "finding": "Manual resource handling detected - consider using 'with' statements for automatic cleanup"
            })
        
        # Check for ON_ERROR clause in Snowflake COPY operations
        if re.search(r'COPY\s+INTO', self.code, re.IGNORECASE):
            if not re.search(r'ON_ERROR\s*=', self.code, re.IGNORECASE):
                findings.append({
                    "severity": "WARNING",
                    "check": "Error Handling",
                    "finding": "COPY INTO detected without ON_ERROR clause - may fail on first bad record"
                })
            else:
                findings.append({
                    "severity": "PASS",
                    "check": "Error Handling",
                    "finding": "ON_ERROR clause present in COPY INTO statement"
                })
        
        # Check for transaction control
        has_commit = bool(re.search(r'\bcommit\b', self.code, re.IGNORECASE))
        has_rollback = bool(re.search(r'\brollback\b', self.code, re.IGNORECASE))
        
        if has_commit and not has_rollback:
            findings.append({
                "severity": "INFO",
                "check": "Transaction Safety",
                "finding": "Commit detected but no rollback - consider adding rollback for error scenarios"
            })
        
        self.findings.extend(findings)
        self._create_test_results(findings, "Error Handling")
    
    # ─────────────────────────────────────────────────────────────────────────
    # LOGGING AND DEBUGGING
    # ─────────────────────────────────────────────────────────────────────────
    
    def _check_logging_and_debugging(self):
        """Analyze logging patterns in code."""
        findings = []
        
        # Check for structured logging
        has_logger = bool(re.search(r'logger\.(info|error|warning|debug|critical)', self.code, re.IGNORECASE))
        has_print = bool(re.search(r'\bprint\s*\(', self.code))
        
        if has_print and not has_logger:
            findings.append({
                "severity": "WARNING",
                "check": "Logging",
                "finding": "Using print() statements instead of logger - structured logging recommended for production"
            })
        
        if has_logger:
            findings.append({
                "severity": "PASS",
                "check": "Logging",
                "finding": "Structured logging (logger) detected - good for production environments"
            })
        
        # Check for debug flags
        has_debug_flag = bool(re.search(r'debug\s*=\s*(True|False|args\.debug)', self.code, re.IGNORECASE))
        if not has_debug_flag and (has_print or has_logger):
            findings.append({
                "severity": "INFO",
                "check": "Debugging",
                "finding": "Consider adding debug flag for conditional verbose logging"
            })
        
        # Check for progress/status reporting in operations
        has_status_output = bool(re.search(r'(print|logger)\s*\(["\'].*(?:start|complete|fail|error|progress)', self.code, re.IGNORECASE))
        if not has_status_output and re.search(r'(for|while)\s+\w+\s+in', self.code):
            findings.append({
                "severity": "INFO",
                "check": "Observability",
                "finding": "Loops detected without progress reporting - consider adding status messages"
            })
        
        self.findings.extend(findings)
        self._create_test_results(findings, "Logging")
    
    # ─────────────────────────────────────────────────────────────────────────
    # SECURITY PATTERNS
    # ─────────────────────────────────────────────────────────────────────────
    
    def _check_security_patterns(self):
        """Analyze security-related patterns in code."""
        findings = []
        
        # Check for secrets management
        has_secrets_pattern = bool(re.search(
            r'(AyAzureKeyVault|get_secret|SecretClient|azure\.keyvault|boto3.*secret|os\.environ)',
            self.code, re.IGNORECASE
        ))
        
        if has_secrets_pattern:
            findings.append({
                "severity": "PASS",
                "check": "Security",
                "finding": "Credentials retrieved from secure storage (Key Vault/Environment) - good practice"
            })
        else:
            findings.append({
                "severity": "WARNING",
                "check": "Security",
                "finding": "No secure credential retrieval detected - ensure secrets are not hardcoded"
            })
        
        # Check for hardcoded credentials
        has_hardcoded = bool(re.search(
            r'(?:password|secret|token|api[_-]?key)\s*=\s*["\'][^"\']{8,}["\']',
            self.code, re.IGNORECASE
        ))
        if has_hardcoded:
            findings.append({
                "severity": "CRITICAL",
                "check": "Security",
                "finding": "Hardcoded credentials detected - MUST use secure credential storage"
            })
        
        # Check for SQL injection vulnerability patterns
        has_f_string_sql = bool(re.search(r'f["\'].*SELECT.*{', self.code, re.IGNORECASE))
        has_format_sql = bool(re.search(r'\.format\(["\'].*SELECT', self.code, re.IGNORECASE))
        has_concat_sql = bool(re.search(r'\+\s*["\'](?:SELECT|INSERT|UPDATE|DELETE)', self.code, re.IGNORECASE))
        
        if has_f_string_sql or has_format_sql or has_concat_sql:
            findings.append({
                "severity": "WARNING",
                "check": "SQL Injection",
                "finding": "SQL string concatenation detected - use parameterized queries when possible"
            })
        
        # Check for HTTPS/encryption
        if re.search(r'http\s*://', self.code, re.IGNORECASE):
            findings.append({
                "severity": "WARNING",
                "check": "Security",
                "finding": "HTTP (non-HTTPS) connections detected - should use HTTPS for security"
            })
        
        # Check for TLS/SSL verification
        if re.search(r'verify\s*=\s*False', self.code):
            findings.append({
                "severity": "WARNING",
                "check": "Security",
                "finding": "SSL verification disabled (verify=False) - security risk in production"
            })
        
        self.findings.extend(findings)
        self._create_test_results(findings, "Security")
    
    # ─────────────────────────────────────────────────────────────────────────
    # RESOURCE MANAGEMENT
    # ─────────────────────────────────────────────────────────────────────────
    
    def _check_resource_management(self):
        """Analyze resource management patterns (connections, file handles, memory)."""
        findings = []
        
        # Check for connection closure patterns
        has_close = bool(re.search(r'\.close\(\)|conn\.close', self.code))
        has_disconnect = bool(re.search(r'\.disconnect\(\)', self.code))
        has_context = bool(re.search(r'\bwith\s+\w+', self.code))
        
        if (has_close or has_disconnect or has_context):
            findings.append({
                "severity": "PASS",
                "check": "Resource Management",
                "finding": "Proper connection cleanup pattern detected"
            })
        else:
            findings.append({
                "severity": "WARNING",
                "check": "Resource Management",
                "finding": "No explicit connection cleanup detected - may cause resource leaks"
            })
        
        # Check for memory-intensive operations
        has_list_comprehension = bool(re.search(r'\[[^\]]*\s+for\s+\w+\s+in\s+', self.code))
        has_generator = bool(re.search(r'\([^\)]*\s+for\s+\w+\s+in\s+', self.code))
        
        if has_list_comprehension and not has_generator:
            findings.append({
                "severity": "INFO",
                "check": "Performance",
                "finding": "List comprehensions detected - consider generators for large datasets to reduce memory usage"
            })
        
        # Check for batch processing
        has_batch = bool(re.search(r'batch|chunk|window|limit', self.code, re.IGNORECASE))
        if not has_batch and re.search(r'for\s+\w+\s+in\s+', self.code):
            findings.append({
                "severity": "INFO",
                "check": "Performance",
                "finding": "Sequential processing detected - consider batching for large datasets"
            })
        
        self.findings.extend(findings)
        self._create_test_results(findings, "Resources")
    
    # ─────────────────────────────────────────────────────────────────────────
    # CODE QUALITY
    # ─────────────────────────────────────────────────────────────────────────
    
    def _check_code_quality(self):
        """Analyze general code quality patterns."""
        findings = []
        
        # Check for docstrings
        has_docstrings = bool(re.search(r'""".*?"""|\'\'\'.*?\'\'\'', self.code, re.DOTALL))
        if not has_docstrings:
            findings.append({
                "severity": "INFO",
                "check": "Documentation",
                "finding": "No docstrings detected - consider adding for functions and classes"
            })
        
        # Check for type hints
        has_type_hints = bool(re.search(r'def\s+\w+\([^)]*:\s*\w+', self.code))
        if not has_type_hints:
            findings.append({
                "severity": "INFO",
                "check": "Code Quality",
                "finding": "No type hints detected - consider adding for better IDE support and documentation"
            })
        
        # Check for magic numbers
        has_magic_numbers = bool(re.search(r'=\s*\d{4,}|:\s*\d{4,}|,\s*\d{4,}', self.code))
        if has_magic_numbers:
            findings.append({
                "severity": "INFO",
                "check": "Code Quality",
                "finding": "Magic numbers detected - consider defining as named constants"
            })
        
        # Check for dynamic imports (bad practice)
        has_dynamic_import = bool(re.search(r'__import__|importlib\.import_module', self.code))
        if has_dynamic_import:
            findings.append({
                "severity": "WARNING",
                "check": "Code Quality",
                "finding": "Dynamic imports detected - prefer static imports for clarity and maintainability"
            })
        
        # Check for comprehensive module structure
        has_main_guard = bool(re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', self.code))
        if not has_main_guard and re.search(r'def\s+main\s*\(', self.code):
            findings.append({
                "severity": "INFO",
                "check": "Code Quality",
                "finding": "main() function exists but no __main__ guard - consider adding for testability"
            })
        
        self.findings.extend(findings)
        self._create_test_results(findings, "Quality")
    
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
                test_name=f"[{category}] {check}",
                phase="Phase 10",
                status=status,
                finding=message,
                recommendation="",
                check_type=check,
                severity=severity
            )
            self.test_results.append(result)
