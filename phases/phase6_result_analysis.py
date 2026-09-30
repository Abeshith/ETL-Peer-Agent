from typing import List, Dict, Any
from utils.models import TestResult
from src.config import Config
import os
import re
from dotenv import load_dotenv
from pathlib import Path


class ResultAnalysisAgent:
    def __init__(self, python_code: str, metadata: dict, all_test_results: List[TestResult],
                 phase5c_summary: Dict[str, Any] = None, context=None, business_rules: list = None):
        self.code = python_code
        self.metadata = metadata
        self.all_test_results = all_test_results
        self.phase5c_summary = phase5c_summary or {}
        self.context = context  # ETLContext — source of truth, do not re-discover
        self.business_rules = business_rules or []
        self.test_results = []
        self.groq_response = None
        self.analysis = {}

    def run(self) -> List[TestResult]:
        self._run_static_code_analysis()

        if self._has_groq_key():
            self._analyze_results_with_llm()
            self._generate_llm_analysis_results()
        else:
            self._generate_static_analysis_results()

        return self.test_results

    def _has_groq_key(self) -> bool:
        from utils.llm_client import has_llm_key
        return has_llm_key()

    def _run_static_code_analysis(self):
        has_rollback = bool(re.search(r"rollback|ROLLBACK", self.code, re.IGNORECASE))
        has_commit   = bool(re.search(r"commit|COMMIT", self.code, re.IGNORECASE))
        self.analysis["has_transaction_safety"] = has_rollback and has_commit

        has_retry = bool(re.search(r"retry|backoff|sleep|tenacity|@retry", self.code, re.IGNORECASE))
        self.analysis["has_retry_logic"] = has_retry

        has_logger = bool(re.search(r"logger\.(info|error|warning|debug|critical)", self.code))
        has_print  = bool(re.search(r"\bprint\(", self.code))
        self.analysis["uses_structured_logging"] = has_logger
        self.analysis["uses_print"] = has_print

        # Detect any secrets/credential mechanism — not just AzureKeyVault
        has_secrets = bool(re.search(
            r"AyAzureKeyVault|azureKV|get_secret|SecretClient|azure\.keyvault"
            r"|boto3.*secret|secretsmanager"
            r"|hvac|vault\.read"
            r"|os\.environ|os\.getenv|load_dotenv",
            self.code, re.IGNORECASE
        ))
        self.analysis["has_secrets_mechanism"] = has_secrets
        # keep old key for backward compat with Phase 6 prompt
        self.analysis["has_keyvault"] = has_secrets

    def _analyze_results_with_llm(self):
        try:
            from utils.llm_client import call_llm
            print("  [Phase 6] LLM: Attempting result analysis with LLM...")
            prompt = self._build_analysis_prompt()
            response = call_llm(prompt, max_tokens=3000, temperature=0.3)
            if response and response.strip():
                self.groq_response = response
                self._parse_analysis()
        except Exception as e:
            print(f"  [Phase 6] LLM: FALLBACK - {type(e).__name__}: {str(e)[:100]}")

    def _build_analysis_prompt(self) -> str:
        failed_tests = [r for r in self.all_test_results if r.status == "FAIL"]
        passed_tests = [r for r in self.all_test_results if r.status == "PASS"]

        failures = "\n".join([f"- {r.test_id} {r.test_name}: {r.finding}" for r in failed_tests[:8]])
        successes = "\n".join([f"- {r.test_id} {r.test_name}" for r in passed_tests[:8]])

        exec_summary_str = ""
        if self.phase5c_summary:
            s = self.phase5c_summary
            exec_summary_str = (
                f"\nPhase 5 Execution Results:\n"
                f"  Executed: {s.get('executed', 0)} | "
                f"Passed: {s.get('passed', 0)} | "
                f"Failed: {s.get('failed', 0)} | "
                f"Skipped: {s.get('skipped', 0)} | "
                f"Pass Rate: {s.get('pass_rate', 'N/A')}"
            )

        static_summary = (
            f"Transaction safety: {self.analysis.get('has_transaction_safety')}\n"
            f"Retry logic: {self.analysis.get('has_retry_logic')}\n"
            f"Structured logging: {self.analysis.get('uses_structured_logging')}\n"
            f"Uses print(): {self.analysis.get('uses_print')}\n"
            f"Key Vault integration: {self.analysis.get('has_keyvault')}"
        )

        context_block = self.context.to_prompt_block() if self.context else "(no pre-built context available)"

        rules_block = ("\nBusiness Logic Rules to validate:\n" +
                       "\n".join(f"- {r}" for r in self.business_rules) + "\n") if self.business_rules else ""

        return f"""Use the supplied capability and metadata objects as the source of truth. Do not rediscover information already present. Only add new information relevant to this phase.

{context_block}
{rules_block}
You are a senior data engineer reviewing a Python ETL script for production readiness.
Do NOT assume any specific framework or library.
The ETL may use any credential mechanism (Azure Key Vault, AWS Secrets Manager, env vars, etc.)
and any data source (Azure Blob, S3, SFTP, local files, API, etc.).

ETL Code:
```python
{self.code}
```

Static Analysis:
{static_summary}

Passed: {len(passed_tests)} tests
{successes}

Failed: {len(failed_tests)} tests
{failures}
{exec_summary_str}

Analyze this ETL across ALL of these dimensions. For every issue found, include:
- Why it matters in production
- Production impact if it fails
- Severity (Critical/High/Medium/Low)
- Suggested fix
- Expected benefit after fix

Dimensions to analyze:
1. ARCHITECTURE — load strategy correctness, idempotency, re-runnability
2. SECURITY — credential exposure, secret rotation, least-privilege access
3. RELIABILITY — retry logic, transient failure handling, timeout handling
4. RECOVERABILITY — rollback on failure, partial load detection, re-run safety
5. PERFORMANCE — batch size, SELECT * usage, excessive DB calls, parallelism
6. MAINTAINABILITY — hardcoded values, logging quality, code structure
7. OPERATIONAL RISKS — missing monitoring, no alerting, silent failures
8. DATA QUALITY — null handling, type validation, duplicate prevention
9. BUSINESS RISKS — data loss scenarios, incorrect load order, audit gaps

Do NOT repeat passed validations. Focus only on gaps and risks.

Provide analysis in this format:
RISK_LEVEL: HIGH/MEDIUM/LOW
REASON: <one-line explanation>

CRITICAL_ISSUES: <comma-separated list>

PERFORMANCE_CONCERNS: <comma-separated list>

SECURITY_GAPS: <comma-separated list>

RECOMMENDATIONS:
1. <dimension: specific fix | production impact | expected benefit>
2. <dimension: specific fix | production impact | expected benefit>
3. <dimension: specific fix | production impact | expected benefit>"""

    def _parse_analysis(self):
        if not self.groq_response:
            return

        lines = self.groq_response.split('\n')
        rec_lines = []
        in_recommendations = False

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                in_recommendations = False
                continue

            if line_stripped.startswith('RISK_LEVEL:'):
                self.analysis['risk_level'] = line_stripped.replace('RISK_LEVEL:', '').strip()
            elif line_stripped.startswith('REASON:'):
                self.analysis['reason'] = line_stripped.replace('REASON:', '').strip()
            elif line_stripped.startswith('CRITICAL_ISSUES:'):
                self.analysis['critical_issues'] = line_stripped.replace('CRITICAL_ISSUES:', '').strip()
            elif line_stripped.startswith('PERFORMANCE_CONCERNS:'):
                self.analysis['performance'] = line_stripped.replace('PERFORMANCE_CONCERNS:', '').strip()
            elif line_stripped.startswith('SECURITY_GAPS:'):
                self.analysis['security'] = line_stripped.replace('SECURITY_GAPS:', '').strip()
            elif line_stripped.startswith('RECOMMENDATIONS:'):
                in_recommendations = True
            elif in_recommendations and re.match(r'^\d+\.', line_stripped):
                rec_lines.append(line_stripped)

        if rec_lines:
            self.analysis['recommendations'] = rec_lines

    def _generate_llm_analysis_results(self):
        if not self.analysis.get('risk_level'):
            return

        target_db = self.metadata.get('target_database') or "N/A"
        recs = self.analysis.get('recommendations', [])
        if recs:
            self.test_results.append(TestResult(
                test_id="ANALYSIS01",
                test_name="Action Items",
                phase="AI Analysis",
                status="FAIL",
                finding=" | ".join(recs[:5]),
                recommendation="Address the above action items before production deployment",
                check_type="AI Analysis",
                severity="High",
                database_target=target_db,
                stage2_relevance="Critical",
            ))

    def _generate_static_analysis_results(self):
        pass  # No overall risk assessment row

    def _get_execution_counts(self) -> Dict[str, Any]:
        if self.phase5c_summary:
            return self.phase5c_summary

        total = len(self.all_test_results)
        passed = sum(1 for r in self.all_test_results if r.status == "PASS")
        failed = sum(1 for r in self.all_test_results if r.status == "FAIL")
        skipped = sum(1 for r in self.all_test_results if r.status == "SKIPPED")
        generated = sum(1 for r in self.all_test_results if r.status == "GENERATED")
        executed = total - generated

        return {
            "executed": executed,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pass_rate": f"{(passed / executed * 100):.0f}%" if executed else "N/A",
        }

    def _build_rich_summary(self, risk: str, counts: Dict[str, Any]) -> str:
        executed = counts.get("executed", 0)
        passed = counts.get("passed", 0)
        failed = counts.get("failed", 0)
        skipped = counts.get("skipped", 0)
        pass_rate = counts.get("pass_rate", "N/A")

        summary = (
            f"Executed Tests: {executed} | "
            f"Passed: {passed} | "
            f"Failed: {failed} | "
            f"Skipped: {skipped} | "
            f"Pass Rate: {pass_rate} | "
            f"Risk: {risk}"
        )

        if self.analysis.get('reason'):
            summary += f" - {self.analysis['reason']}"

        return summary
