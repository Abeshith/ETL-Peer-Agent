from utils.models import TestResult
from phases.capability_discovery import CapabilityDiscovery

class Phase3CapabilityDiscovery:
    def __init__(self, code: str):
        self.code = code
        self.discovery = CapabilityDiscovery(code)
        self.test_results = []

    def run(self):
        snowflake_model = self.discovery.discover_snowflake_capabilities()
        
        self.test_results.append(TestResult(
            test_id="CAP_SF_001",
            test_name="Load Strategy Detection",
            phase="Phase3",
            status="PASS" if snowflake_model.load_strategy else "FAIL",
            finding=f"Load strategy: {snowflake_model.load_strategy or 'NONE'}",
            recommendation="N/A" if snowflake_model.load_strategy else "Implement COPY INTO, MERGE, or INSERT strategy",
            check_type="Capability",
            severity="Critical",
            database_target="LANDING",
            stage2_relevance="Critical"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_SF_002",
            test_name="Connection Type",
            phase="Phase3",
            status="PASS" if snowflake_model.connection_type else "FAIL",
            finding=f"Connection type: {snowflake_model.connection_type or 'NONE'}",
            recommendation="N/A" if snowflake_model.connection_type else "Use password, private_key, or managed_identity",
            check_type="Capability",
            severity="Critical",
            database_target="LANDING",
            stage2_relevance="Critical"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_SF_003",
            test_name="Transaction Management",
            phase="Phase3",
            status="PASS" if snowflake_model.transaction_management else "FAIL",
            finding=f"Transaction management: {'Implemented' if snowflake_model.transaction_management else 'Missing'}",
            recommendation="N/A" if snowflake_model.transaction_management else "Add BEGIN TRANSACTION and autocommit=False",
            check_type="Capability",
            severity="Critical" if not snowflake_model.transaction_management else "Info",
            database_target="LANDING",
            stage2_relevance="Critical"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_SF_004",
            test_name="Rollback on Failure",
            phase="Phase3",
            status="PASS" if snowflake_model.rollback_on_failure else "FAIL",
            finding=f"Rollback logic: {'Implemented' if snowflake_model.rollback_on_failure else 'Missing'}",
            recommendation="N/A" if snowflake_model.rollback_on_failure else "Add ROLLBACK in exception handlers",
            check_type="Capability",
            severity="Critical" if not snowflake_model.rollback_on_failure else "Info",
            database_target="LANDING",
            stage2_relevance="Critical"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_SF_005",
            test_name="Error Handling",
            phase="Phase3",
            status="PASS" if snowflake_model.error_handling else "FAIL",
            finding=f"Error handling: {'Implemented' if snowflake_model.error_handling else 'Missing'}",
            recommendation="N/A" if snowflake_model.error_handling else "Add try/except around Snowflake operations",
            check_type="Capability",
            severity="Critical" if not snowflake_model.error_handling else "Info",
            database_target="LANDING",
            stage2_relevance="Critical"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_SF_006",
            test_name="Batch Operations",
            phase="Phase3",
            status="PASS" if snowflake_model.batch_operations else "FAIL",
            finding=f"Batch operations: {'Implemented' if snowflake_model.batch_operations else 'Missing'}",
            recommendation="N/A" if snowflake_model.batch_operations else "Use executemany or batch INSERT for performance",
            check_type="Capability",
            severity="Medium" if not snowflake_model.batch_operations else "Info",
            database_target="LANDING",
            stage2_relevance="Important"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_SF_007",
            test_name="Audit Logging",
            phase="Phase3",
            status="PASS" if snowflake_model.audit_logging else "FAIL",
            finding=f"Audit logging: {'Implemented' if snowflake_model.audit_logging else 'Missing'}",
            recommendation="N/A" if snowflake_model.audit_logging else "Add audit_log table to track all loads",
            check_type="Capability",
            severity="Medium" if not snowflake_model.audit_logging else "Info",
            database_target="LANDING",
            stage2_relevance="Important"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_SF_008",
            test_name="Structured Logging",
            phase="Phase3",
            status="PASS" if snowflake_model.structured_logging else "FAIL",
            finding=f"Structured logging: {'Implemented' if snowflake_model.structured_logging else 'Missing'}",
            recommendation="N/A" if snowflake_model.structured_logging else "Use JSON formatter for structured logs",
            check_type="Capability",
            severity="Low" if not snowflake_model.structured_logging else "Info",
            database_target="LANDING",
            stage2_relevance="Important"
        ))

        return self.test_results, snowflake_model
