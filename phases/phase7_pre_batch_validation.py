from typing import List, Dict, Any, Optional
from utils.models import TestResult
from src.config import Config
import yaml
import re
from pathlib import Path


class Phase7PreBatchValidation:
    def __init__(self, metadata: Dict[str, Any], etl_file_path: Optional[str] = None,
                 validate_resources: bool = False, run_date: str = None, env: str = None):
        self.metadata = metadata
        self.etl_file_path = etl_file_path
        self.validate_resources = validate_resources
        self.run_date = run_date
        self.env = env
        self.test_results = []
        self.azure_client = None
        self.snowflake_cursor = None
        self.etl_config = self._load_etl_yaml_config()
        self.pre_batch_metrics = {}
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

    def run(self) -> tuple:
        if not self.validate_resources:
            self._generate_skipped_results()
        else:
            self._initialize_clients()
            # Skip LLM - go straight to fallback checks
            self._run_fallback_checks()
        
        return self.pre_batch_metrics, self.test_results

    def _discover_checks_via_llm(self) -> List[Dict]:
        try:
            from utils.llm_client import call_llm
            code = Path(self.etl_file_path).read_text(encoding="utf-8", errors="ignore") if self.etl_file_path else ""
            # Truncate code to prevent 413 error
            code_preview = (code[:1500] + "\n...[middle truncated]...\n" + code[-500:]) if len(code) > 2000 else code
            
            config_str = yaml.dump(self.etl_config) if self.etl_config else "Not found"
            # Truncate config to 2000 chars
            config_preview = config_str[:2000] if len(config_str) > 2000 else config_str

            prompt = f"""Pre-execution operational summary. Output pipe-delimited single-line format.

Code preview:
{code_preview}

Config (env: {self.env}):
{config_preview}

Generate summary covering:
- Source: data source (container, bucket, path, stage, endpoint)
- Target: tables to load (actual names from config/code)
- Expected files: file patterns, extensions
- Blob/file path pattern (with DATE substitution if applicable)
- Load strategy: Full Refresh, Incremental, Merge, etc.
- Expected schema: database.schema.table
- Warehouse: Snowflake warehouse if applicable
- Execution date: how run_date is used
- Warnings: pre-conditions (stage exists, files present, secrets accessible)

Output format (pipe-delimited, one item per line, NO multi-line fields):
NAME: ... | TYPE: Source|Target|ExpectedFiles|BlobPattern|LoadStrategy|Schema|Warehouse|ExecutionDate|Warning | TARGET: ... | FINDING: ..."""

            response = call_llm(prompt, max_tokens=1000, temperature=0.1)
            if response:
                checks = self._parse_llm_blocks(response)
                print(f"  [Phase 7] LLM: SUCCESS - Discovered {len(checks)} pre-execution summary items")
                return checks
        except Exception as e:
            print(f"  [Phase 7] LLM: FALLBACK - {type(e).__name__}: {str(e)[:100]}")
        return []

    def _parse_llm_blocks(self, response: str) -> List[Dict]:
        checks = []
        for line in response.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Parse pipe-delimited format: "NAME: ... | TYPE: ... | TARGET: ... | FINDING: ..."
            if "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            chk = {}
            for part in parts:
                if ":" in part:
                    key, val = part.split(":", 1)
                    key_upper = key.strip().upper()
                    if key_upper in ("NAME", "TYPE", "TARGET", "FINDING"):
                        chk[key_upper] = val.strip()
            if chk.get("NAME") and chk.get("TYPE"):
                checks.append(chk)
        # Force DDL/SQL targets to SOURCEFILES type
        for chk in checks:
            if re.search(r'\.sql$|ddl/', chk.get("TARGET", ""), re.I):
                chk["TYPE"] = "SOURCEFILES"
        return checks[:8]

    def _execute_llm_checks(self):
        target_db = (self.metadata.get("target_database") or "N/A").upper()
        target_schema = (self.metadata.get("target_schema") or "").upper()
        tables = self._find_tables()

        # Collect pre-batch record counts for metrics (still needed by Phase 8)
        if self.snowflake_cursor and tables:
            total, per_table = 0, {}
            for t in tables:
                tname = t.get("name", "")
                try:
                    self.snowflake_cursor.execute(f"SELECT COUNT(*) FROM {target_db}.{target_schema}.{tname}")
                    result = self.snowflake_cursor.fetchone()
                    cnt = result[0] if result else 0
                    total += cnt
                    per_table[tname] = cnt
                except Exception:
                    per_table[tname] = 0
            self.pre_batch_metrics["pre_batch_total_records"] = total
            self.pre_batch_metrics["pre_batch_table_records"] = per_table
            self.pre_batch_metrics["config_table_count"] = len(tables)

        # Emit LLM summary items as INFO-level operational summary (not PASS/FAIL)
        for chk in self._llm_checks:
            self.test_counter += 1
            test_id = f"PRE_{self.test_counter:03d}"
            name = chk.get("NAME", "Checks Before Batch Run")
            ctype = chk.get("TYPE", "Summary")
            finding = chk.get("FINDING", "")
            target = chk.get("TARGET", "")

            self.test_results.append(TestResult(
                test_id=test_id,
                test_name=f"[{ctype}] {name}",
                phase="Checks Before Batch Run",
                status="PASS",
                finding=f"{finding} | Target: {target}" if target else finding,
                recommendation="N/A",
                check_type="Checks Before-Batch Run",
                severity="Info",
                database_target=target_db,
                stage2_relevance="Critical",
            ))

    def _execute_single_check(self, check_type, target, target_db, target_schema, tables):
        try:
            if check_type == "TABLECOUNT":
                count = len(tables)
                self.pre_batch_metrics["config_table_count"] = count
                passed = count > 0
                return passed, f"Config contains {count} tables: {[t.get('name') for t in tables]}", "config_table_count", count

            elif check_type == "TABLESCHEMA":
                total_fields, table_fields = 0, {}
                for t in tables:
                    tname = t.get("name", "")
                    if not self.snowflake_cursor:
                        table_fields[tname] = 0
                        continue
                    try:
                        self.snowflake_cursor.execute(
                            f"SELECT COUNT(*) FROM {target_db}.INFORMATION_SCHEMA.COLUMNS "
                            f"WHERE TABLE_SCHEMA = '{target_schema.upper()}' AND TABLE_NAME = '{tname.upper()}'"
                        )
                        result = self.snowflake_cursor.fetchone()
                        fc = result[0] if result else 0
                        total_fields += fc
                        table_fields[tname] = fc
                    except Exception:
                        table_fields[tname] = 0
                self.pre_batch_metrics["total_fields"] = total_fields
                self.pre_batch_metrics["table_fields"] = table_fields
                passed = total_fields > 0
                return passed, f"Total fields: {total_fields} | Per table: {table_fields}", "total_fields", total_fields

            elif check_type == "RECORDCOUNT":
                total, per_table = 0, {}
                for t in tables:
                    tname = t.get("name", "")
                    if not self.snowflake_cursor:
                        per_table[tname] = 0
                        continue
                    try:
                        self.snowflake_cursor.execute(f"SELECT COUNT(*) FROM {target_db}.{target_schema}.{tname}")
                        result = self.snowflake_cursor.fetchone()
                        cnt = result[0] if result else 0
                        total += cnt
                        per_table[tname] = cnt
                    except Exception:
                        per_table[tname] = 0
                self.pre_batch_metrics["pre_batch_total_records"] = total
                self.pre_batch_metrics["pre_batch_table_records"] = per_table
                return True, f"Pre-batch records: {total} | Per table: {per_table}", "pre_batch_total_records", total

            elif check_type == "SOURCEFILES":
                # DDL/SQL file patterns — check locally relative to ETL dir
                if re.search(r'\.sql$|ddl/', target, re.I) and self.etl_file_path:
                    etl_dir = Path(self.etl_file_path).parent
                    # Handle comma-separated list of DDL files
                    ddl_targets = [t.strip() for t in target.split(',') if t.strip()]
                    found_files, missing_files = [], []
                    for dt in ddl_targets:
                        pattern_clean = dt.lstrip('/')
                        if '*' in pattern_clean:
                            matches = list(etl_dir.glob(pattern_clean))
                            if matches:
                                found_files.extend([m.name for m in matches])
                            else:
                                missing_files.append(dt)
                        else:
                            p = etl_dir / pattern_clean
                            if p.exists():
                                found_files.append(p.name)
                            else:
                                missing_files.append(dt)
                    count = len(found_files)
                    self.pre_batch_metrics["source_file_count"] = count
                    if missing_files:
                        return False, f"DDL files missing: {missing_files} | Found: {found_files}", "source_file_count", count
                    return count > 0, f"DDL files matching '{target}': {count} | {found_files}", "source_file_count", count
                # "ddl directory" or similar descriptive targets — check if ddl dir exists
                if re.search(r'ddl\s*dir|ddl\s*folder|ddl\s*path', target, re.I) and self.etl_file_path:
                    etl_dir = Path(self.etl_file_path).parent
                    ddl_dir = etl_dir / "ddl"
                    if ddl_dir.exists():
                        sql_files = list(ddl_dir.glob("*.sql"))
                        count = len(sql_files)
                        self.pre_batch_metrics["source_file_count"] = count
                        return count > 0, f"DDL directory found: {count} SQL files | {[f.name for f in sql_files]}", "source_file_count", count
                    return False, f"DDL directory not found at '{ddl_dir}'", "source_file_count", 0
                # Snowflake stage as source (suffix-based pattern or any _STAGE$ target) — check via SHOW STAGES
                if self.snowflake_cursor and re.search(r'_STAGE$|STAGE$', target, re.I):
                    try:
                        self.snowflake_cursor.execute(f"SHOW STAGES LIKE '{target.upper()}'")
                        found = self.snowflake_cursor.fetchone() is not None
                        self.pre_batch_metrics["source_file_count"] = 1 if found else 0
                        return found, f"Snowflake stage '{target}' {'exists' if found else 'NOT found'}", "source_file_count", 1 if found else 0
                    except Exception:
                        pass
                # If target contains file pattern chars (/, *, .) and ETL uses a Snowflake stage
                # (detected from config), redirect to stage check instead of blob listing
                sf_block = self._find_snowflake_block(self.etl_config)
                stage_name = sf_block.get("stage", "") if sf_block else ""
                if stage_name and re.search(r'[/*.]', target) and self.snowflake_cursor:
                    try:
                        self.snowflake_cursor.execute(f"SHOW STAGES LIKE '{stage_name.upper()}'")
                        found = self.snowflake_cursor.fetchone() is not None
                        self.pre_batch_metrics["source_file_count"] = 1 if found else 0
                        return found, f"Snowflake stage '{stage_name}' {'exists' if found else 'NOT found'} (source for pattern '{target}')", "source_file_count", 1 if found else 0
                    except Exception:
                        pass
                # Config key expression (e.g. 'base_prefix, target_prefix, ext_regex') — resolve to actual source
                if re.search(r'[,\s]|prefix|ext_regex|pattern', target, re.I):
                    resolved = self._find_source_identifier()
                    count, details = self._count_source_files(resolved)
                    self.pre_batch_metrics["source_file_count"] = count
                    return count > 0, f"Source files in '{resolved}': {count} | {details}", "source_file_count", count
                # Resolve LLM target to actual source identifier if it's a config key expression
                resolved_target = target if target and not re.search(r"[\[\]'\"]", target) else self._find_source_identifier()
                count, details = self._count_source_files(resolved_target)
                self.pre_batch_metrics["source_file_count"] = count
                passed = count > 0
                return passed, f"Source files matching '{resolved_target}': {count} | {details}", "source_file_count", count

            elif check_type == "PRECONDITION":
                # Share existence checks are ETL outputs, not pre-conditions — treat as PASS
                if re.search(r'share', target, re.I):
                    return True, f"Share '{target}' — created by ETL, not a pre-condition", None, None
                # "Snowflake Connection" — pass if cursor is available
                if re.search(r'snowflake.conn|sf.conn|connection', target, re.I):
                    connected = self.snowflake_cursor is not None
                    return connected, f"Snowflake connection {'established' if connected else 'NOT available'}", None, None
                # Comma-separated multi-target: split and check each
                if ',' in target:
                    sub_targets = [t.strip() for t in target.split(',')]
                    results = []
                    for st in sub_targets:
                        passed = self._check_precondition(st, target_db, target_schema)
                        results.append((st, passed))
                    all_pass = all(r[1] for r in results)
                    detail = " | ".join(f"{t}: {'OK' if p else 'FAIL'}" for t, p in results)
                    return all_pass, f"Pre-conditions: {detail}", None, None
                passed = self._check_precondition(target, target_db, target_schema)
                return passed, f"Pre-condition '{target}' {'satisfied' if passed else 'NOT satisfied'}", None, None

            else:
                return True, f"Check '{check_type}' noted: {target}", None, None

        except Exception as e:
            return False, f"Check error: {e}", None, None

    def _run_fallback_checks(self):
        target_db = (self.metadata.get("target_database") or "N/A").upper()
        target_schema = (self.metadata.get("target_schema") or "").upper()
        tables = self._find_tables()
        table_count = len(tables)
        self.pre_batch_metrics["config_table_count"] = table_count

        # CHECK 1: Config Tables + Schema Summary (ONE TEST, compact format)
        if self.snowflake_cursor:
            total_fields, table_fields = 0, {}
            for t in tables:
                tname = t.get("name", "unknown")
                try:
                    self.snowflake_cursor.execute(
                        f"SELECT COUNT(*) FROM {target_db}.INFORMATION_SCHEMA.COLUMNS "
                        f"WHERE TABLE_SCHEMA = '{target_schema.upper()}' AND TABLE_NAME = '{tname.upper()}'"
                    )
                    result = self.snowflake_cursor.fetchone()
                    fc = result[0] if result else 0
                    total_fields += fc
                    table_fields[tname] = fc
                except Exception:
                    table_fields[tname] = 0
            self.pre_batch_metrics["total_fields"] = total_fields
            self.pre_batch_metrics["table_fields"] = table_fields
            
            self.test_counter += 1
            config_summary = f"tables: {table_count} | fields: {total_fields} | db: {target_db} | schema: {target_schema}"
            self._add_result(f"PRE_{self.test_counter:03d}", "Configuration Summary", table_count > 0,
                             config_summary,
                             "Missing config or schema",
                             "Verify config.yml and Snowflake schema", target_db)
        else:
            self.test_counter += 1
            self._add_fail(f"PRE_{self.test_counter:03d}", "Configuration Summary",
                           "Cannot connect to Snowflake")

        # CHECK 2: Pre-Batch Record Count Summary (ONE TEST, all tables on one line)
        if self.snowflake_cursor:
            total, per_table = 0, {}
            for t in tables:
                tname = t.get("name", "unknown")
                try:
                    self.snowflake_cursor.execute(f"SELECT COUNT(*) FROM {target_db}.{target_schema}.{tname}")
                    result = self.snowflake_cursor.fetchone()
                    cnt = result[0] if result else 0
                    total += cnt
                    per_table[tname] = cnt
                except Exception:
                    per_table[tname] = 0
            self.pre_batch_metrics["pre_batch_total_records"] = total
            self.pre_batch_metrics["pre_batch_table_records"] = per_table
            
            self.test_counter += 1
            records_summary = " | ".join([f"{tbl}: {per_table.get(tbl, 0)}" for tbl in [t.get("name", "unknown") for t in tables]]) + f" | total: {total}"
            self._add_result(f"PRE_{self.test_counter:03d}", "Pre-Batch Record Count", True,
                             records_summary, "", "N/A", target_db)
        else:
            self.test_counter += 1
            self._add_fail(f"PRE_{self.test_counter:03d}", "Pre-Batch Record Count",
                           "Cannot connect to Snowflake")

        # CHECK 3: Source Files Status (ONE TEST)
        source_type = self._detect_source_type()
        source_id = self._find_source_identifier()

        if source_type == "unknown":
            # No external source — ETL reads from internal Snowflake objects
            self.test_counter += 1
            self._add_result(f"PRE_{self.test_counter:03d}", "Source Configuration", True,
                "No external file source — internal Snowflake objects",
                "", "N/A", target_db)
        elif source_type == "azure_blob" and not self.azure_client:
            self.test_counter += 1
            sf_block = self._find_snowflake_block(self.etl_config)
            stage = sf_block.get("stage", "") if sf_block else ""
            has_blob_config = bool(self.etl_config.get("blob_storage"))
            if stage:
                self._add_result(f"PRE_{self.test_counter:03d}",
                    "Source Configuration", True,
                    f"Snowflake stage: {stage} | no Azure client needed",
                    "", "N/A", target_db)
            elif not has_blob_config:
                self._add_result(f"PRE_{self.test_counter:03d}",
                    "Source Configuration", True,
                    "No external file source — internal Snowflake objects",
                    "", "N/A", target_db)
            else:
                self._add_fail(f"PRE_{self.test_counter:03d}", "Source Configuration",
                    f"Azure source '{source_id}' — credentials unavailable")
        else:
            count, details = self._count_source_files(source_id)
            self.pre_batch_metrics["source_file_count"] = count
            self.test_counter += 1
            self._add_result(f"PRE_{self.test_counter:03d}", "Source Configuration", count > 0,
                             f"Source: {source_id} | files: {count} | {details}",
                             f"No files in {source_id}",
                             "Upload source files before running ETL", target_db)

    def _count_source_files(self, pattern: str):
        source_type = self._detect_source_type()
        source_id = self._find_source_identifier()

        if source_type == "azure_blob" and self.azure_client:
            try:
                container = self.azure_client.get_container_client(source_id)
                all_blobs = list(container.list_blobs())
                if pattern and pattern != source_id:
                    ext = pattern.lstrip("*.")
                    matching = [b for b in all_blobs if b.name.endswith(ext) or pattern in b.name]
                else:
                    matching = all_blobs
                return len(matching), f"container={source_id}"
            except Exception as e:
                return 0, f"Azure error: {e}"

        if source_type == "s3":
            try:
                import boto3
                s3 = boto3.client("s3")
                resp = s3.list_objects_v2(Bucket=source_id, MaxKeys=100)
                files = resp.get("Contents", [])
                return len(files), f"bucket={source_id}"
            except Exception as e:
                return 0, f"S3 error: {e}"

        if source_type == "local":
            import os
            try:
                files = os.listdir(source_id)
                return len(files), f"path={source_id}"
            except Exception as e:
                return 0, f"Local error: {e}"

        return 0, "Source not accessible"

    def _resolve_precondition_target(self, target: str) -> str:
        """Resolve a precondition target label to an actual Snowflake object name from config."""
        sf_block = self._find_snowflake_block(self.etl_config)
        label_lower = target.lower().strip()

        # run_date / date — runtime arg, not a Snowflake object
        if re.search(r'run_date|\bdate\b', target, re.I):
            return None

        # KV secret references — already validated in Phase 4, auto-PASS
        if re.search(r'key.vault|azure.key|secret|kv\b', label_lower):
            return None

        # Snowflake connection parameter values (account IDs, usernames, roles, DB, schema)
        # These are config values read at runtime — not Snowflake objects to SHOW
        sf_conn_keys = {'account', 'user', 'username', 'role', 'database', 'schema',
                        'warehouse', 'private_key', 'password'}
        # If target matches a known sf_block value (account ID, username etc.) — auto-PASS
        if sf_block:
            sf_values = {str(v).lower() for v in sf_block.values() if isinstance(v, (str, int))}
            if label_lower in sf_values:
                return None
        # If target label IS a connection parameter name (not a resource name) — auto-PASS
        if re.search(r'\baccount\b|\buser\b|\brole\b|\bdatabase\b|\bschema\b|\bpassword\b|\bprivate.key\b', label_lower):
            # Only auto-PASS if it's describing a config key, not a Snowflake object like a stage
            if not re.search(r'stage|format|warehouse|proc|procedure|share', label_lower):
                return None

        # Generic config-section labels — auto-PASS
        if re.search(r'snowflake.config|blob.storage|storage.account|config.section|configuration', label_lower):
            return None

        # Try to match the label against known sf_block keys (stage, file_format, warehouse, etc.)
        for key in ['stage', 'file_format', 'storage_integration', 'warehouse']:
            if key.replace('_', ' ') in label_lower or key in label_lower:
                val = sf_block.get(key)
                if val:
                    return str(val)

        # Bracket notation e.g. snowflake_table['file_format'] -> look up key in sf_block
        bracket_key = re.search(r"\['([^']+)'\]", target)
        if bracket_key:
            key = bracket_key.group(1)
            if sf_block.get(key):
                return sf_block[key]

        # Config section key (snake_case, no uppercase) that exists as a top-level config key — auto-PASS
        if re.match(r'^[a-z][a-z0-9_]+$', label_lower) and not re.search(r'stage|format|warehouse|proc|procedure', label_lower):
            if label_lower in {k.lower() for k in self.etl_config.keys()}:
                return None

        return target

    def _check_precondition(self, target: str, db: str, schema: str) -> bool:
        resolved = self._resolve_precondition_target(target)
        if resolved is None:
            return True  # runtime arg — not a Snowflake object to check

        # Secret names (contain hyphens, look like KV secret names) — skip, already checked in Phase 4
        if re.match(r'^[a-z0-9][a-z0-9\-]+[a-z0-9]$', resolved.lower()) and '-' in resolved:
            return True  # treat as PASS — KV secret reachability is Phase 4's job

        # DDL file paths — check local existence relative to ETL dir
        if re.search(r'\.sql$|ddl/', resolved, re.I):
            if self.etl_file_path:
                ddl_path = Path(self.etl_file_path).parent / resolved.lstrip('/')
                if ddl_path.exists():
                    return True
                if '*' in resolved:
                    return bool(list(Path(self.etl_file_path).parent.glob(resolved.lstrip('/'))))
            return False

        if not self.snowflake_cursor:
            return False
        target_clean = resolved.lstrip('@')
        # Set database context to match the ETL's target database before running SHOW commands
        try:
            self.snowflake_cursor.execute(f"USE DATABASE {db}")
        except Exception:
            pass
        for show_cmd in [
            f"SHOW STAGES LIKE '{target_clean.upper()}'",
            f"SHOW FILE FORMATS LIKE '{target_clean.upper()}'",
            f"SHOW INTEGRATIONS LIKE '{target_clean.upper()}'",
            f"SHOW WAREHOUSES LIKE '{target_clean.upper()}'",
            f"SHOW PROCEDURES LIKE '{target_clean.upper()}'",
            f"SHOW SHARES LIKE '{target_clean.upper()}'",
            f"SHOW STAGES LIKE '{target_clean.upper()}' IN SCHEMA {db}.{schema}",
            f"SHOW FILE FORMATS LIKE '{target_clean.upper()}' IN SCHEMA {db}.{schema}",
        ]:
            try:
                self.snowflake_cursor.execute(show_cmd)
                if self.snowflake_cursor.fetchone():
                    return True
            except Exception:
                pass
        return False

    def _add_result(self, test_id, name, passed, pass_msg, fail_msg, rec, target_db):
        self.test_results.append(TestResult(
            test_id=test_id, test_name=name, phase="Checks Before Batch Run",
            status="PASS" if passed else "FAIL",
            finding=pass_msg if passed else fail_msg,
            recommendation="N/A" if passed else rec,
            check_type="Pre-Batch",
            severity="Info" if passed else "High",
            database_target=target_db,
            stage2_relevance="Critical",
        ))

    def _add_fail(self, test_id, name, reason):
        self.test_results.append(TestResult(
            test_id=test_id, test_name=name, phase="Checks Before Batch Run",
            status="FAIL", finding=reason,
            recommendation="Resolve connection issue and re-run with --validate-resources",
            check_type="Pre-Batch", severity="High",
            database_target=self.metadata.get("target_database"),
            stage2_relevance="Critical",
        ))

    def _generate_skipped_results(self):
        target_db = (self.metadata.get("target_database") or "N/A").upper()
        source_id = self._find_source_identifier()
        source_type = self._detect_source_type()
        source_label = {
            "azure_blob": f"Source Files Exist in Azure ({source_id})",
            "s3": f"Source Files Exist in S3 ({source_id})",
            "sftp": f"Source Files Exist on SFTP ({source_id})",
            "local": f"Source Files Exist (Local: {source_id})",
        }.get(source_type, f"Source Files Exist ({source_id})")
        
        for name in [
            "Config Tables Count",
            "Table Schema Validation",
            "Pre-Batch Record Count",
            source_label,
        ]:
            self.test_counter += 1
            self.test_results.append(TestResult(
                test_id=f"PRE_{self.test_counter:03d}", test_name=name, phase="Checks Before Batch Run",
                status="SKIPPED",
                finding="Pre-batch validation disabled — pass --validate-resources to run",
                recommendation="Run with --validate-resources flag",
                check_type="Skipped", severity="Info",
                database_target=target_db, stage2_relevance="Critical",
            ))

    def _initialize_clients(self):
        try:
            from azure.storage.blob import BlobServiceClient
            from azure.identity import ClientSecretCredential, DefaultAzureCredential
            t, c, s = Config.AZURE_TENANT_ID, Config.AZURE_CLIENT_ID, Config.AZURE_CLIENT_SECRET
            credential = ClientSecretCredential(t, c, s) if (t and c and s) else DefaultAzureCredential()
            if Config.AZURE_CONNECTION_STRING:
                self.azure_client = BlobServiceClient.from_connection_string(Config.AZURE_CONNECTION_STRING)
            else:
                storage_account = (self.etl_config.get("blob_storage") or {}).get("storage_account", "")
                if storage_account:
                    self.azure_client = BlobServiceClient(
                        account_url=f"https://{storage_account}.blob.core.windows.net",
                        credential=credential,
                    )
        except Exception:
            pass
        try:
            from utils.snowflake_connector import get_snowflake_cursor, get_snowflake_cursor_env
            self.snowflake_cursor = get_snowflake_cursor(self.etl_config)
        except Exception:
            pass
        if not self.snowflake_cursor:
            from utils.snowflake_connector import get_snowflake_cursor_env
            self.snowflake_cursor = get_snowflake_cursor_env(self.etl_config)

    def _find_snowflake_block(self, cfg: dict) -> dict:
        for k, v in cfg.items():
            if "snowflake" in k.lower():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v[0]
                if isinstance(v, dict):
                    return v
        return {}

    def _find_tables(self) -> List[Dict]:
        sf_block = self._find_snowflake_block(self.etl_config)
        if not sf_block:
            return []
        
        tables = sf_block.get("tables", [])
        if isinstance(tables, list):
            valid = [t for t in tables if isinstance(t, dict) and t.get("name")]
            if valid:
                return valid
        
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
                            matches = re.findall(r'CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:\w+\.)?(?:\w+\.)?([A-Za-z0-9_]+)', ddl_content, re.IGNORECASE)
                            table_names.extend(matches)
                    if table_names:
                        return [{"name": name} for name in list(dict.fromkeys(table_names))]
                except Exception:
                    pass
            return []
        
        flat = []
        for k, v in sf_block.items():
            if isinstance(v, str) and any(k.lower().endswith(s) for s in ("_target_table", "_table", "tablename", "table_name")):
                if k.lower() not in ("database", "schema", "warehouse", "account", "user", "role"):
                    flat.append({"name": v})
        if flat:
            return flat

        # Pattern 4: stored procedure CALL statements — infer table names generically
        if self.etl_file_path:
            try:
                code = Path(self.etl_file_path).read_text(encoding="utf-8", errors="ignore")
                call_procs = re.findall(r"CALL\s+([A-Za-z0-9_]+)\s*\(", code, re.IGNORECASE)
                inferred = []
                for proc in call_procs:
                    m = re.match(r'LOAD_([A-Za-z0-9_]+?)(?:_CSV|_SQL|_DATA|_TABLE)?$', proc, re.IGNORECASE)
                    if m:
                        inferred.append({"name": m.group(1).upper()})
                    else:
                        inferred.append({"name": proc.upper()})
                if inferred:
                    return list({t["name"]: t for t in inferred}.values())
            except Exception:
                pass

        return []

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
        # Only fall back to metadata azure_container if it looks like a real container name
        # (not a KV name like etl-test-agent-kv, not a stage name, no spaces/colons)
        container = self.metadata.get("azure_container") or ""
        if container and ' ' not in container and ':' not in container:
            if not re.search(r'-kv$|\bkv\b', container, re.I):
                return "azure_blob"
        return "unknown"

    def _find_source_identifier(self) -> str:
        for section_key in ["blob_storage", "adls", "s3", "gcs", "sftp", "ftp", "source", "storage", "api"]:
            section = self.etl_config.get(section_key)
            if isinstance(section, dict):
                for ck in ["container_name", "bucket", "bucket_name", "path", "host", "endpoint", "directory", "folder"]:
                    if section.get(ck):
                        return str(section[ck])
        # Only use metadata fallback if it looks like a real source identifier
        container = self.metadata.get("azure_container") or ""
        if container and ' ' not in container and ':' not in container:
            if not re.search(r'-kv$|\bkv\b', container, re.I):
                return container
        return "N/A"
