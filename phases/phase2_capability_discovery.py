from utils.models import TestResult
from phases.capability_discovery import CapabilityDiscovery

class Phase2CapabilityDiscovery:
    def __init__(self, code: str):
        self.code = code
        self.discovery = CapabilityDiscovery(code)
        self.test_results = []

    def run(self):
        azure_model = self.discovery.discover_azure_capabilities()
        
        self.test_results.append(TestResult(
            test_id="CAP_AZURE_001",
            test_name="Azure Source Detection",
            phase="Phase2",
            status="PASS" if azure_model.source else "FAIL",
            finding=f"Azure source detected: {azure_model.source or 'NONE'}",
            recommendation="N/A" if azure_model.source else "Add Azure Blob/DataLake/Queue client",
            check_type="Capability",
            severity="Info",
            database_target="N/A",
            stage2_relevance="Required"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_AZURE_002",
            test_name="Azure Authentication Method",
            phase="Phase2",
            status="PASS" if azure_model.auth_method else "FAIL",
            finding=f"Auth method: {azure_model.auth_method or 'NONE'}",
            recommendation="N/A" if azure_model.auth_method else "Implement authentication (Key Vault/Managed Identity/Connection String)",
            check_type="Capability",
            severity="Critical",
            database_target="N/A",
            stage2_relevance="Critical"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_AZURE_003",
            test_name="Retry Logic",
            phase="Phase2",
            status="PASS" if azure_model.retry_logic else "FAIL",
            finding=f"Retry logic: {'Implemented' if azure_model.retry_logic else 'Missing'}",
            recommendation="N/A" if azure_model.retry_logic else "Add retry mechanism with exponential backoff",
            check_type="Capability",
            severity="High" if not azure_model.retry_logic else "Info",
            database_target="N/A",
            stage2_relevance="Important"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_AZURE_004",
            test_name="Error Handling",
            phase="Phase2",
            status="PASS" if azure_model.error_handling else "FAIL",
            finding=f"Error handling: {'Implemented' if azure_model.error_handling else 'Missing'}",
            recommendation="N/A" if azure_model.error_handling else "Add try/except blocks around Azure operations",
            check_type="Capability",
            severity="High" if not azure_model.error_handling else "Info",
            database_target="N/A",
            stage2_relevance="Critical"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_AZURE_005",
            test_name="Connection Validation",
            phase="Phase2",
            status="PASS" if azure_model.connection_validation else "FAIL",
            finding=f"Connection validation: {'Implemented' if azure_model.connection_validation else 'Missing'}",
            recommendation="N/A" if azure_model.connection_validation else "Add .exists() checks before operations",
            check_type="Capability",
            severity="Medium" if not azure_model.connection_validation else "Info",
            database_target="N/A",
            stage2_relevance="Important"
        ))

        self.test_results.append(TestResult(
            test_id="CAP_AZURE_006",
            test_name="Duplicate Detection",
            phase="Phase2",
            status="PASS" if azure_model.duplicate_detection else "FAIL",
            finding=f"Duplicate detection: {'Implemented' if azure_model.duplicate_detection else 'Missing'}",
            recommendation="N/A" if azure_model.duplicate_detection else "Add checksum/hash-based duplicate detection",
            check_type="Capability",
            severity="Medium" if not azure_model.duplicate_detection else "Info",
            database_target="N/A",
            stage2_relevance="Important"
        ))

        return self.test_results, azure_model
