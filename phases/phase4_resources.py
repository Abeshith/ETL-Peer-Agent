from typing import List, Dict, Any, Optional
from utils.models import TestResult
from src.config import Config
import yaml
import re
from pathlib import Path


class Phase4ResourceValidation:
    def __init__(self, metadata: Dict[str, Any], validate_resources: bool = False,
                 etl_file_path: Optional[str] = None, env: str = None):
        self.metadata = metadata
        self.validate_resources = validate_resources
        self.etl_file_path = etl_file_path
        self.env = env
        self.test_results = []
        self.azure_client = None
        self.snowflake_cursor = None
        self.etl_config = self._load_etl_yaml_config()
        self._llm_checks: List[Dict] = []
        self.test_counter = 0

    def _load_etl_yaml_config(self) -> dict:
        if not self.etl_file_path:
            return {}
        try:
            found_config = None
            for base_dir in [Path(self.etl_file_path).parent, Path(self.etl_file_path).parent.parent]:
                for fname in ["config.yaml", "config.yml"]:
                    p = base_dir / fname
                    if p.exists():
                        found_config = p
                        break
                if found_config:
                    break
            if found_config:
                with open(found_config, 'r') as f:
                    config_data = yaml.safe_load(f)
                if self.env and self.env in config_data:
                    return config_data[self.env]
                if isinstance(config_data, dict):
                    return next((v for v in config_data.values() if isinstance(v, dict)), {})
        except Exception:
            pass
        return {}

    def _has_llm(self) -> bool:
        from utils.llm_client import has_llm_key
        return has_llm_key()

    def _discover_checks_via_llm(self) -> List[Dict]:
        try:
            from utils.llm_client import call_llm
            print("  [Phase 4] LLM: Attempting to discover resource checks with LLM...")
            code = Path(self.etl_file_path).read_text(encoding="utf-8", errors="ignore") if self.etl_file_path else ""
            code_preview = (code[:1500] + "\n...[middle truncated]...\n" + code[-500:]) if len(code) > 2000 else code
            config_str = yaml.dump(self.etl_config, default_flow_style=False, width=80) if self.etl_config else "Not found"
            config_preview = config_str[:2000] if len(config_str) > 2000 else config_str

            prompt = f"""You are a data engineer. Identify external resources this ETL needs.

Code (truncated):
```python
{code_preview}
```

Config (env: {self.env}):
```yaml
{config_preview}
```

List resource checks ONLY (one per line, format: NAME | TYPE | TARGET):
- Secrets: Key Vault names, AWS secret names
- Source: Blob containers, S3 buckets, file paths
- Snowflake: database, schema, tables, stages, file formats, stored procedures

Output format (concise):
NAME: Resource name | TYPE: Secrets|Source|SnowflakeDB|SnowflakeSchema|SnowflakeTable|SnowflakeObject | TARGET: identifier"""

            response = call_llm(prompt, max_tokens=1000, temperature=0.1)
            if response:
                return self._parse_llm_check_blocks(response)
        except Exception as e:
            err_type = type(e).__name__
            err_msg = str(e)[:100]
            # Silently skip rate limit errors - they're transient
            if "429" not in err_msg and "RateLimit" not in err_type:
                print(f"  [Phase 4] LLM: FALLBACK - {err_type}: {err_msg}")
        return []

    def _parse_llm_check_blocks(self, response: str) -> List[Dict]:
        checks = []
        for line in response.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Parse "NAME: ... | TYPE: ... | TARGET: ..." format
            if "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            chk = {}
            for part in parts:
                if ":" in part:
                    key, val = part.split(":", 1)
                    key_upper = key.strip().upper()
                    if key_upper in ("NAME", "TYPE", "TARGET"):
                        chk[key_upper] = val.strip()
            if chk.get("NAME") and chk.get("TYPE") and chk.get("TARGET"):
                checks.append(chk)
        # Cleanup: remove invalid targets, shares checks, duplicate/hallucinated entries
        def _has_shares_key(node):
            if isinstance(node, dict):
                return "shares" in node or any(_has_shares_key(v) for v in node.values())
            if isinstance(node, list):
                return any(_has_shares_key(i) for i in node)
            return False
        has_shares = _has_shares_key(self.etl_config)
        if not has_shares:
            checks = [c for c in checks if not re.search(r'\bshare', c.get("TARGET", ""), re.I)]
        checks = [c for c in checks if c.get("TARGET", "").strip().upper() not in ("", "N/A", "NONE", "NULL")]
        return checks[:8]

    def run(self) -> List[TestResult]:
        if not self.validate_resources:
            self._generate_skipped_results()
        else:
            self._initialize_clients()
            self._run_fallback_checks()
        return self.test_results

    def _execute_llm_checks(self):
        target_db = (self.metadata.get("target_database") or "N/A").upper()

        for chk in self._llm_checks:
            self.test_counter += 1
            test_id = f"RES_{self.test_counter:03d}"
            name = chk.get("NAME", "Resource Check")
            ctype = chk.get("TYPE", "Other").upper()
            target = chk.get("TARGET", "")

            passed, finding = self._execute_single_check(ctype, target, target_db)

            self.test_results.append(TestResult(
                test_id=test_id,
                test_name=name,
                phase="Resource Validation",
                status="PASS" if passed else "FAIL",
                finding=finding,
                recommendation="N/A" if passed else f"Fix '{target}' or update config",
                check_type="Resource",
                severity="Info" if passed else "Critical",
                database_target=target_db,
                stage2_relevance="Critical",
            ))

    def _execute_single_check(self, check_type: str, target: str, target_db: str):
        try:
            if check_type == "SECRETS":
                passed = self._check_secrets(target)
                return passed, f"Secret '{target}' accessible" if passed else f"Secret '{target}' not found"
            elif check_type == "SOURCE":
                passed, _ = self._check_source(target)
                return passed, f"Source '{target}' accessible" if passed else f"Source '{target}' not found"
            elif check_type == "SNOWFLAKEDB":
                db = target or target_db
                passed = self._check_snowflake_database(db)
                return passed, f"Database '{db}' {'exists' if passed else 'NOT found'}"
            elif check_type == "SNOWFLAKESCHEMA":
                parts = [p.strip() for p in target.split(".")]
                db = parts[0] if len(parts) > 1 else target_db
                schema = parts[-1]
                passed = self._check_snowflake_schema(db, schema)
                return passed, f"Schema '{schema}' exists" if passed else f"Schema '{schema}' NOT found"
            elif check_type == "SNOWFLAKETABLE":
                parts = target.split(".")
                schema = self.metadata.get("target_schema") or ""
                if len(parts) == 3:
                    db, schema, table = parts
                elif len(parts) == 2:
                    db, table = parts
                else:
                    db, table = target_db, target
                passed = self._check_snowflake_table(db, schema, table)
                return passed, f"Table '{table}' exists" if passed else f"Table '{table}' NOT found"
            elif check_type == "SNOWFLAKEOBJECT":
                if not target or target.upper() in ('N/A', 'NONE', 'NULL'):
                    return True, "Not configured"
                passed = self._check_snowflake_object(target)
                return passed, f"Object '{target}' found" if passed else f"Object '{target}' NOT found"
            elif check_type == "OTHER":
                # DDL file check — resolve relative to ETL dir
                if re.search(r'\.sql$|ddl/', target, re.I):
                    if self.etl_file_path:
                        etl_dir = Path(self.etl_file_path).parent
                        # Handle comma-separated list of DDL files
                        ddl_targets = [t.strip() for t in target.split(',') if t.strip()]
                        found, missing = [], []
                        for dt in ddl_targets:
                            ddl_path = etl_dir / dt.lstrip('/')
                            if ddl_path.exists():
                                found.append(dt)
                            elif '*' in dt:
                                matches = list(etl_dir.glob(dt.lstrip('/')))
                                if matches:
                                    found.extend([m.name for m in matches])
                                else:
                                    missing.append(dt)
                            else:
                                missing.append(dt)
                        if missing:
                            return False, f"DDL file '{', '.join(missing)}' not found relative to ETL directory"
                        return True, f"DDL files found: {found}"
                    return False, f"DDL file '{target}' not found relative to ETL directory"
                # Stored procedure check — verify target matches an actual CALL in the ETL code
                if target and self.etl_file_path:
                    try:
                        etl_code = Path(self.etl_file_path).read_text(encoding="utf-8", errors="ignore")
                        call_names = re.findall(r"CALL\s+([A-Za-z0-9_]+)\s*\(", etl_code, re.IGNORECASE)
                        if any(target.upper() == c.upper() for c in call_names):
                            passed = self._check_snowflake_object(target)
                            return passed, f"Stored procedure '{target}' {'found' if passed else 'NOT found'}"
                    except Exception:
                        pass
                if not target:
                    return True, "No specific target — skipped"
                passed, msg = self._check_generic(target)
                return passed, msg
            else:
                passed, msg = self._check_generic(target)
                return passed, msg
        except Exception as e:
            return False, f"Check error: {e}"

    def _run_fallback_checks(self):
        target_db = (self.metadata.get("target_database") or "N/A").upper()
        target_schema = (self.metadata.get("target_schema") or "").upper()
        source_id = self._find_source_identifier()
        source_type = self._detect_source_type()
        kv_name = self._find_kv_name()
        tables = self._resolve_tables_from_config()

        # PRELIMINARY CHECK: Config File Exists
        self.test_counter += 1
        config_path = None
        if self.etl_file_path:
            for base_dir in [Path(self.etl_file_path).parent, Path(self.etl_file_path).parent.parent]:
                for fname in ["config.yaml", "config.yml"]:
                    p = base_dir / fname
                    if p.exists():
                        config_path = p
                        break
                if config_path:
                    break
        config_exists = config_path is not None
        config_name = config_path.name if config_path else "config.yml"
        self._add_result(f"RES_{self.test_counter:03d}", "Configuration File",
                         config_exists,
                         f"Config file '{config_name}' found" if config_exists else f"Config file '{config_name}' not found",
                         f"Config file '{config_name}' not accessible",
                         f"Ensure config.yml or config.yaml exists in ETL directory or parent directory",
                         target_db)
        
        # If config doesn't exist, skip remaining checks since ETL can't run
        if not config_exists:
            return

        # MAIN CHECK 1: Key Vault / Secrets
        self.test_counter += 1
        if kv_name:
            passed = self._check_secrets(kv_name)
            self._add_result(f"RES_{self.test_counter:03d}", f"Azure Key Vault ({kv_name})",
                             passed, f"Secrets/credentials for '{kv_name}' verified",
                             f"Key Vault '{kv_name}' unreachable or secrets missing",
                             f"Verify key_vault '{kv_name}' in config and run az login", target_db)
        else:
            self._add_result(f"RES_{self.test_counter:03d}", "Azure Key Vault", False,
                             "No key vault configured", "Key vault not found in config",
                             "Add key_vault configuration to config.yml", target_db)

        # MAIN CHECK 2: Source (Blob/S3/SFTP/Local) — skip entirely when no external source
        self.test_counter += 1
        if source_type == "unknown":
            self._add_result(f"RES_{self.test_counter:03d}", "Data Source", True,
                             "No external file/object-store source configured — source check not applicable",
                             "", "", target_db)
        elif source_type in ("azure_blob", "s3", "sftp", "local"):
            labels = {
                "azure_blob": f"Azure Blob Container ({source_id})",
                "s3": f"S3 Bucket ({source_id})",
                "sftp": f"SFTP Host ({source_id})",
                "local": f"Local Source Path ({source_id})",
            }
            passed, msg = self._check_source(source_id)
            self._add_result(f"RES_{self.test_counter:03d}", labels[source_type], passed, msg,
                             f"Source '{source_id}' not accessible",
                             f"Verify source '{source_id}' exists and credentials are correct", target_db)
        else:
            self._add_result(f"RES_{self.test_counter:03d}", "Data Source", False,
                             "No data source configured", "Data source not found",
                             "Configure blob_storage, s3, sftp, or local source in config", target_db)


        # MAIN CHECK 3 & 4: Snowflake Database + Schema (consolidated into one test)
        self.test_counter += 1
        passed_db = self._check_snowflake_database(target_db)
        passed_schema = self._check_snowflake_schema(target_db, target_schema)
        passed_both = passed_db and passed_schema
        
        db_status = "exists" if passed_db else "missing"
        schema_status = "exists" if passed_schema else "missing"
        finding = f"db: {target_db} ({db_status}) | schema: {target_schema} ({schema_status})"
        
        self._add_result(f"RES_{self.test_counter:03d}", "Database & Schema", passed_both,
                         finding,
                         finding,
                         f"Create database '{target_db}' and schema '{target_schema}'" if not passed_both else "N/A",
                         target_db)

        # OPTIONAL: Source Files — only check if there's an actual source configured
        self.test_counter += 1
        if source_type == "unknown":
            self._add_result(f"RES_{self.test_counter:03d}", "Source File(s) Present", True,
                             "No external file source configured — source file check not applicable",
                             "", "", target_db)
        else:
            passed_files, file_list = self._check_source_files(source_type)
            file_names = ", ".join(file_list[:10]) if file_list else "none"
            finding = f"Source: {source_id} | files: {len(file_list)} | names: {file_names}"
            self._add_result(f"RES_{self.test_counter:03d}", "Source File(s) Present", passed_files,
                             finding, "No source files found",
                             "Ensure source files are available before running ETL", target_db)

        # OPTIONAL: Target Tables — consolidate into ONE test with pipe-delimited findings
        table_results = {}
        for t in tables:
            table_name = t.get("name", "unknown")
            exists = self._check_snowflake_table(target_db, target_schema, table_name)
            table_results[table_name] = exists
        
        # Create single consolidated test for all tables
        if tables:
            self.test_counter += 1
            all_exist = all(table_results.values())
            existing = ", ".join([name for name, exists in table_results.items() if exists])
            missing = ", ".join([name for name, exists in table_results.items() if not exists])
            
            if all_exist:
                finding = f"tables: {existing}"
            else:
                finding = f"tables: {existing} | missing: {missing}"
            
            self._add_result(f"RES_{self.test_counter:03d}",
                             "Target Tables", all_exist,
                             finding,
                             finding,
                             f"Create missing tables in {target_db}.{target_schema}", target_db)

    def _add_result(self, test_id, name, passed, pass_msg, fail_msg, rec, target_db):
        self.test_results.append(TestResult(
            test_id=test_id, test_name=name, phase="Resource Validation",
            status="PASS" if passed else "FAIL",
            finding=pass_msg if passed else fail_msg,
            recommendation="N/A" if passed else rec,
            check_type="Resource",
            severity="Info" if passed else "Critical",
            database_target=target_db,
            stage2_relevance="Critical",
        ))

    def _generate_skipped_results(self):
        target_db = (self.metadata.get("target_database") or "N/A").upper()
        target_schema = (self.metadata.get("target_schema") or "").upper()

        if self._has_llm():
            self._llm_checks = self._discover_checks_via_llm()

        if self._llm_checks:
            for chk in self._llm_checks:
                self.test_counter += 1
                self.test_results.append(TestResult(
                    test_id=f"RES_{self.test_counter:03d}",
                    test_name=chk.get("NAME", "Resource Check"),
                    phase="Resource Validation",
                    status="SKIPPED",
                    finding="Resource validation disabled — pass --validate-resources to run",
                    recommendation="Run with --validate-resources flag",
                    check_type="Skipped",
                    severity="Info",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
        else:
            source_id = self._find_source_identifier()
            source_type = self._detect_source_type()
            kv_name = self._find_kv_name() or "N/A"
            tables = self._resolve_tables_from_config()
            source_label = {
                "azure_blob": f"Azure Blob Container ({source_id})",
                "s3": f"S3 Bucket ({source_id})",
                "sftp": f"SFTP Host ({source_id})",
                "local": f"Local Source Path ({source_id})",
            }.get(source_type, f"Source ({source_id})")

            for name in [
                f"Key Vault ({kv_name})",
                source_label,
                f"Snowflake Database ({target_db})",
                f"Snowflake Schema ({target_schema})",
                "Source File(s) Present",
            ]:
                self.test_counter += 1
                self.test_results.append(TestResult(
                    test_id=f"RES_{self.test_counter:03d}", test_name=name, phase="Resource Validation",
                    status="SKIPPED",
                    finding="Resource validation disabled — pass --validate-resources to run",
                    recommendation="Run with --validate-resources flag",
                    check_type="Skipped", severity="Info",
                    database_target=target_db, stage2_relevance="Critical",
                ))
            for t in tables:
                table_name = t.get("name", "unknown")
                self.test_counter += 1
                self.test_results.append(TestResult(
                    test_id=f"RES_{self.test_counter:03d}",
                    test_name=f"Table Exists: {table_name}",
                    phase="Resource Validation", status="SKIPPED",
                    finding="Resource validation disabled — pass --validate-resources to run",
                    recommendation="Run with --validate-resources flag",
                    check_type="Skipped", severity="Info",
                    database_target=target_db, stage2_relevance="Critical",
                ))

    def _initialize_clients(self):
        try:
            from azure.storage.blob import BlobServiceClient
            connection_string = Config.AZURE_CONNECTION_STRING
            if connection_string:
                self.azure_client = BlobServiceClient.from_connection_string(connection_string)
            else:
                storage_account = (self.etl_config.get("blob_storage") or {}).get("storage_account", "")
                if storage_account:
                    self.azure_client = BlobServiceClient(
                        account_url=f"https://{storage_account}.blob.core.windows.net",
                        credential=self._get_azure_credential(),
                    )
        except Exception:
            pass
        # Try Key Vault private key first, fall back to env credentials via utils
        try:
            from utils.snowflake_connector import get_snowflake_cursor, get_snowflake_cursor_env
            self.snowflake_cursor = get_snowflake_cursor(self.etl_config)
        except Exception:
            pass
        if not self.snowflake_cursor:
            from utils.snowflake_connector import get_snowflake_cursor_env
            self.snowflake_cursor = get_snowflake_cursor_env(self.etl_config)
            if self.snowflake_cursor:
                print("  [Phase 4] Snowflake: connected via env fallback")

    def _check_secrets(self, target: str) -> bool:
        """Check Key Vault reachability via HTTP — 401 means vault exists, connection error means it doesn't.
        If target looks like a plain KV name (no dots, no slashes) treat it as a vault name.
        If it looks like a secret name (hyphens only, no dots) skip the HTTP check — secret names
        cannot be validated without credentials and the vault reachability is checked separately.
        """
        if not target:
            return False
        # Plain secret name pattern: lowercase/digits/hyphens, no dots — e.g. 'snowflake-private-key'
        # These are secret names inside a vault, not vault hostnames — skip HTTP check, return True
        if re.match(r'^[a-z0-9][a-z0-9\-]+[a-z0-9]$', target.lower()) and '-' in target and '.' not in target and '/' not in target:
            return True
        vault_uri = target if target.startswith("http") else f"https://{target}.vault.azure.net/"
        try:
            import urllib.request
            req = urllib.request.Request(vault_uri, method="GET")
            try:
                urllib.request.urlopen(req, timeout=5)
                return True
            except urllib.error.HTTPError as e:
                # 401/403 = vault exists but we lack permission — that's fine, vault is reachable
                return e.code in (401, 403, 404)
            except urllib.error.URLError:
                return False
        except Exception:
            return False

    def _check_source(self, target: str):
        if not target or target == "N/A":
            return False, "No source identifier found"
        if self.azure_client:
            try:
                exists = self.azure_client.get_container_client(target).exists()
                return exists, f"Container '{target}' {'exists' if exists else 'NOT found'}"
            except Exception:
                pass
        if re.search(r's3://|\\.s3\\.', target, re.I):
            try:
                import boto3
                s3 = boto3.client("s3")
                s3.head_bucket(Bucket=target.replace("s3://", "").split("/")[0])
                return True, f"S3 bucket '{target}' accessible"
            except Exception:
                return False, f"S3 bucket '{target}' not accessible"
        if re.search(r'sftp|ftp', target, re.I):
            try:
                import socket
                with socket.create_connection((target, 22), timeout=5):
                    return True, f"SFTP host '{target}' reachable"
            except Exception:
                return False, f"SFTP host '{target}' not reachable"
        if "/" in target or "\\" in target:
            exists = Path(target).exists()
            return exists, f"Local path '{target}' {'exists' if exists else 'NOT found'}"
        return False, f"Source '{target}' could not be validated — no client available"

    def _check_source_files(self, source_type: str) -> tuple:
        """Returns (bool, list_of_files)"""
        if source_type == "azure_blob" and self.azure_client:
            try:
                container = self.metadata.get("azure_container")
                if not container:
                    return False, []
                files = list(self.azure_client.get_container_client(container).list_blobs())
                file_names = [f.name for f in files]
                return bool(file_names), file_names
            except Exception:
                return False, []
        if source_type == "s3":
            try:
                import boto3
                bucket = self._find_source_identifier()
                resp = boto3.client("s3").list_objects_v2(Bucket=bucket)
                files = [obj["Key"] for obj in resp.get("Contents", [])]
                return bool(files), files
            except Exception:
                return False, []
        if source_type == "local":
            import os
            path = self._find_source_identifier()
            try:
                files = os.listdir(path)
                return len(files) > 0, files
            except Exception:
                return False, []
        return False, []

    def _check_snowflake_database(self, db_name: str) -> bool:
        if not self.snowflake_cursor:
            return False
        try:
            self.snowflake_cursor.execute(
                f"SELECT DATABASE_NAME FROM INFORMATION_SCHEMA.DATABASES "
                f"WHERE UPPER(DATABASE_NAME) = '{db_name.upper()}'"
            )
            return self.snowflake_cursor.fetchone() is not None
        except Exception:
            return False

    def _check_snowflake_schema(self, db_name: str, schema_name: str) -> bool:
        if not self.snowflake_cursor:
            return False
        try:
            self.snowflake_cursor.execute(
                f"SELECT SCHEMA_NAME FROM {db_name.upper()}.INFORMATION_SCHEMA.SCHEMATA "
                f"WHERE UPPER(SCHEMA_NAME) = '{schema_name.upper()}'"
            )
            return self.snowflake_cursor.fetchone() is not None
        except Exception:
            return False

    def _check_snowflake_table(self, db_name: str, schema_name: str, table_name: str) -> bool:
        if not self.snowflake_cursor:
            return False
        try:
            self.snowflake_cursor.execute(
                f"SELECT TABLE_NAME FROM {db_name.upper()}.INFORMATION_SCHEMA.TABLES "
                f"WHERE TABLE_SCHEMA = '{schema_name.upper()}' "
                f"AND UPPER(TABLE_NAME) = '{table_name.upper()}'"
            )
            return self.snowflake_cursor.fetchone() is not None
        except Exception:
            return False

    def _check_snowflake_object(self, target: str) -> bool:
        if not self.snowflake_cursor or not target:
            return False
        # Strip @ prefix if present
        target = target.lstrip('@')
        # User check — connection already proves user is valid, skip SHOW USERS (requires ACCOUNTADMIN on some accounts)
        if re.match(r'^[A-Z0-9_]+$', target.upper()) and len(target) < 30:
            # Try SHOW USERS first, fall through to other checks if it fails
            try:
                self.snowflake_cursor.execute(f"SHOW USERS LIKE '{target.upper()}'")
                if self.snowflake_cursor.fetchone():
                    return True
            except Exception:
                pass
        for show_cmd in [
            f"SHOW STAGES LIKE '{target.upper()}'",
            f"SHOW FILE FORMATS LIKE '{target.upper()}'",
            f"SHOW INTEGRATIONS LIKE '{target.upper()}'",
            f"SHOW PROCEDURES LIKE '{target.upper()}'",
            f"SHOW WAREHOUSES LIKE '{target.upper()}'",
            f"SHOW ROLES LIKE '{target.upper()}'",
            f"SHOW SHARES LIKE '{target.upper()}'",
        ]:
            try:
                self.snowflake_cursor.execute(show_cmd)
                if self.snowflake_cursor.fetchone():
                    return True
            except Exception:
                pass
        return False

    def _check_generic(self, target: str):
        if not target:
            return False, "No target specified"
        if Path(target).exists():
            return True, f"'{target}' exists locally"
        return False, f"'{target}' could not be validated"

    def _find_snowflake_block(self, cfg: dict) -> dict:
        for k, v in cfg.items():
            if "snowflake" in k.lower():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v[0]
                if isinstance(v, dict):
                    return v
        return {}

    def _resolve_tables_from_config(self) -> List[Dict]:
        sf_block = self._find_snowflake_block(self.etl_config)
        if sf_block:
            tables = sf_block.get("tables", [])
            if isinstance(tables, list):
                valid = [t for t in tables if isinstance(t, dict) and t.get("name")]
                if valid:
                    return valid[:10]
            tables_to_copy = sf_block.get("tables_to_copy", [])
            if isinstance(tables_to_copy, list) and tables_to_copy:
                if self.etl_file_path:
                    try:
                        etl_dir = Path(self.etl_file_path).parent
                        table_names = []
                        for ddl_ref in tables_to_copy:
                            ddl_file = ddl_ref.get("filename") if isinstance(ddl_ref, dict) else str(ddl_ref)
                            ddl_path = etl_dir / ddl_file
                            if ddl_path.exists():
                                ddl_content = ddl_path.read_text(encoding="utf-8", errors="ignore")
                                raw_matches = re.findall(r'CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_${}.]+)', ddl_content, re.IGNORECASE)
                                for rm in raw_matches:
                                    name = rm.split('.')[-1].strip('{}')
                                    if name:
                                        table_names.append(name)
                        if table_names:
                            return [{"name": name} for name in list(dict.fromkeys(table_names))]
                    except Exception:
                        pass
                result = []
                for t in tables_to_copy:
                    name = (t.get("filename") or t.get("name")) if isinstance(t, dict) else str(t)
                    if name:
                        result.append({"name": name})
                if result:
                    return result[:10]
            flat = []
            for k, v in sf_block.items():
                if isinstance(v, str) and any(k.lower().endswith(s) for s in ("_target_table", "_table", "tablename", "table_name")):
                    if k.lower() not in ("database", "schema", "warehouse", "account", "user", "role"):
                        flat.append({"name": v})
            if flat:
                return flat[:10]
        fallback = []
        if self.metadata.get("target_table"):
            fallback.append({"name": self.metadata["target_table"]})
        if self.metadata.get("audit_table"):
            fallback.append({"name": self.metadata["audit_table"]})
        return fallback

    def _detect_source_type(self) -> str:
        source_map = {
            "blob_storage": "azure_blob", "azure": "azure_blob",
            "adls": "adls", "datalake": "adls",
            "s3": "s3",
            "sftp": "sftp", "ftp": "sftp",
            "gcs": "gcs", "google": "gcs",
            "api": "api", "http": "api",
            "local": "local", "file": "local",
        }
        for key in self.etl_config:
            for k_pattern, source in source_map.items():
                if k_pattern in key.lower():
                    return source
        # Only fall back to azure_blob if the container name is NOT a Snowflake stage or key vault
        container = self.metadata.get("azure_container") or ""
        if container:
            # Don't treat key vault names as containers
            if any(kw in container.lower() for kw in ['key', 'vault', 'secret', 'kv']):
                return "unknown"
            sf_block = self._find_snowflake_block(self.etl_config)
            sf_stage = (sf_block.get("stage") or "").upper()
            if sf_stage and container.upper() == sf_stage:
                return "unknown"  # it's a Snowflake stage, not a blob container
            # Only treat as azure_blob if there's explicit blob_storage config
            if "blob_storage" in self.etl_config or "azure" in self.etl_config:
                return "azure_blob"
        return "unknown"

    def _find_source_identifier(self) -> str:
        for section_key in ["blob_storage", "adls", "s3", "gcs", "sftp", "ftp", "source", "storage", "api"]:
            section = self.etl_config.get(section_key)
            if isinstance(section, dict):
                for ck in ["container_name", "bucket", "bucket_name", "path", "host", "endpoint", "directory", "folder"]:
                    if section.get(ck):
                        return str(section[ck])
        # Only use azure_container from metadata if it's not a Snowflake stage
        container = self.metadata.get("azure_container") or ""
        if container:
            sf_block = self._find_snowflake_block(self.etl_config)
            sf_stage = (sf_block.get("stage") or "").upper()
            if sf_stage and container.upper() == sf_stage:
                return "N/A"
            return container
        return "N/A"

    def _find_kv_name(self) -> str:
        kv_key_patterns = ["key_vault", "keyvault", "vault", "secret_manager", "secrets_manager",
                           "aws_secrets", "gcp_secret", "hashicorp_vault"]
        for k, v in self.etl_config.items():
            if any(kw in k.lower() for kw in kv_key_patterns):
                return str(v)
        return ""

    def _get_azure_credential(self):
        from azure.identity import ClientSecretCredential, DefaultAzureCredential
        t, c, s = Config.AZURE_TENANT_ID, Config.AZURE_CLIENT_ID, Config.AZURE_CLIENT_SECRET
        if t and c and s:
            return ClientSecretCredential(t, c, s)
        return DefaultAzureCredential()
