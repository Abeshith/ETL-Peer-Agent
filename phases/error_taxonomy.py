from enum import Enum
from typing import Dict, List


class ErrorCategory(Enum):
    CONFIGURATION = "configuration"
    CREDENTIALS = "credentials"
    NETWORK = "network"
    PERMISSIONS = "permissions"
    DATA = "data"
    RESOURCE_NOT_FOUND = "resource_not_found"
    INVALID_STATE = "invalid_state"
    UNKNOWN = "unknown"


class ErrorTaxonomy:
    def __init__(self):
        self.error_categories = {
            ErrorCategory.CONFIGURATION: {
                "patterns": ["FileNotFoundError", "YAMLError", "KeyError", "ConfigError", "parse error"],
                "retryable": False,
                "criticality": "high",
                "description": "Configuration/setup issues"
            },
            ErrorCategory.CREDENTIALS: {
                "patterns": ["Unauthorized", "Forbidden", "InvalidCredential", "vault not found", 
                           "AuthenticationFailed", "access denied"],
                "retryable": False,
                "criticality": "critical",
                "description": "Authentication/authorization failures"
            },
            ErrorCategory.NETWORK: {
                "patterns": ["TimeoutError", "ConnectionRefused", "ServiceUnavailable", "timeout",
                           "ConnectionError", "Network", "unreachable"],
                "retryable": True,
                "criticality": "medium",
                "description": "Network/connectivity issues"
            },
            ErrorCategory.PERMISSIONS: {
                "patterns": ["PermissionError", "AccessDenied", "permission denied", "no permission"],
                "retryable": False,
                "criticality": "high",
                "description": "Permission/authorization issues"
            },
            ErrorCategory.DATA: {
                "patterns": ["ConstraintViolation", "DataType", "type mismatch", "DuplicateKey", 
                           "ValueError", "data error"],
                "retryable": False,
                "criticality": "high",
                "description": "Data validation/integrity issues"
            },
            ErrorCategory.RESOURCE_NOT_FOUND: {
                "patterns": ["NotFound", "not found", "does not exist", "no such file",
                           "ResourceNotFound", "does not exist"],
                "retryable": False,
                "criticality": "high",
                "description": "Missing resource (table, container, file, etc.)"
            },
            ErrorCategory.INVALID_STATE: {
                "patterns": ["InvalidState", "invalid environment", "state error"],
                "retryable": False,
                "criticality": "medium",
                "description": "Invalid operational state"
            }
        }

    def classify_error(self, error_message: str) -> tuple:
        error_lower = error_message.lower()
        for category, details in self.error_categories.items():
            for pattern in details["patterns"]:
                if pattern.lower() in error_lower:
                    return category, details["retryable"], details["criticality"]
        return ErrorCategory.UNKNOWN, False, "medium"

    def is_retryable(self, category: ErrorCategory) -> bool:
        return self.error_categories[category]["retryable"]

    def get_criticality(self, category: ErrorCategory) -> str:
        return self.error_categories[category]["criticality"]

    def get_category_description(self, category: ErrorCategory) -> str:
        return self.error_categories[category]["description"]
