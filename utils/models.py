from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime


@dataclass
class ETLContext:
    """
    Compact structured object built after Phase 1-3 and passed to all downstream phases.
    Each phase reads from this — never re-discovers information already present.
    """
    # ── Identity ──────────────────────────────────────────────────────────────
    etl_file: str = None
    env_key: str = None          # e.g. "dev", "dev_v3", "prd"

    # ── Metadata (from Phase 1) ───────────────────────────────────────────────
    target_database: str = None
    target_schema: str = None
    target_table: str = None
    audit_table: str = None
    credential_mechanism: str = None   # e.g. "AzureKeyVault", "EnvVars"
    source_type: str = None            # e.g. "AzureBlob", "SnowflakeStage"
    source_identifier: str = None      # container name, bucket, stage, etc.
    has_env_arg: bool = False
    has_run_date_arg: bool = False
    valid_envs: List[str] = field(default_factory=list)
    kv_name: str = None
    table_names: List[str] = field(default_factory=list)
    blob_paths: List[str] = field(default_factory=list)

    # ── Azure capabilities (from Phase 2) ─────────────────────────────────────
    azure_source: str = None
    azure_auth_method: str = None
    azure_retry_logic: bool = False
    azure_error_handling: bool = False
    azure_connection_validation: bool = False
    azure_duplicate_detection: bool = False

    # ── Snowflake capabilities (from Phase 3) ─────────────────────────────────
    sf_load_strategy: str = None
    sf_connection_type: str = None
    sf_transaction_management: bool = False
    sf_rollback_on_failure: bool = False
    sf_batch_operations: bool = False
    sf_error_handling: bool = False
    sf_audit_logging: bool = False
    sf_structured_logging: bool = False

    def to_prompt_block(self) -> str:
        """Compact string representation for injecting into LLM prompts."""
        lines = [
            "=== ETL Context (source of truth — do not rediscover) ===",
            f"ETL file       : {self.etl_file}",
            f"Env key        : {self.env_key}",
            f"Target DB      : {self.target_database}.{self.target_schema}",
            f"Target table   : {self.target_table}",
            f"Audit table    : {self.audit_table}",
            f"Credential     : {self.credential_mechanism}",
            f"Source type    : {self.source_type}",
            f"Source id      : {self.source_identifier}",
            f"KV name        : {self.kv_name}",
            f"Tables         : {self.table_names}",
            f"Blob paths     : {self.blob_paths}",
            f"CLI --env      : present={self.has_env_arg}, valid={self.valid_envs}",
            f"CLI --run_date : present={self.has_run_date_arg}",
            "--- Azure capabilities ---",
            f"  source={self.azure_source}, auth={self.azure_auth_method}",
            f"  retry={self.azure_retry_logic}, error_handling={self.azure_error_handling}",
            f"  conn_validation={self.azure_connection_validation}, dedup={self.azure_duplicate_detection}",
            "--- Snowflake capabilities ---",
            f"  load_strategy={self.sf_load_strategy}, conn_type={self.sf_connection_type}",
            f"  transactions={self.sf_transaction_management}, rollback={self.sf_rollback_on_failure}",
            f"  batch_ops={self.sf_batch_operations}, error_handling={self.sf_error_handling}",
            f"  audit_logging={self.sf_audit_logging}, structured_logging={self.sf_structured_logging}",
            "========================================================",
        ]
        return "\n".join(lines)


@dataclass
class Metadata:
    azure_connection: Optional[str] = None
    azure_container: Optional[str] = None
    snowflake_connection: Optional[str] = None
    target_database: Optional[str] = None
    target_schema: Optional[str] = None
    target_table: Optional[str] = None
    audit_table: Optional[str] = None
    source_file_pattern: Optional[str] = None
    functions: List[Dict] = field(default_factory=list)
    classes: List[Dict] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    sql_statements: List[str] = field(default_factory=list)
    extracted_at: datetime = field(default_factory=datetime.now)
    # Phase 1 classification: Static / Dynamic / Derived per field
    field_classifications: Dict[str, str] = field(default_factory=dict)


@dataclass
class TestResult:
    test_id: str
    test_name: str
    phase: str
    status: str
    finding: str
    recommendation: str
    check_type: str
    severity: str
    database_target: Optional[str] = None
    stage2_relevance: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ExecutionResult:
    """
    Produced by Phase 5B for each test case that was actually executed.
    Carries raw output and metadata used to build the final TestResult.
    """
    test_id: str
    test_name: str
    status: str           # PASS / FAIL / SKIPPED
    stdout: str
    stderr: str
    duration_ms: float
    action_performed: str
    restored: bool        # True if any side-effects (renamed files, temp configs) were cleaned up


@dataclass
class BatchRunRow:
    """
    One row of the Batch Run Validation sheet in the output report.
    Mirrors the ETL batch lifecycle: Trigger → Parse → Connect → Load → Audit.
    """
    scenario: str
    expected_result: str
    actual_result: str
    status: str           # PASS / FAIL / SKIPPED
    source_phase: str     # Which phase generated this row (Phase3, Phase5B, etc.)


@dataclass
class CodeAnalysis:
    metadata: Metadata
    test_results: List[TestResult] = field(default_factory=list)
    azure_patterns_found: Dict[str, List[str]] = field(default_factory=dict)
    snowflake_patterns_found: Dict[str, List[str]] = field(default_factory=dict)
    groq_findings: Dict[str, Any] = field(default_factory=dict)
    file_path: Optional[str] = None
    analysis_timestamp: datetime = field(default_factory=datetime.now)
    # Phase 5C summary dict — populated by agent orchestrator after Phase 5C
    phase5c_summary: Dict[str, Any] = field(default_factory=dict)
    # Batch run rows — populated by report generator
    batch_run_rows: List[BatchRunRow] = field(default_factory=list)

    def add_test_result(self, result: TestResult):
        self.test_results.append(result)

    def get_pass_count(self):
        return sum(1 for r in self.test_results if r.status == "PASS")

    def get_fail_count(self):
        return sum(1 for r in self.test_results if r.status == "FAIL")

    def get_generated_count(self):
        return sum(1 for r in self.test_results if r.status == "GENERATED")

    def get_skipped_count(self):
        return sum(1 for r in self.test_results if r.status == "SKIPPED")

    def get_executed_count(self):
        """Count tests that were actually executed (PASS or FAIL, not GENERATED/SKIPPED)."""
        return sum(1 for r in self.test_results if r.status in ("PASS", "FAIL"))

    def get_results_by_phase(self, phase: str):
        return [r for r in self.test_results if r.phase == phase]


@dataclass
class ResourceValidationConfig:
    azure_connection_string: Optional[str] = None
    snowflake_config: Optional[Dict[str, str]] = None
    validate_resources: bool = False
    environment: str = "development"
