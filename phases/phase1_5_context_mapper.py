import ast
import re
from typing import Dict, List, Tuple
from phases.etl_profile import ETLProfile


class Phase1_5PatternContextMapper:
    def __init__(self, code: str, profile: ETLProfile):
        self.code = code
        self.profile = profile
        self.tree = ast.parse(code)
        self.pattern_context = {}
        self.operation_graph = {}

    def run(self) -> Tuple[Dict, Dict]:
        self._build_operation_dependency_graph()
        self._map_patterns_to_context()
        self._build_execution_context_graph()
        # Make operations available in pattern_context for phase5_5
        self.pattern_context["operations"] = self.operation_graph.get("operations", [])
        return self.pattern_context, self.operation_graph

    def _build_execution_context_graph(self):
        """
        Build a linear execution context graph:
        Source → Authentication → Container → Blob Pattern → Transformation → Target Table → Target Database → Audit Table
        """
        graph = {}

        # Source
        source = "unknown"
        if re.search(r"BlobServiceClient|azure\.storage\.blob", self.code, re.I):
            source = "AzureBlob"
        elif re.search(r"DataLakeServiceClient|abfss://", self.code, re.I):
            source = "ADLS"
        elif re.search(r"boto3.*s3|s3://", self.code, re.I):
            source = "S3"
        elif re.search(r"paramiko|SFTPClient", self.code, re.I):
            source = "SFTP"
        elif re.search(r"requests\.(get|post)", self.code, re.I):
            source = "API"
        elif re.search(r"os\.listdir|open.*\.csv", self.code, re.I):
            source = "LocalFiles"
        elif re.search(r"COPY\s+INTO|snowflake_stage", self.code, re.I):
            source = "SnowflakeStage"
        graph["source"] = source

        # Authentication
        auth = "unknown"
        if re.search(r"AyAzureKeyVault|SecretClient|azure\.keyvault", self.code, re.I):
            auth = "AzureKeyVault"
        elif re.search(r"DefaultAzureCredential|managed.*identity", self.code, re.I):
            auth = "ManagedIdentity"
        elif re.search(r"ClientSecretCredential", self.code, re.I):
            auth = "ServicePrincipal"
        elif re.search(r"secretsmanager|get_secret_value", self.code, re.I):
            auth = "AWSSecretsManager"
        elif re.search(r"os\.environ|os\.getenv|load_dotenv", self.code, re.I):
            auth = "EnvVars"
        elif re.search(r"yaml\.safe_load", self.code, re.I):
            auth = "ConfigFile"
        graph["authentication"] = auth

        # Container / bucket / stage
        container = None
        m = re.search(r"container_name\s*=\s*['\"]([^'\"]+)['\"]", self.code)
        if m:
            container = m.group(1)
        elif re.search(r"get_container_client\(['\"]([^'\"]+)['\"]", self.code):
            m2 = re.search(r"get_container_client\(['\"]([^'\"]+)['\"]", self.code)
            container = m2.group(1) if m2 else None
        graph["container"] = container or "(from config)"

        # Blob / file pattern
        blob_pattern = None
        m = re.search(r"blob_path\s*=\s*['\"]([^'\"]+)['\"]", self.code)
        if m:
            blob_pattern = m.group(1)
        elif re.search(r"\{DATE\}|format.*DATE|run_date", self.code):
            blob_pattern = "(dynamic — DATE substitution)"
        graph["blob_pattern"] = blob_pattern or "(from config)"

        # Transformation
        transforms = []
        if re.search(r"pd\.read_csv|pd\.DataFrame|pandas", self.code, re.I):
            transforms.append("pandas_transform")
        if re.search(r"json\.loads|json\.dumps", self.code, re.I):
            transforms.append("json_parse")
        if re.search(r"UUID|uuid_string|generate_id", self.code, re.I):
            transforms.append("uuid_generation")
        if re.search(r"datetime\.strptime|strftime", self.code, re.I):
            transforms.append("date_transform")
        if re.search(r"\.strip\(\)|\.upper\(\)|\.lower\(\)|\.replace\(", self.code):
            transforms.append("string_transform")
        graph["transformation"] = transforms if transforms else ["none"]

        # Target table
        target_table = None
        m = re.search(r"INSERT\s+INTO\s+(?:\w+\.\w+\.)?([A-Za-z0-9_]+)", self.code, re.I)
        if m:
            target_table = m.group(1)
        elif re.search(r"COPY\s+INTO\s+(?:\w+\.\w+\.)?([A-Za-z0-9_]+)", self.code, re.I):
            m2 = re.search(r"COPY\s+INTO\s+(?:\w+\.\w+\.)?([A-Za-z0-9_]+)", self.code, re.I)
            target_table = m2.group(1) if m2 else None
        graph["target_table"] = target_table or "(from config)"

        # Target database
        target_db = None
        m = re.search(r"database\s*=\s*['\"]([^'\"]+)['\"]", self.code, re.I)
        if m:
            target_db = m.group(1)
        graph["target_database"] = target_db or "(from config)"

        # Audit table
        audit = None
        m = re.search(r"INSERT\s+INTO\s+(?:\w+\.\w+\.)?([A-Za-z0-9_]*audit[A-Za-z0-9_]*)", self.code, re.I)
        if m:
            audit = m.group(1)
        elif re.search(r"audit_log|audit_table", self.code, re.I):
            audit = "(audit table referenced)"
        graph["audit_table"] = audit or "none"

        self.operation_graph["execution_context_graph"] = graph
        self.pattern_context["execution_context_graph"] = graph

        print(f"  [Phase 1.5] Context graph: {source} -> {auth} -> {graph['container']} -> {graph['blob_pattern']} -> {graph['target_table']} -> {graph['target_database']} -> {graph['audit_table']}")

    def _build_operation_dependency_graph(self) -> Dict:
        """Detect operations by scanning raw source code for SQL keywords."""
        import re
        operations = []
        code_upper = self.code.upper()
        
        # First: Scan for method calls on loader objects (truncate, load_csv, run_query)
        if re.search(r'\.truncate\s*\(', self.code, re.IGNORECASE):
            operations.append({"name": "SQL:TRUNCATE TABLE", "line": 0, "type": "sql_operation", "sql_type": "truncate"})
        
        if re.search(r'\.load_csv\s*\(', self.code, re.IGNORECASE):
            operations.append({"name": "SQL:COPY INTO", "line": 0, "type": "sql_operation", "sql_type": "copy_into"})
        
        if re.search(r'\.run_query\s*\(', self.code, re.IGNORECASE):
            # Generic query - scan the arguments for more specific operations
            pass
        
        # Second: Scan for SQL keywords in string literals
        sql_patterns = [
            ("TRUNCATE TABLE", "truncate"),
            ("COPY INTO", "copy_into"),
            ("WHERE NOT EXISTS", "dedup"),
            ("DISTINCT", "dedup"),
            ("INSERT INTO", "insert"),
            ("MERGE INTO", "merge"),
            ("CREATE TABLE", "create"),
            ("LATERAL FLATTEN", "lateral"),
            ("DELETE FROM", "delete"),
            ("CALL", "procedure"),
        ]
        
        for keyword, op_type in sql_patterns:
            if keyword in code_upper:
                # Make sure it's not already detected as a method
                if not any(op.get('sql_type') == op_type for op in operations):
                    operations.append({
                        "name": f"SQL:{keyword}",
                        "line": 0,
                        "type": "sql_operation",
                        "sql_type": op_type
                    })
        
        # Also scan for method calls
        method_patterns = [
            (r"\.truncate\s*\(", "truncate_method"),
            (r"\.load_csv\s*\(", "load_csv_method"),
            (r"\.run_query\s*\(", "run_query_method"),
        ]
        
        for pattern, op_type in method_patterns:
            if re.search(pattern, self.code, re.IGNORECASE):
                operations.append({
                    "name": f"METHOD:{op_type}",
                    "line": 0,
                    "type": "method_call"
                })
        
        self.operation_graph["operations"] = operations
        self.operation_graph["sequence"] = [op.get("name") for op in operations]
        print(f"  [Phase 1.5] Detected {len(operations)} operations: {[op.get('name') for op in operations[:20]]}")
        return self.operation_graph

    def _get_function_name(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None

    def _classify_operation(self, func_name: str) -> str:
        if func_name in ["get_secret", "from_connection_string"]:
            return "auth"
        elif func_name in ["list_blobs", "list_containers", "get_blob_client"]:
            return "source_read"
        elif func_name in ["download_blob", "read"]:
            return "source_fetch"
        elif func_name in ["run_query", "execute"]:
            return "query_execute"
        elif func_name in ["load_csv", "insert", "update", "delete"]:
            return "target_write"
        elif func_name in ["commit", "rollback"]:
            return "transaction"
        return "general"

    def _build_sequence(self, operations: List[Dict]) -> List[str]:
        return [op["name"] for op in sorted(operations, key=lambda x: x["line"])]

    def _extract_sql_from_call(self, call_node: ast.Call) -> List[Dict]:
        """Extract SQL keywords from arguments passed to run_query/execute."""
        sql_ops = []
        
        # Check all arguments for string constants
        for arg in call_node.args:
            sql_text = self._extract_string_from_node(arg)
            if sql_text:
                sql_ops.extend(self._parse_sql_operations(sql_text))
        
        # Also check keyword arguments
        for keyword in call_node.keywords:
            sql_text = self._extract_string_from_node(keyword.value)
            if sql_text:
                sql_ops.extend(self._parse_sql_operations(sql_text))
        
        return sql_ops

    def _extract_string_from_node(self, node: ast.expr) -> str:
        """Extract string constant from AST node."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return ""

    def _parse_sql_operations(self, sql_text: str) -> List[Dict]:
        """Parse SQL text to extract operation keywords."""
        ops = []
        sql_upper = sql_text.upper()
        
        # Extract SQL operations (order matters for regex matching)
        operations_to_find = [
            ("TRUNCATE TABLE", "truncate"),
            ("COPY INTO", "copy_into"),
            ("MERGE INTO", "merge"),
            ("INSERT INTO", "insert"),
            ("CREATE TABLE", "create_table"),
            ("CREATE TEMPORARY TABLE", "create_temp_table"),
            ("CREATE OR REPLACE TABLE", "create_or_replace_table"),
            ("LATERAL FLATTEN", "lateral_flatten"),
            ("CALL", "procedure_call"),
            ("DELETE FROM", "delete"),
            ("UPDATE", "update"),
            ("SELECT", "select"),
        ]
        
        for sql_keyword, op_type in operations_to_find:
            if sql_keyword in sql_upper:
                ops.append({
                    "name": f"SQL:{sql_keyword}",
                    "line": 0,
                    "type": "sql_operation",
                    "sql_type": op_type
                })
                # Don't break - one query might have multiple operations
        
        # Also detect patterns like "WHERE NOT EXISTS" for deduplication
        if "WHERE NOT EXISTS" in sql_upper or "DISTINCT" in sql_upper:
            ops.append({
                "name": "SQL:DEDUP_PATTERN",
                "line": 0,
                "type": "sql_operation",
                "sql_type": "dedup"
            })
        
        return ops

    def _extract_sql_from_strings(self) -> List[Dict]:
        """Scan entire code for SQL strings that might not be in function calls."""
        ops = []
        
        # Find all string literals in the code
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                sql_text = node.value
                # Only process strings that look like SQL
                if any(kw in sql_text.upper() for kw in ["TRUNCATE", "COPY INTO", "INSERT INTO", "MERGE", "CREATE TABLE", "LATERAL", "DELETE", "UPDATE"]):
                    ops.extend(self._parse_sql_operations(sql_text))
        
        return ops

    def _extract_loader_methods(self) -> List[Dict]:
        """Detect loader method calls like truncate(), load_csv(), run_query()."""
        ops = []
        
        # Look for calls like snowflake_conn.truncate(...), snowflake_conn.load_csv(...), etc.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                method_name = node.func.attr
                
                if method_name == "truncate":
                    ops.append({
                        "name": "SQL:TRUNCATE TABLE",
                        "line": getattr(node, "lineno", 0),
                        "type": "sql_operation",
                        "sql_type": "truncate"
                    })
                elif method_name == "load_csv":
                    ops.append({
                        "name": "SQL:COPY INTO",
                        "line": getattr(node, "lineno", 0),
                        "type": "sql_operation",
                        "sql_type": "copy_into"
                    })
                elif method_name in ["run_query", "execute"]:
                    # These are already handled in _extract_sql_from_call
                    pass
        
        return ops

    def _map_patterns_to_context(self):
        self._map_error_handling_context()
        self._map_retry_context()
        self._map_transaction_context()
        self._map_operation_coverage()

    def _map_error_handling_context(self):
        error_handlers = []
        
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Try):
                handler_line = getattr(node, "lineno", 0)
                handlers = [self._get_exception_type(h) for h in node.handlers]
                error_handlers.append({
                    "line": handler_line,
                    "exceptions": handlers,
                    "has_reraise": any(isinstance(n, ast.Raise) for n in ast.walk(node))
                })
        
        self.pattern_context["error_handling"] = {
            "present": bool(error_handlers),
            "handlers": error_handlers,
            "comprehensive": len(error_handlers) >= 2
        }

    def _get_exception_type(self, handler: ast.ExceptHandler) -> str:
        if handler.type is None:
            return "BaseException"
        if isinstance(handler.type, ast.Name):
            return handler.type.id
        return "Exception"

    def _map_retry_context(self):
        retry_patterns = []
        
        for node in ast.walk(self.tree):
            if isinstance(node, ast.For):
                if self._is_retry_loop(node):
                    retry_patterns.append({
                        "type": "loop",
                        "line": getattr(node, "lineno", 0)
                    })
        
        self.pattern_context["retry_logic"] = {
            "present": bool(retry_patterns),
            "patterns": retry_patterns,
            "in_error_handler": self._is_in_exception_handler()
        }

    def _is_retry_loop(self, node: ast.For) -> bool:
        code_slice = ast.unparse(node)
        return any(kw in code_slice for kw in ["max_retries", "range(", "attempt"])

    def _is_in_exception_handler(self) -> bool:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    for child in ast.walk(handler):
                        if isinstance(child, ast.For):
                            if self._is_retry_loop(child):
                                return True
        return False

    def _map_transaction_context(self):
        transactions = []
        
        for node in ast.walk(self.tree):
            if isinstance(node, ast.With):
                if "context" in ast.unparse(node).lower():
                    transactions.append({
                        "type": "context_manager",
                        "line": getattr(node, "lineno", 0)
                    })
        
        if re.search(r"BEGIN|COMMIT|ROLLBACK", self.code, re.I):
            transactions.append({"type": "explicit_sql"})
        
        self.pattern_context["transaction_management"] = {
            "present": bool(transactions),
            "patterns": transactions,
            "wraps_all_ops": self._transaction_wraps_all_operations()
        }

    def _transaction_wraps_all_operations(self) -> bool:
        has_tx = bool(self.pattern_context.get("transaction_management", {}).get("patterns"))
        total_ops = len(self.operation_graph.get("operations", []))
        wrapped_ops = 0
        
        if has_tx:
            for tx in self.pattern_context["transaction_management"]["patterns"]:
                if tx["type"] == "context_manager":
                    wrapped_ops += 1
        
        return wrapped_ops > 0 and total_ops > 0

    def _map_operation_coverage(self):
        ops = self.operation_graph.get("operations", [])
        op_types = {}
        
        for op in ops:
            op_type = op.get("type", "general")
            op_types[op_type] = op_types.get(op_type, 0) + 1
        
        self.pattern_context["operation_coverage"] = {
            "total_operations": len(ops),
            "by_type": op_types,
            "has_auth": "auth" in op_types,
            "has_source_read": "source_read" in op_types,
            "has_target_write": "target_write" in op_types
        }
