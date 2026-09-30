import ast
import re
from typing import Optional, Dict, List
from pathlib import Path
from utils.models import Metadata, TestResult


class ASTParser:
    def __init__(self, code: str):
        self.code = code
        self.tree = None
        self.metadata = Metadata()
        self.functions = []
        self.classes = []
        self.imports = []
        self.sql_statements = []
        
        try:
            self.tree = ast.parse(code)
        except SyntaxError as e:
            raise ValueError(f"Invalid Python code: {e}")

    def extract_metadata(self) -> Metadata:
        self.extract_key_vault_usage()
        self.extract_azure_connection()
        self.extract_snowflake_connection()
        self.extract_database_table_info()
        self.extract_container_name_from_assignments()
        self.extract_functions()
        self.extract_classes()
        self.extract_imports()
        self.extract_sql_statements()
        self.metadata.functions = self.functions
        self.metadata.classes = self.classes
        self.metadata.imports = self.imports
        self.metadata.sql_statements = self.sql_statements
        return self.metadata

    def extract_key_vault_usage(self):
        """
        Detect any secrets/credential mechanism — not tied to AyAzureKeyVault.
        Covers: AzureKeyVault, AWS Secrets Manager, HashiCorp Vault, env vars, config creds.
        """
        credential_patterns = [
            "AyAzureKeyVault", "SecretClient", "azure.keyvault",
            "secretsmanager", "get_secret_value", "hvac", "vault.read",
        ]
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                unparsed = ast.unparse(node)
                if any(p in unparsed for p in credential_patterns):
                    self.metadata.azure_connection = unparsed
                    return
                if isinstance(node.func, ast.Attribute) and node.func.attr in ("get_secret", "get_secret_value"):
                    if not self.metadata.azure_connection:
                        self.metadata.azure_connection = unparsed

    def extract_container_name_from_assignments(self):
        # Match any variable whose name suggests it holds a container/bucket/source identifier
        # BUT: Exclude Snowflake-specific names (stage, warehouse, database, schema, etc.)
        container_var_patterns = re.compile(
            r'container_name|bucket_name|source_container|blob_container|storage_container',
            re.IGNORECASE
        )
        snowflake_exclude = re.compile(
            r'stage|warehouse|database|schema|account|user|role',
            re.IGNORECASE
        )
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        # Only extract if it matches container patterns AND is not a Snowflake term
                        if container_var_patterns.search(target.id) and not snowflake_exclude.search(target.id):
                            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                                self.metadata.azure_container = node.value.value

    def extract_azure_connection(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                if self._is_azure_call(node):
                    if not self.metadata.azure_connection:
                        self.metadata.azure_connection = ast.unparse(node)
                    
                    if self._check_container_reference(node):
                        container_name = self._extract_string_arg(node)
                        if container_name:
                            self.metadata.azure_container = container_name

    def extract_snowflake_connection(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                if self._is_snowflake_call(node):
                    self.metadata.snowflake_connection = ast.unparse(node)
                    self._extract_snowflake_params(node)

    def extract_database_table_info(self):
        """
        Only extract DB/schema/table from string VALUES that appear as actual
        identifiers — not dict key strings like 'database', 'schema', 'warehouse'.
        These are only used as a last-resort fallback; YAML config takes priority.
        """
        # Common dict key strings used in ETL code — never treat these as values
        key_strings = {
            'database', 'schema', 'warehouse', 'account', 'user', 'role',
            'tables', 'stage', 'name', 'blob_path', 'file_format', 'on_error',
            'secret_name', 'private_key', 'key_vault', 'container_name',
            'folder_prefix', 'blob_storage', 'snowflake', 'env', 'run_date',
        }
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.strip()
                if not value or len(value) > 60 or value.lower() in key_strings:
                    continue
                upper = value.upper()
                # Only match values that look like real identifiers (uppercase, underscores)
                # AND are not generic words
                if not self.metadata.audit_table and re.match(r'^[A-Za-z][A-Za-z0-9_]{3,50}$', value):
                    if 'AUDIT' in upper and '_' in value:
                        self.metadata.audit_table = value

    def _is_azure_call(self, node: ast.Call) -> bool:
        # Generic: any call that looks like a cloud storage / secret client
        source_patterns = [
            "BlobServiceClient", "ContainerClient", "BlobClient",
            "S3Client", "boto3", "SFTPClient", "paramiko",
            "AyAzureKeyVault", "SecretClient", "get_secret",
        ]
        if isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr or ""
            return any(p in attr_name for p in source_patterns)
        elif isinstance(node.func, ast.Name):
            return any(p in node.func.id for p in source_patterns)
        return False

    def _is_snowflake_call(self, node: ast.Call) -> bool:
        unparsed = ast.unparse(node)
        # Generic: any call that looks like a Snowflake connection or loader
        return bool(re.search(
            r'snowflake\.connector\.connect|AYSnowflakeLoader|SnowflakeConnection'
            r'|snowflake_connector|SnowflakeConnector|sf_connect|create_engine.*snowflake'
            r'|SnowflakeWriter|SnowflakeClient|snowflake_loader|SnowflakeLoader',
            unparsed, re.IGNORECASE
        ))

    def _check_container_reference(self, node: ast.Call) -> bool:
        if isinstance(node.func, ast.Attribute):
            return node.func.attr == "get_container_client"
        return False

    def _extract_string_arg(self, node: ast.Call) -> Optional[str]:
        if node.args and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str):
                return node.args[0].value
        return None

    def _extract_snowflake_params(self, node: ast.Call):
        for keyword in node.keywords:
            if keyword.arg == "snowflake_database":
                if isinstance(keyword.value, ast.Name):
                    pass
                elif isinstance(keyword.value, ast.Constant):
                    self.metadata.target_database = keyword.value.value
            elif keyword.arg == "snowflake_schema":
                if isinstance(keyword.value, ast.Constant):
                    self.metadata.target_schema = keyword.value.value
            elif keyword.arg == "database":
                if isinstance(keyword.value, ast.Constant):
                    self.metadata.target_database = keyword.value.value
            elif keyword.arg == "schema":
                if isinstance(keyword.value, ast.Constant):
                    self.metadata.target_schema = keyword.value.value

    def extract_functions(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef):
                self.functions.append({
                    "name": node.name,
                    "args": [arg.arg for arg in node.args.args],
                    "line": node.lineno,
                })

    def extract_classes(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
                self.classes.append({
                    "name": node.name,
                    "methods": methods,
                    "line": node.lineno,
                })

    def extract_imports(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                self.imports.append(f"{node.module or ''}")

    def extract_sql_statements(self):
        sql_pattern = r"(SELECT|INSERT|UPDATE|DELETE|COPY INTO|CREATE|DROP)\s+(?:INTO|FROM|.*?)\s+[\w\.]+"
        matches = re.findall(sql_pattern, self.code, re.IGNORECASE)
        self.sql_statements = list(set(matches))

    def classify_metadata_fields(self) -> dict:
        """
        Classify each discovered metadata field as:
          Static   — hardcoded literal value
          Dynamic  — runtime-resolved (format(), f-string with variable, .format(DATE), argparse)
          Derived  — computed from other values (concatenation, path join, regex, config lookup)
        """
        classifications = {}
        # Patterns that indicate dynamic resolution
        dynamic_patterns = [
            r'\{DATE\}', r'\.format\(', r'f["\'].*\{', r'args\.\w+',
            r'os\.environ', r'os\.getenv', r'yaml\.safe_load', r'get_secret',
            r'run_date', r'datetime\.now', r'strftime',
        ]
        # Patterns that indicate derived values
        derived_patterns = [
            r'os\.path\.join', r'\+.*path', r'Path\(', r're\.sub',
            r'replace\(', r'split\(.*\[', r'\[.*\].*\+',
        ]
        fields_to_check = {
            'azure_container': self.metadata.azure_container,
            'target_database': self.metadata.target_database,
            'target_schema': self.metadata.target_schema,
            'target_table': self.metadata.target_table,
            'audit_table': self.metadata.audit_table,
        }
        for field_name, value in fields_to_check.items():
            if value is None:
                classifications[field_name] = 'Missing'
                continue
            # Check if the value appears as a literal string in the code
            if f"'{value}'" in self.code or f'"{value}"' in self.code:
                # Also check if it's used in a dynamic expression
                is_dynamic = any(re.search(p, self.code) for p in dynamic_patterns)
                is_derived = any(re.search(p, self.code) for p in derived_patterns)
                if is_derived:
                    classifications[field_name] = 'Derived'
                elif is_dynamic:
                    classifications[field_name] = 'Dynamic'
                else:
                    classifications[field_name] = 'Static'
            else:
                # Value not found as literal — it's resolved at runtime
                is_derived = any(re.search(p, self.code) for p in derived_patterns)
                classifications[field_name] = 'Derived' if is_derived else 'Dynamic'
        return classifications

    def find_string_patterns(self, patterns: Dict[str, str]) -> Dict[str, List[str]]:
        found = {}
        for pattern_name, pattern in patterns.items():
            matches = re.findall(pattern, self.code, re.IGNORECASE)
            if matches:
                found[pattern_name] = matches
        return found


class Phase1MetadataExtraction:
    def __init__(self, python_code: str, file_path: Optional[str] = None, env: str = None):
        self.code = python_code
        self.file_path = file_path
        self.env = env
        self.parser = ASTParser(python_code)
        self.metadata = None
        self.test_results = []

    def _has_groq_key(self) -> bool:
        from utils.llm_client import has_llm_key
        return has_llm_key()

    def _extract_metadata_via_llm(self) -> dict:
        try:
            from utils.llm_client import call_llm
            from pathlib import Path
            print("  [Phase 1] LLM: Attempting metadata extraction with LLM...")

            yaml_content = ""
            if self.file_path:
                target_dir = Path(self.file_path).parent.absolute()
                found_config = None
                for base_dir in [target_dir, target_dir.parent]:
                    for f_name in ["config.yaml", "config.yml"]:
                        p = base_dir / f_name
                        if p.exists():
                            found_config = p
                            break
                    if found_config:
                        break
                if found_config:
                    try:
                        with open(found_config, 'r') as f:
                            yaml_content = f.read()
                    except Exception:
                        pass

            prompt = f"""Analyze the following Python ETL code and its associated config file.
Do NOT assume any specific library or framework — the ETL may use any credential mechanism
(Azure Key Vault, AWS Secrets Manager, env vars, etc.) and any data source
(Azure Blob, S3, SFTP, local files, API, etc.).
The runtime environment is: '{self.env}' — use this env block from the config.

Code:
```python
{self.code}
```
"""
            if yaml_content:
                prompt += f"""
Associated config file:
```yaml
{yaml_content}
```
"""
            prompt += """
By reading the code AND config together, extract:
1. Data source identifier — container name, bucket name, SFTP path, local dir, or API endpoint (whichever applies)
2. Snowflake target database name
3. Snowflake target schema name
4. Primary target table name (first/main table loaded)
5. Audit log table name (any table with 'audit' in the name, or None)

If a value is read dynamically from config, use the actual value from the config file.

Response format (exactly this, one per line):
CONTAINER: <value or None>
DATABASE: <value or None>
SCHEMA: <value or None>
TABLE: <value or None>
AUDIT_TABLE: <value or None>"""

            response = call_llm(prompt, max_tokens=500, temperature=0.1)
            results = {}
            if response:
                for line in response.split('\n'):
                    line = line.strip()
                    if line.startswith("CONTAINER:"):
                        results["azure_container"] = line.replace("CONTAINER:", "").strip()
                    elif line.startswith("DATABASE:"):
                        results["target_database"] = line.replace("DATABASE:", "").strip()
                    elif line.startswith("SCHEMA:"):
                        results["target_schema"] = line.replace("SCHEMA:", "").strip()
                    elif line.startswith("TABLE:"):
                        results["target_table"] = line.replace("TABLE:", "").strip()
                    elif line.startswith("AUDIT_TABLE:"):
                        results["audit_table"] = line.replace("AUDIT_TABLE:", "").strip()
            
            for k, v in list(results.items()):
                if not v or v.lower() in ["none", "", "null", "<none>"]:
                    results[k] = None
            return results
        except Exception as e:
            print(f"  [Phase 1] LLM: FALLBACK - {type(e).__name__}: {str(e)[:100]}")
            return {}

    def _load_yaml_config_metadata(self):
        """
        Generic config loader — works with any YAML structure.
        Strategy:
          1. Find the first env-keyed block (dev/prd/staging/uat or first dict key)
          2. Walk ALL nested keys looking for Snowflake DB/schema/tables and source identifiers
          3. No assumptions about key names — uses heuristics on key/value content
        """
        if not self.file_path:
            return
        try:
            import yaml
            target_dir = Path(self.file_path).parent.absolute()
            found_config = None
            for base_dir in [target_dir, target_dir.parent]:
                for f_name in ["config.yaml", "config.yml"]:
                    p = base_dir / f_name
                    if p.exists():
                        found_config = p
                        break
                if found_config:
                    break
            if not found_config:
                return

            with open(found_config, 'r') as stream:
                config_data = yaml.safe_load(stream)
            if not isinstance(config_data, dict) or not config_data:
                return

            # Pick the env block — prefer runtime --env value, else first dict value
            env_cfg = None
            if self.env and self.env in config_data and isinstance(config_data[self.env], dict):
                env_cfg = config_data[self.env]
            if env_cfg is None:
                for v in config_data.values():
                    if isinstance(v, dict):
                        env_cfg = v
                        break
            if env_cfg is None:
                env_cfg = config_data

            self._extract_from_config_block(env_cfg)

        except Exception as e:
            print(f"  [Phase 1] Error parsing local YAML configuration: {e}")

    def _extract_from_config_block(self, block: dict):
        """Walk a config dict block and extract metadata using generic heuristics."""
        if not isinstance(block, dict):
            return

        # ── Credential / secrets mechanism ──
        if not self.metadata.azure_connection:
            for k, v in block.items():
                k_lower = k.lower()
                if any(kw in k_lower for kw in ['key_vault', 'keyvault', 'secret', 'vault']):
                    self.metadata.azure_connection = f"{k}: {v}"
                    break

        # ── Source identifier (container / bucket / path / host) ──
        if not self.metadata.azure_container:
            # Check known source sections first
            for section_key in ['blob_storage', 's3', 'sftp', 'ftp', 'source', 'storage']:
                section = block.get(section_key)
                if isinstance(section, dict):
                    for ck in ['container_name', 'bucket', 'bucket_name', 'path', 'host', 'directory', 'folder']:
                        if section.get(ck):
                            self.metadata.azure_container = str(section[ck])
                            break
                    if self.metadata.azure_container:
                        break
            # Fallback: scan all string values for container/bucket-like keys (but NOT key_vault)
            if not self.metadata.azure_container:
                for k, v in block.items():
                    # Skip key_vault, secret_name, and other credential-related keys
                    if any(kw in k.lower() for kw in ['key_vault', 'keyvault', 'secret_name', 'vault']):
                        continue
                    if isinstance(v, str) and any(kw in k.lower() for kw in ['container', 'bucket', 'stage']):
                        self.metadata.azure_container = v
                        break

        # ── Snowflake connection + DB/schema/tables ──
        sf_block = None
        for k, v in block.items():
            if 'snowflake' in k.lower():
                sf_block = v
                break

        if isinstance(sf_block, list) and sf_block:
            sf_block = sf_block[0]

        if isinstance(sf_block, dict):
            acct = sf_block.get('account', '')
            user = sf_block.get('user', sf_block.get('username', ''))
            role = sf_block.get('role', '')
            if acct or user:
                self.metadata.snowflake_connection = f"Snowflake: account={acct}, user={user}, role={role}"
            if not self.metadata.target_database:
                self.metadata.target_database = sf_block.get('database') or sf_block.get('db')
            if not self.metadata.target_schema:
                self.metadata.target_schema = sf_block.get('schema')
            # Stage as fallback source identifier
            if not self.metadata.azure_container and sf_block.get('stage'):
                self.metadata.azure_container = sf_block['stage']

            tables = sf_block.get('tables', [])
            if isinstance(tables, list) and tables:
                names = [t.get('name') for t in tables if isinstance(t, dict) and t.get('name')]
                if names and not self.metadata.target_table:
                    self.metadata.target_table = names[0]
                for n in names:
                    if 'audit' in n.lower() and not self.metadata.audit_table:
                        self.metadata.audit_table = n

            if not self.metadata.target_table:
                tables_to_copy = sf_block.get('tables_to_copy', [])
                if isinstance(tables_to_copy, list) and tables_to_copy:
                    # For DDL-based ETLs, look for INSERT INTO statements in the code
                    insert_matches = re.findall(r'INSERT\s+INTO\s+([A-Za-z0-9_.]+)', self.code, re.IGNORECASE)
                    if insert_matches:
                        # Use the first INSERT target as the primary table
                        self.metadata.target_table = insert_matches[0]
                        # Update database/schema if they appear in the INSERT statement
                        if '.' in insert_matches[0]:
                            parts = insert_matches[0].split('.')
                            if len(parts) >= 3:
                                self.metadata.target_database = parts[0]
                                self.metadata.target_schema = parts[1]
                    else:
                        # Fallback: use the filename from tables_to_copy
                        first = tables_to_copy[0]
                        name = first.get('filename') or first.get('name') if isinstance(first, dict) else str(first)
                        if name:
                            self.metadata.target_table = name

            # ── Pattern 3: flat string table keys e.g. term_target_table, pipe_target_table ──
            if not self.metadata.target_table:
                table_key_suffixes = ('_target_table', '_table', 'tablename', 'table_name')
                for k, v in sf_block.items():
                    if isinstance(v, str) and any(k.lower().endswith(s) for s in table_key_suffixes):
                        if 'audit' not in k.lower():
                            self.metadata.target_table = v
                            break
            if not self.metadata.audit_table:
                for k, v in sf_block.items():
                    if isinstance(v, str) and 'audit' in k.lower():
                        self.metadata.audit_table = v
                        break

            if not self.metadata.target_table:
                call_procs = re.findall(r"CALL\s+([A-Za-z0-9_]+)\s*\(", self.code, re.IGNORECASE)
                call_in_strings = re.findall(r"['\"]\s*CALL\s+([A-Za-z0-9_]+)\s*\(", self.code, re.IGNORECASE)
                all_procs = list(dict.fromkeys(call_procs + call_in_strings))
                if all_procs:
                    self.metadata.target_table = f"via stored procedures: {', '.join(all_procs)}"

        for k, v in block.items():
            if not isinstance(v, (str, int)):
                continue
            k_lower = k.lower()
            if not self.metadata.target_database and k_lower in ('database', 'db', 'target_database'):
                self.metadata.target_database = str(v)
            if not self.metadata.target_schema and k_lower in ('schema', 'target_schema'):
                self.metadata.target_schema = str(v)
            if not self.metadata.target_table and any(k_lower.endswith(s) for s in ('_target_table', '_table', 'tablename', 'table_name')):
                self.metadata.target_table = str(v)
            if not self.metadata.audit_table and 'audit' in k_lower and 'table' in k_lower:
                self.metadata.audit_table = str(v)

        for v in block.values():
            if isinstance(v, dict):
                self._extract_from_config_block(v)

    def run(self) -> tuple:
        self.metadata = self.parser.extract_metadata()
        self._load_yaml_config_metadata()
        
        if self._has_groq_key():
            print("  [Phase 1] Enhancing metadata extraction with LLM...")
            llm_meta = self._extract_metadata_via_llm()
            
            if not self.metadata.azure_container and llm_meta.get("azure_container"):
                self.metadata.azure_container = llm_meta["azure_container"]
            if not self.metadata.target_database and llm_meta.get("target_database"):
                self.metadata.target_database = llm_meta["target_database"]
            if not self.metadata.target_schema and llm_meta.get("target_schema"):
                self.metadata.target_schema = llm_meta["target_schema"]
            if not self.metadata.target_table and llm_meta.get("target_table"):
                self.metadata.target_table = llm_meta["target_table"]
            if not self.metadata.audit_table and llm_meta.get("audit_table"):
                self.metadata.audit_table = llm_meta["audit_table"]

        # Classify each metadata field as Static / Dynamic / Derived
        self.metadata.field_classifications = self.parser.classify_metadata_fields()
        print(f"  [Phase 1] Field classifications: {self.metadata.field_classifications}")

        self._generate_test_results()
        return self.metadata, self.test_results

    def _detect_credential_mechanism(self) -> tuple:
        code = self.code
        checks = [
            ("Azure Key Vault (AyAzureKeyVault)",
             r"AyAzureKeyVault|azureKV\.get_secret|AzureKeyVault",
             "AyAzureKeyVault instantiation detected"),
            ("Azure Key Vault (SecretClient)",
             r"SecretClient|azure\.keyvault|KeyVaultSecret",
             "Azure SecretClient detected"),
            ("AWS Secrets Manager",
             r"boto3.*secret|secretsmanager|get_secret_value",
             "AWS Secrets Manager detected"),
            ("HashiCorp Vault",
             r"hvac|vault\.read|vault\.secrets",
             "HashiCorp Vault client detected"),
            ("GCP Secret Manager",
             r"google\.cloud\.secretmanager|SecretManagerServiceClient",
             "GCP Secret Manager detected"),
            ("Environment Variables",
             r"os\.environ|os\.getenv|dotenv|load_dotenv",
             "Environment variable credential loading detected"),
            ("Config File Credentials",
             r"yaml\.safe_load.*password|config.*password|config.*secret",
             "Credentials loaded from config file"),
        ]
        for name, pattern, detail in checks:
            if re.search(pattern, code, re.IGNORECASE):
                return name, True, detail
        return "Unknown", False, "No recognisable credential/secrets mechanism found"

    def _detect_source_mechanism(self) -> tuple:
        code = self.code
        checks = [
            ("Azure Blob Storage",
             r"BlobServiceClient|blob_path|azure\.storage\.blob",
             "Azure Blob Storage client detected"),
            ("Azure Data Lake (ADLS)",
             r"DataLakeServiceClient|adls|datalake|abfss://",
             "Azure Data Lake Storage detected"),
            ("AWS S3",
             r"boto3\.client.*s3|s3\.get_object|s3://",
             "AWS S3 client detected"),
            ("GCS",
             r"google\.cloud\.storage|storage\.Client|gs://",
             "Google Cloud Storage detected"),
            ("SFTP",
             r"paramiko|SFTPClient|sftp\.get|sftp\.listdir",
             "SFTP (paramiko) client detected"),
            ("Local Filesystem",
             r"os\.listdir|isfile|os\.path\.join.*\.csv|open\(.*\.csv",
             "Local filesystem file access detected"),
            ("HTTP/REST API",
             r"requests\.get|requests\.post|urllib\.request",
             "HTTP/REST API call detected"),
            ("Database (non-Snowflake)",
             r"psycopg2|pymysql|pyodbc|cx_Oracle",
             "Source database connection detected"),
            ("Snowflake Stage (COPY INTO)",
             r"snowflake_stage|COPY\s+INTO|load_csv",
             "Snowflake internal stage / COPY INTO detected"),
        ]
        # Check config env block first — most reliable signal
        if self.file_path:
            try:
                import yaml
                from pathlib import Path as _Path
                for base in [_Path(self.file_path).parent, _Path(self.file_path).parent.parent]:
                    for fname in ["config.yaml", "config.yml"]:
                        p = base / fname
                        if p.exists():
                            with open(p) as f:
                                cfg = yaml.safe_load(f) or {}
                            env_block = None
                            if self.env and self.env in cfg and isinstance(cfg[self.env], dict):
                                env_block = cfg[self.env]
                            else:
                                env_block = next((v for v in cfg.values() if isinstance(v, dict)), None)
                            if env_block:
                                source_section_map = {
                                    "blob_storage": ("Azure Blob Storage", "blob_storage section found in config"),
                                    "adls": ("Azure Data Lake (ADLS)", "adls section found in config"),
                                    "sftp": ("SFTP", "sftp section found in config"),
                                    "s3": ("AWS S3", "s3 section found in config"),
                                    "gcs": ("GCS", "gcs section found in config"),
                                    "api": ("HTTP/REST API", "api section found in config"),
                                    "local": ("Local Filesystem", "local section found in config"),
                                }
                                for key, (name, detail) in source_section_map.items():
                                    if key in env_block:
                                        return name, True, detail
            except Exception:
                pass
        for name, pattern, detail in checks:
            if re.search(pattern, code, re.IGNORECASE):
                return name, True, detail
        return "Unknown", False, "No recognisable data source mechanism found"

    def _generate_test_results(self):
        """
        TC001 — Credential / Secrets Mechanism (dynamic: detects whatever the ETL uses)
        TC002 — Data Source / Stage Configured (dynamic: detects blob/s3/sftp/local/etc.)
        TC003 — Snowflake Connection Config
        TC004 — Snowflake Target Metadata Extraction
        TC005 — Safety Check - Target Is Not MAIN/PROD Database
        """
        db = self.metadata.target_database
        schema = self.metadata.target_schema
        table = self.metadata.target_table
        audit = self.metadata.audit_table

        cred_name, cred_found, cred_detail = self._detect_credential_mechanism()
        src_name, src_found, src_detail = self._detect_source_mechanism()

        has_db     = db is not None
        has_schema = schema is not None
        has_table  = table is not None
        has_audit  = audit is not None

        # Audit table is optional — only required if the ETL actually references one
        etl_uses_audit = bool(re.search(r'audit', self.code, re.IGNORECASE))
        core_present = has_db and has_schema and has_table
        all_present  = core_present and (has_audit if etl_uses_audit else True)

        extracted = []
        if has_db:     extracted.append(f"Database: {db}")
        if has_schema: extracted.append(f"Schema: {schema}")
        if has_table:  extracted.append(f"Table: {table}")
        if has_audit:  extracted.append(f"Audit: {audit}")
        elif not etl_uses_audit: extracted.append("Audit: N/A (ETL has no audit table)")

        if all_present:
            meta_finding = "Snowflake target metadata extracted — " + " | ".join(extracted)
        else:
            missing = []
            if not has_db:     missing.append("database")
            if not has_schema: missing.append("schema")
            if not has_table:  missing.append("target_table")
            if etl_uses_audit and not has_audit: missing.append("audit_table")
            meta_finding = "Snowflake target metadata incomplete — missing: " + ", ".join(missing)

        tests = [
            {
                "id": "TC001",
                "name": "Credential / Secrets Mechanism",
                "check": lambda: cred_found,
                "finding_pass": f"mechanism: {cred_name}",
                "finding_fail": "mechanism: none",
                "recommendation": "Use a secrets manager (Azure Key Vault, AWS Secrets Manager, env vars) — never hardcode credentials",
                "relevance": "Required",
            },
            {
                "id": "TC002",
                "name": "Data Source Configured",
                "check": lambda: src_found,
                "finding_pass": f"source: {src_name}",
                "finding_fail": "source: none",
                "recommendation": "Ensure the ETL explicitly sets up its data source connection or file path",
                "relevance": "Required",
            },
            {
                "id": "TC003",
                "name": "Snowflake Connection Config",
                "check": lambda: self.metadata.snowflake_connection is not None,
                "finding_pass": self.metadata.snowflake_connection or "connection: configured",
                "finding_fail": "connection: none — no Snowflake connector or loader instantiation found",
                "recommendation": "Add a Snowflake connection using snowflake.connector.connect or a wrapper with credentials from a secrets manager",
                "relevance": "Required",
            },
            {
                "id": "TC004",
                "name": "Snowflake Target Metadata Extraction",
                "check": lambda: all_present,
                "finding_pass": f"db: {db} | schema: {schema} | tables: {table}" + (f" | audit: {audit}" if has_audit else ""),
                "finding_fail": f"db: {db or 'missing'} | schema: {schema or 'missing'} | tables: {table or 'missing'}",
                "recommendation": (
                    "Ensure config.yaml contains snowflake connection block with database, schema, "
                    "and at least one target table defined (tables list, tables_to_copy, or flat *_table keys)."
                    + (" Add an audit table entry if this ETL performs audit logging." if etl_uses_audit and not has_audit else "")
                ),
                "relevance": "Critical",
            },
            {
                "id": "TC005",
                "name": "Safety Check - Target Is Not MAIN/PROD Database",
                "check": lambda: (db or "").upper() not in ["MAIN", "PROD", "PRODUCTION"],
                "finding_pass": f"db: {db} | safe: yes",
                "finding_fail": f"db: {db} | safe: no (PROD/MAIN)",
                "recommendation": "Change target database away from MAIN/PROD",
                "relevance": "Critical",
            },
        ]

        for test in tests:
            passed = test["check"]()
            status = "PASS" if passed else "FAIL"
            finding = test["finding_pass"] if passed else test["finding_fail"]
            
            result = TestResult(
                test_id=test["id"],
                test_name=test["name"],
                phase="Metadata Extraction",
                status=status,
                finding=finding,
                recommendation="N/A" if passed else test["recommendation"],
                check_type="Static",
                severity="Info" if passed else ("Critical" if test["relevance"] == "Critical" else "High"),
                database_target=self.metadata.target_database or "N/A",
                stage2_relevance=test["relevance"],
            )
            self.test_results.append(result)

    def _check_config_file_valid(self) -> bool:
        if not self.file_path:
            return False
        try:
            import yaml
            target_dir = Path(self.file_path).parent.absolute()
            for base_dir in [target_dir, target_dir.parent]:
                for f_name in ["config.yaml", "config.yml"]:
                    p = base_dir / f_name
                    if p.exists():
                        with open(p, 'r') as f:
                            yaml.safe_load(f)
                        return True
            return False
        except Exception:
            return False
    
    def _check_cli_args_defined(self) -> bool:
        has_env = "add_argument" in self.code and "--env" in self.code
        has_run_date = "add_argument" in self.code and ("--run_date" in self.code or "--run-date" in self.code)
        return has_env and has_run_date
    
    def _check_snowflake_stage_configured(self) -> bool:
        if self.file_path:
            try:
                import yaml
                target_dir = Path(self.file_path).parent.absolute()
                for base_dir in [target_dir, target_dir.parent]:
                    for f_name in ["config.yaml", "config.yml"]:
                        p = base_dir / f_name
                        if p.exists():
                            with open(p, 'r') as f:
                                cfg = yaml.safe_load(f) or {}
                            for env_block in cfg.values():
                                if isinstance(env_block, dict):
                                    sf = env_block.get('snowflake')
                                    if isinstance(sf, list) and sf:
                                        sf = sf[0]
                                    if isinstance(sf, dict) and sf.get('stage'):
                                        return True
            except Exception:
                pass
        return "snowflake_stage" in self.code or ("@" in self.code and "COPY INTO" in self.code)
