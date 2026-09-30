from typing import List, Dict, Any
from utils.models import TestResult
from phases.etl_profile import ETLProfile
from phases.error_taxonomy import ErrorTaxonomy
from phases.capability_registry import CapabilityRegistry


class Phase9FailurePatternCorrelator:
    def __init__(self, test_results: List[TestResult], profile: ETLProfile):
        self.test_results = test_results
        self.profile = profile
        self.error_taxonomy = ErrorTaxonomy()
        self.registry = CapabilityRegistry()
        self.failure_analysis = {}

    def run(self) -> Dict[str, Any]:
        self._analyze_failures()
        self._correlate_to_capabilities()
        self._identify_systemic_issues()
        if self._has_llm():
            print("  [Phase 9] LLM: Attempting to generate recommendations with LLM...")
            self._generate_recommendations_via_llm()
        else:
            self._generate_recommendations()
        return self.failure_analysis

    def _has_llm(self) -> bool:
        from utils.llm_client import has_llm_key
        return has_llm_key()

    def _generate_recommendations_via_llm(self):
        try:
            from utils.llm_client import call_llm
            failures_text = "\n".join([
                f"- {r.test_id} ({r.phase} | {r.test_name}): {r.finding[:120]}"
                for r in self.test_results if r.status == "FAIL"
            ])
            if not failures_text:
                self._generate_recommendations()
                return
            prompt = f"""You are a senior data engineer performing root cause analysis on ETL test failures.

ETL Profile:
- Complexity: {self.profile.complexity_level}
- Load Strategy: {self.profile.load_strategy}
- Source: {self.profile.source_type}
- Target: {self.profile.target_type}
- Discovered capabilities: {self.profile.discovered_capabilities}
- Capability gaps: {self.profile.get_capability_gaps()}

Failed tests:
{failures_text}

For each failure cluster, identify:
1. Root Cause — the actual underlying reason
2. Affected Components — what parts of the ETL are impacted
3. Downstream Impact — what breaks downstream if this is not fixed
4. Likely Reason — most probable cause given the ETL profile
5. Suggested Fix — specific actionable remediation
6. Confidence — HIGH/MEDIUM/LOW

Cluster related failures together. Avoid duplicate recommendations.
Do NOT repeat passing tests.

Return as JSON array:
[{{"cluster": "name", "root_cause": "...", "affected_components": [...], "downstream_impact": "...", "likely_reason": "...", "fix": "...", "confidence": "HIGH|MEDIUM|LOW"}}]"""
            response = call_llm(prompt, max_tokens=800)
            import json
            text = response.strip()
            start = text.find('[')
            raw = text[start:] if start != -1 else text
            recommendations, _ = json.JSONDecoder().raw_decode(raw)
            self.failure_analysis["llm_recommendations"] = recommendations
            self.failure_analysis["recommendations"] = [r["fix"] for r in recommendations[:5]]
            # Enrich failure_analysis with root cause clusters
            self.failure_analysis["root_cause_clusters"] = [
                {
                    "cluster": r.get("cluster"),
                    "root_cause": r.get("root_cause"),
                    "downstream_impact": r.get("downstream_impact"),
                    "confidence": r.get("confidence"),
                }
                for r in recommendations
            ]
            print("  [Phase 9] LLM: SUCCESS - Generated root cause analysis")
        except Exception as e:
            print(f"  [Phase 9] LLM: FALLBACK - {type(e).__name__}: {str(e)[:100]}")
            self._generate_recommendations()

    def _analyze_failures(self):
        failures = [r for r in self.test_results if r.status == "FAIL"]
        self.failure_analysis["total_failures"] = len(failures)
        self.failure_analysis["failure_rate"] = (
            len(failures) / len(self.test_results) if self.test_results else 0
        )
        
        error_distribution = {}
        for failure in failures:
            finding = failure.finding or ""
            category, retryable, criticality = self.error_taxonomy.classify_error(finding)
            
            key = category.value
            if key not in error_distribution:
                error_distribution[key] = []
            error_distribution[key].append({
                "test": failure.test_id,
                "retryable": retryable,
                "criticality": criticality,
                "finding": finding[:100]
            })
        
        self.failure_analysis["error_distribution"] = error_distribution

    def _correlate_to_capabilities(self):
        failures = [r for r in self.test_results if r.status == "FAIL"]
        capability_failures = {}
        
        for capability in self.profile.discovered_capabilities:
            related_failures = [
                f for f in failures 
                if self._is_capability_related(f, capability)
            ]
            if related_failures:
                capability_failures[capability] = len(related_failures)
        
        for gap in self.profile.get_capability_gaps():
            if gap not in capability_failures:
                capability_failures[gap] = 0
        
        self.failure_analysis["capability_failures"] = capability_failures

    def _is_capability_related(self, failure: TestResult, capability: str) -> bool:
        test_name = (failure.test_name or "").lower()
        finding = (failure.finding or "").lower()
        
        capability_keywords = {
            "error_handling": ["error", "exception", "handler", "fail"],
            "retry_logic": ["retry", "timeout", "backoff", "attempt"],
            "transaction_management": ["transaction", "commit", "rollback"],
            "audit_logging": ["audit", "log"],
            "duplicate_detection": ["duplicate", "dedup"],
            "batch_operations": ["batch", "bulk"],
        }
        
        keywords = capability_keywords.get(capability, [])
        return any(kw in test_name or kw in finding for kw in keywords)

    def _identify_systemic_issues(self):
        error_dist = self.failure_analysis.get("error_distribution", {})
        cap_failures = self.failure_analysis.get("capability_failures", {})
        
        systemic_issues = []
        
        if error_dist.get("credentials", []):
            systemic_issues.append({
                "type": "credentials",
                "severity": "critical",
                "count": len(error_dist["credentials"]),
                "description": "Multiple failures due to credential/authentication issues"
            })
        
        if error_dist.get("network", []):
            has_retry = "retry_logic" in self.profile.discovered_capabilities
            systemic_issues.append({
                "type": "network",
                "severity": "high" if not has_retry else "medium",
                "count": len(error_dist["network"]),
                "description": "Network connectivity issues detected"
            })
        
        cap_gaps = self.profile.get_capability_gaps()
        for gap in cap_gaps:
            if cap_failures.get(gap, 0) > 0:
                severity = "critical" if gap in self.registry.get_critical_capabilities() else "high"
                systemic_issues.append({
                    "type": f"missing_{gap}",
                    "severity": severity,
                    "count": cap_failures[gap],
                    "description": f"Failures related to missing {gap}"
                })
        
        self.failure_analysis["systemic_issues"] = systemic_issues

    def _generate_recommendations(self):
        systemic = self.failure_analysis.get("systemic_issues", [])
        recommendations = []
        
        for issue in systemic:
            if issue["type"] == "credentials":
                recommendations.append(
                    "Verify credentials and secrets are properly configured in the credential store"
                )
            elif issue["type"] == "network":
                recommendations.append(
                    "Implement retry mechanism with exponential backoff for transient network failures"
                )
            elif "missing_" in issue["type"]:
                capability = issue["type"].replace("missing_", "")
                recommendations.append(
                    f"Implement {capability.replace('_', ' ')} in the ETL logic"
                )
        
        self.failure_analysis["recommendations"] = list(set(recommendations))
        
        critical_issues = [i for i in systemic if i["severity"] == "critical"]
        self.failure_analysis["risk_level"] = (
            "CRITICAL" if critical_issues else
            "HIGH" if len(systemic) >= 2 else
            "MEDIUM" if len(systemic) > 0 else
            "LOW"
        )
