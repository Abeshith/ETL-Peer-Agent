from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any


class ComplexityLevel(Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class LoadStrategy(Enum):
    TRUNCATE_LOAD = "truncate_load"
    FULL_REFRESH = "full_refresh"
    INCREMENTAL = "incremental"
    APPEND = "append"
    UPSERT = "upsert"
    MERGE = "merge"
    CDC = "cdc"
    DDL = "ddl"
    AUDIT = "audit"
    CUSTOM = "custom"


class SourceType(Enum):
    BLOB = "blob"
    S3 = "s3"
    SFTP = "sftp"
    LOCAL_FS = "local_fs"
    DATABASE = "database"
    API = "api"
    STREAM = "stream"
    UNKNOWN = "unknown"


class TargetType(Enum):
    SNOWFLAKE = "snowflake"
    BIGQUERY = "bigquery"
    REDSHIFT = "redshift"
    POSTGRES = "postgres"
    DELTA = "delta"
    UNKNOWN = "unknown"


class CredentialType(Enum):
    KEYVAULT = "keyvault"
    SECRETS_MANAGER = "secrets_manager"
    ENV_VARS = "env_vars"
    CONFIG_FILE = "config_file"
    MANAGED_IDENTITY = "managed_identity"
    DIRECT = "direct"
    UNKNOWN = "unknown"


@dataclass
class ETLProfile:
    complexity_level: ComplexityLevel = ComplexityLevel.SIMPLE
    load_strategy: LoadStrategy = LoadStrategy.APPEND
    source_type: SourceType = SourceType.UNKNOWN
    target_type: TargetType = TargetType.UNKNOWN
    credential_type: CredentialType = CredentialType.UNKNOWN
    
    has_error_handling: bool = False
    has_retry_logic: bool = False
    has_transaction_management: bool = False
    has_batch_operations: bool = False
    has_audit_logging: bool = False
    has_duplicate_detection: bool = False
    has_uuid_generation: bool = False
    has_cross_db_ops: bool = False
    
    discovered_capabilities: List[str] = field(default_factory=list)
    critical_capabilities_missing: List[str] = field(default_factory=list)
    discovered_tables: List[str] = field(default_factory=list)
    discovered_sources: List[str] = field(default_factory=list)
    cli_arguments: Dict[str, Any] = field(default_factory=dict)
    config_structure: Dict[str, Any] = field(default_factory=dict)
    # Raw LLM findings from Phase 0 — reused by Phase 2/3/5 to avoid re-calling LLM
    llm_findings: Dict[str, Any] = field(default_factory=dict)
    
    def get_test_count(self) -> int:
        if self.complexity_level == ComplexityLevel.SIMPLE:
            return 4
        elif self.complexity_level == ComplexityLevel.MEDIUM:
            return 8
        else:
            return 12 + len(self.discovered_capabilities)
    
    def get_required_capabilities(self) -> List[str]:
        base = ["error_handling", "connection_validation"]
        if self.has_retry_logic or self.source_type == SourceType.BLOB:
            base.append("retry_logic")
        if self.complexity_level in [ComplexityLevel.MEDIUM, ComplexityLevel.COMPLEX]:
            base.extend(["transaction_management", "audit_logging"])
        if self.load_strategy in [LoadStrategy.UPSERT, LoadStrategy.MERGE]:
            base.append("duplicate_detection")
        return base
    
    def get_capability_gaps(self) -> List[str]:
        required = set(self.get_required_capabilities())
        discovered = set(self.discovered_capabilities)
        return list(required - discovered)
    
    def get_risk_level(self) -> str:
        gaps = self.get_capability_gaps()
        critical_gaps = [g for g in gaps if g in self.critical_capabilities_missing]
        
        if critical_gaps:
            return "CRITICAL"
        elif len(gaps) >= 3:
            return "HIGH"
        elif len(gaps) > 0:
            return "MEDIUM"
        return "LOW"
