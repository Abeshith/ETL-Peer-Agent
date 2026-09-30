from enum import Enum
from typing import Dict, List, Any


class CapabilitySeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CapabilityRegistry:
    def __init__(self):
        self.capabilities = {
            "retry_logic": {
                "domains": ["source_ops", "target_ops"],
                "severity": CapabilitySeverity.HIGH,
                "keywords": ["max_retries", "backoff", "exponential", "attempt", "sleep"],
                "ast_patterns": ["for_loop", "exception_handler"],
                "description": "Retry mechanism with exponential backoff"
            },
            "error_handling": {
                "domains": ["source_ops", "target_ops", "general"],
                "severity": CapabilitySeverity.CRITICAL,
                "keywords": ["try", "except", "exception", "error", "logging.error"],
                "ast_patterns": ["try_except", "exception_catch"],
                "description": "Comprehensive exception handling"
            },
            "transaction_management": {
                "domains": ["target_ops"],
                "severity": CapabilitySeverity.CRITICAL,
                "keywords": ["BEGIN", "COMMIT", "ROLLBACK", "autocommit"],
                "ast_patterns": ["context_manager", "transaction_block"],
                "description": "Transaction boundary management"
            },
            "batch_operations": {
                "domains": ["target_ops"],
                "severity": CapabilitySeverity.MEDIUM,
                "keywords": ["executemany", "batch", "bulk_insert"],
                "ast_patterns": ["list_iteration", "batch_loop"],
                "description": "Batch insert/update operations"
            },
            "audit_logging": {
                "domains": ["target_ops"],
                "severity": CapabilitySeverity.MEDIUM,
                "keywords": ["audit", "audit_table", "log"],
                "ast_patterns": ["insert_into_audit"],
                "description": "Audit trail logging"
            },
            "duplicate_detection": {
                "domains": ["source_ops"],
                "severity": CapabilitySeverity.MEDIUM,
                "keywords": ["duplicate", "processed", "cache", "checksum"],
                "ast_patterns": ["dedup_logic", "exists_check"],
                "description": "Duplicate prevention mechanism"
            },
            "connection_validation": {
                "domains": ["source_ops", "target_ops"],
                "severity": CapabilitySeverity.HIGH,
                "keywords": ["exists", "validate", "check_connection"],
                "ast_patterns": ["existence_check"],
                "description": "Pre-operation connection validation"
            },
            "data_transformation": {
                "domains": ["general"],
                "severity": CapabilitySeverity.MEDIUM,
                "keywords": ["transform", "map", "convert", "parse"],
                "ast_patterns": ["custom_function"],
                "description": "Data transformation logic"
            },
            "cross_database_ops": {
                "domains": ["target_ops"],
                "severity": CapabilitySeverity.HIGH,
                "keywords": ["fully_qualified", "cross_db", "schema"],
                "ast_patterns": ["cross_db_select"],
                "description": "Operations across multiple databases"
            },
            "uuid_generation": {
                "domains": ["target_ops"],
                "severity": CapabilitySeverity.MEDIUM,
                "keywords": ["UUID", "uuid_string", "generate_id"],
                "ast_patterns": ["uuid_function"],
                "description": "UUID/ID generation during load"
            },
            "structured_logging": {
                "domains": ["general"],
                "severity": CapabilitySeverity.LOW,
                "keywords": ["json", "structured", "JSONFormatter"],
                "ast_patterns": ["json_logging"],
                "description": "Structured/JSON logging"
            }
        }

    def get_capability(self, name: str) -> Dict[str, Any]:
        return self.capabilities.get(name, {})

    def get_all_capabilities(self) -> Dict[str, Dict]:
        return self.capabilities

    def get_capabilities_by_domain(self, domain: str) -> Dict[str, Dict]:
        return {
            k: v for k, v in self.capabilities.items()
            if domain in v.get("domains", [])
        }

    def get_critical_capabilities(self) -> List[str]:
        return [
            k for k, v in self.capabilities.items()
            if v.get("severity") == CapabilitySeverity.CRITICAL
        ]

    def register_custom_capability(self, name: str, config: Dict[str, Any]):
        self.capabilities[name] = config

    def get_severity(self, name: str) -> CapabilitySeverity:
        return self.capabilities.get(name, {}).get("severity", CapabilitySeverity.LOW)
