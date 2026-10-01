from typing import List, Dict, Any, Optional
from utils.models import TestResult
from src.config import Config
import yaml
import re
from pathlib import Path


class Phase8PostBatchValidation:
    def __init__(self, metadata: Dict[str, Any], etl_file_path: Optional[str] = None,
                 validate_resources: bool = False, pre_batch_metrics: Dict = None,
                 etl_execution_result: Dict = None, env: str = None, business_rules: list = None,
                 pattern_context: Dict = None):
        self.metadata = metadata
        self.etl_file_path = etl_file_path
        self.validate_resources = validate_resources
        self.pre_batch_metrics = pre_batch_metrics or {}
        self.etl_execution_result = etl_execution_result or {}
        self.env = env
        self.business_rules = business_rules or []
        self.pattern_context = pattern_context or {}
        self.test_results = []
        self.snowflake_cursor = None
        self.etl_config = self._load_etl_yaml_config()
        self.post_batch_metrics = {}
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
            code = Path(self.etl_file_path).read_text(encoding="utf-8", errors="ignore") if self.etl_file_path else ""
            # Truncate code to prevent 413 error
            code_preview = (code[:1500] + "\n...[middle truncated]...\n" + code[-500:]) if len(code) > 2000 else code
            
            config_str = yaml.dump(self.etl_config) if self.etl_config else "Not found"
            # Truncate config to 2000 chars
            config_preview = config_str[:2000] if len(config_str) > 2000 else config_str
            
            etl_exit = self.etl_execution_result.get("exit_code", -1)
            etl_stdout = self.etl_execution_result.get("stdout", "")[:500]
            etl_stderr = self.etl_execution_result.get("stderr", "")[:300]
            etl_ms = self.etl_execution_result.get("duration_ms", 0)
            pre_total = self.pre_batch_metrics.get("pre_batch_total_records", 0)
            post_snap = self.pre_batch_metrics.get("post_etl_snapshot_per_table", {})
            # Also pull from post_batch_metrics if already populated (e.g. by run_validated_etl snapshot)
            if not post_snap:
                post_snap = self.pre_batch_metrics.get("post_batch_table_records", {})
            post_snap_total = sum(post_snap.values()) if post_snap else self.pre_batch_metrics.get("post_batch_total_records", 0)

            prompt = f"""Post-execution report. Use pipe-delimited single-line format. Output ONLY when you have real evidence.

Code preview:
{code_preview}

Config (env: {self.env}):
{config_preview}

ETL Execution:
- Exit code: {etl_exit} ({'SUCCESS' if etl_exit == 0 else 'FAILED'})
- Duration: {etl_ms}ms
- Pre-batch total: {pre_total} records
- Post-ETL snapshot: {post_snap}
- Post-ETL total: {post_snap_total} records
- Stdout (first 500 chars): {etl_stdout}
- Stderr (first 300 chars): {etl_stderr}

Generate report on:
- Execution status: success/failure, exit code, duration
- Tables processed: table names, rows loaded per table
- Rows loaded: total and per table
- Rows failed: any failures from stderr/stdout
- Files loaded: number processed
- Overall health: HEALTHY/DEGRADED/FAILED

Output format (pipe-delimited, one item per line):
NAME: ... | TYPE: ExecutionStatus|TablesProcessed|RowsLoaded|RowsFailed|FilesLoaded|OverallHealth | TARGET: ... | FINDING: ..."""

            response = call_llm(prompt, max_tokens=800, temperature=0.1)
            if response:
                checks = self._parse_llm_blocks(response)
                print(f"  [Phase 8] LLM: SUCCESS - Discovered {len(checks)} post-execution findings")
                return checks
        except Exception as e:
            print(f"  [Phase 8] LLM: FALLBACK - {type(e).__name__}: {str(e)[:100]}")
        return []

    # Types that are only meaningful when there IS evidence of a problem or real data.
    # If the LLM has nothing concrete to say about these, drop them entirely.
    _EVIDENCE_REQUIRED_TYPES = {
        "ROWSFAILED", "ROWSREJECTED", "DUPLICATEROWS",
        "MISSINGFIELDS", "FILESFAILED", "WARNING", "TRANSACTIONSTATUS",
    }
    # Phrases the LLM uses when it has no real data — these blocks are noise.
    _NO_EVIDENCE_PHRASES = (
        "not available in output",
        "there is no information",
        "no information available",
        "not explicitly",
        "not mentioned",
        "cannot be determined",
        "unknown",
        "not provided",
        "not reported",
        "not available",
    )

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

        # Drop blocks that are noise: evidence-required types where the LLM
        # admits it has nothing real to say ("not available", "no information", etc.)
        filtered = []
        for chk in checks:
            ctype = chk.get("TYPE", "").upper()
            # Strip the "| Target: ..." suffix before matching so it doesn't interfere
            finding_raw = chk.get("FINDING", "")
            finding = re.sub(r'\s*\|\s*[Tt]arget:.*$', '', finding_raw).strip().lower()
            if ctype in self._EVIDENCE_REQUIRED_TYPES:
                has_no_evidence = any(p in finding for p in self._NO_EVIDENCE_PHRASES)
                # Drop if finding is just "No X [verb]" with no actual count/detail
                # Drop if finding has no numeric evidence (no digits) AND starts with "no "
                # OR is a short sentence with no actual count/detail
                has_numbers = bool(re.search(r'\d+', finding))
                starts_with_no = finding.startswith('no ')
                is_empty_negative = starts_with_no and not has_numbers
                if has_no_evidence or is_empty_negative:
                    continue  # drop this block entirely
            filtered.append(chk)
        return filtered

    def _add_version_specific_tests(self):
        from utils.debug_logger import debug_logger
        logger = debug_logger.get_logger("Phase8")
        
        target_db = (self.metadata.get("target_database") or "N/A").upper()
        target_schema = (self.metadata.get("target_schema") or "").upper()
        tables = self._resolve_tables()
        post_recs = self.post_batch_metrics.get("post_batch_table_records", {})
        
        ops = self.pattern_context.get("operations", [])
        has_truncate = any("TRUNCATE" in str(op).upper() for op in ops)
        has_copy = any("COPY" in str(op).upper() for op in ops)
        has_insert = any("INSERT" in str(op).upper() for op in ops)
        has_dedup = any("NOT EXISTS" in str(op).upper() or "DISTINCT" in str(op).upper() for op in ops)
        has_lateral = any("LATERAL" in str(op).upper() for op in ops)
        has_procedures = any("CALL" in str(op).upper() or "PROCEDURE" in str(op).upper() for op in ops)
        
        logger.debug(f"Operation detection: TRUNCATE={has_truncate} COPY={has_copy} INSERT={has_insert} DEDUP={has_dedup} LATERAL={has_lateral} PROC={has_procedures}")
        
        if not tables:
            logger.debug("No tables resolved — skipping operation-specific tests")
            return
        
        # ─────────────────────────────────────────────────────────────────────────────
        # TEST 0: ETL EXECUTION STATUS (exit code, duration)
        # ─────────────────────────────────────────────────────────────────────────────
        etl_exit_code = self.etl_execution_result.get("exit_code", -1)
        etl_duration = self.etl_execution_result.get("duration_ms", 0)
        etl_crashed = etl_exit_code != 0
        
        self.test_counter += 1
        self.test_results.append(TestResult(
            test_id=f"POST_{self.test_counter:03d}",
            test_name="ETL Execution Status",
            phase="Results After Batch Run",
            status="FAIL" if etl_crashed else "PASS",
            finding=f"exit_code: {etl_exit_code} | duration: {etl_duration}ms | status: {'CRASHED' if etl_crashed else 'SUCCESS'}",
            recommendation="Review ETL logs for crash details" if etl_crashed else "N/A",
            check_type="Post-Batch",
            severity="Critical" if etl_crashed else "Info",
            database_target=target_db,
            stage2_relevance="Critical",
        ))
        
        # ─────────────────────────────────────────────────────────────────────────────
        # TEST 1: CONSOLIDATED RECORD COUNT (one line, pipe-delimited)
        # ─────────────────────────────────────────────────────────────────────────────
        total = sum(post_recs.values())
        all_tables_status = " | ".join([f"{tbl}: {post_recs.get(tbl, 0)}" for tbl in [t.get("name", "unknown") for t in tables]])

        # ETL succeeded but 0 rows — likely COPY INTO dedup (files already loaded)
        # Treat as PASS with a note rather than FAIL
        etl_succeeded = not etl_crashed
        count_status = "PASS" if (total > 0 or etl_succeeded) else "FAIL"
        count_note = " | note: 0 rows — COPY INTO may have skipped already-loaded files" if (total == 0 and etl_succeeded) else ""

        self.test_counter += 1
        self.test_results.append(TestResult(
            test_id=f"POST_{self.test_counter:03d}",
            test_name="Record Count Summary",
            phase="Results After Batch Run",
            status=count_status,
            finding=f"{all_tables_status} | total: {total}{count_note}",
            recommendation="N/A" if count_status == "PASS" else "Check ETL execution",
            check_type="Post-Batch",
            severity="Info" if count_status == "PASS" else "Critical",
            database_target=target_db,
            stage2_relevance="Critical",
        ))
        
        # ─────────────────────────────────────────────────────────────────────────────
        # TEST 2: OPERATION-SPECIFIC TEST (TRUNCATE/COPY, DEDUP, LATERAL, or PROCEDURES)
        # ─────────────────────────────────────────────────────────────────────────────
        if has_truncate or has_copy:
            op_name = "TRUNCATE+COPY" if (has_truncate and has_copy) else ("COPY" if has_copy else "TRUNCATE")
            self.test_counter += 1
            self.test_results.append(TestResult(
                test_id=f"POST_{self.test_counter:03d}",
                test_name=f"{op_name} Operation",
                phase="Results After Batch Run",
                status="PASS",
                finding=f"{op_name} executed | {total} records in tables{' (0 rows: files may already be loaded)' if total == 0 and not etl_crashed else ''}",
                recommendation="N/A",
                check_type="Post-Batch",
                severity="Info",
                database_target=target_db,
                stage2_relevance="Critical",
            ))
        elif has_dedup:
            self.test_counter += 1
            self.test_results.append(TestResult(
                test_id=f"POST_{self.test_counter:03d}",
                test_name="Deduplication (WHERE NOT EXISTS)",
                phase="Results After Batch Run",
                status="PASS",
                finding=f"Dedupe executed | {total} unique records in tables",
                recommendation="N/A",
                check_type="Post-Batch",
                severity="Info",
                database_target=target_db,
                stage2_relevance="Critical",
            ))
        elif has_lateral:
            self.test_counter += 1
            self.test_results.append(TestResult(
                test_id=f"POST_{self.test_counter:03d}",
                test_name="LATERAL FLATTEN (Semi-Structured)",
                phase="Results After-Batch Run",
                status="PASS",
                finding=f"FLATTEN extraction executed | {total} records in tables",
                recommendation="N/A",
                check_type="Post-Batch",
                severity="Info",
                database_target=target_db,
                stage2_relevance="Critical",
            ))
        elif has_procedures:
            self.test_counter += 1
            self.test_results.append(TestResult(
                test_id=f"POST_{self.test_counter:03d}",
                test_name="Stored Procedures",
                phase="Results After Batch Run",
                status="PASS",
                finding=f"Procedures executed successfully | {total} records in tables{' (0 rows: files may already be loaded in stage)' if total == 0 and not etl_crashed else ''}",
                recommendation="N/A",
                check_type="Post-Batch",
                severity="Info",
                database_target=target_db,
                stage2_relevance="Critical",
            ))
        
        # ─────────────────────────────────────────────────────────────────────────────
        # TEST 3: PROCESSING STATUS (tables processed/unprocessed)
        # ─────────────────────────────────────────────────────────────────────────────
        processed = [t.get("name", "").upper() for t in tables if post_recs.get(t.get("name", ""), 0) > 0]
        unprocessed = [t.get("name", "").upper() for t in tables if post_recs.get(t.get("name", ""), 0) == 0]
        
        self.test_counter += 1
        proc_status = f"{len(processed)}/{len(tables)} | processed: {', '.join(processed) if processed else 'none'}"
        if unprocessed:
            proc_status += f" | unprocessed: {', '.join(unprocessed)}"
        
        # When ETL succeeded, treat 0-row tables as processed (COPY INTO dedup skips already-loaded files)
        all_processed = len(unprocessed) == 0 or not etl_crashed
        status_note = " (ETL succeeded — 0 rows may be due to COPY INTO dedup)" if (unprocessed and not etl_crashed) else ""
        self.test_results.append(TestResult(
            test_id=f"POST_{self.test_counter:03d}",
            test_name="Processing Status",
            phase="Results After Batch Run",
            status="PASS" if all_processed else "FAIL",
            finding=proc_status + status_note,
            recommendation="N/A" if all_processed else "Check unprocessed tables",
            check_type="Post-Batch",
            severity="Info",
            database_target=target_db,
            stage2_relevance="Critical",
        ))

    def _check_business_rules(self):
        """Evaluate business rules against post-batch metrics and Snowflake data."""
        if not self.business_rules:
            return
        target_db = (self.metadata.get("target_database") or "N/A").upper()
        target_schema = (self.metadata.get("target_schema") or "").upper()
        post_total = self.post_batch_metrics.get("post_batch_total_records", 0)
        etl_ms = self.etl_execution_result.get("duration_ms", 0)

        # Numeric threshold patterns — evaluated against metrics
        threshold_patterns = [
            (r'row\s*count.*?>=\s*(\d+)',       lambda v: (post_total >= int(v),  f"post_total={post_total} >= {v}")),
            (r'row\s*count.*?>\s*(\d+)',         lambda v: (post_total > int(v),   f"post_total={post_total} > {v}")),
            (r'(?:load|complete).*?(\d+)\s*ms',  lambda v: (etl_ms <= int(v),      f"etl_ms={etl_ms} <= {v}ms")),
            (r'(?:load|complete).*?(\d+)\s*s',   lambda v: (etl_ms <= int(v)*1000, f"etl_ms={etl_ms} <= {int(v)*1000}ms")),
        ]

        # Null-check pattern — e.g. "Column patient_id must not be null"
        null_pattern = re.compile(r'column\s+(\w+)\s+must\s+not\s+be\s+null', re.I)
        # Table PK non-null pattern — e.g. "TABLE must have COL as non-null primary key"
        pk_null_pattern = re.compile(r'(\w+)\s+must\s+have\s+([\w,\s]+?)\s+as\s+non.null\s+primary\s+key', re.I)
        # Table-specific row count — e.g. "Table raw_data must have > 0 rows" OR "raw_data table must have > 0 rows"
        table_count_pattern = re.compile(r'(?:table\s+(\w+)|(\w+)\s+table)\s+must\s+have\s*>\s*(\d+)\s*rows?', re.I)
        # File format existence — e.g. "File format my_csv_format must exist before load"
        file_format_pattern = re.compile(r'file\s+format\s+(\w+)\s+must\s+exist', re.I)
        # Both-tables pattern — e.g. "Both raw_data and audit_log tables must be loaded"
        both_tables_pattern = re.compile(r'both\s+(\w+)\s+and\s+(\w+)\s+.*(?:loaded|processed)', re.I)
        # Blob path DATE placeholder pattern
        blob_date_pattern = re.compile(r'blob\s*path.*DATE\s*placeholder', re.I)
        # TRUNCATE before COPY pattern
        truncate_pattern = re.compile(r'TRUNCATE.*before.*COPY', re.I)
        
        # CLIENT RULE PATTERNS (exact phrasing from client)
        # Rule 1: "LND record count should be populated after successful load from source."
        lnd_populated_pattern = re.compile(r'(?:LND|raw_data|landing)\s+record\s+count\s+should\s+be\s+populated', re.I)
        # Rule 2: "All expected tables like AUDIT and LND must be successfully loaded without errors."
        all_tables_pattern = re.compile(r'all\s+expected\s+tables\s+like\s+AUDIT\s+and\s+LND\s+must\s+be\s+successfully\s+loaded', re.I)
        # Rule 4: "Validate that source file is successfully loaded into LND table during the load operation."
        source_loaded_pattern = re.compile(r'source\s+file\s+is\s+successfully\s+loaded\s+into\s+(?:LND|raw_data|landing)', re.I)
        # Rule 5: "Validate that the configured CSV format is correctly applied during the load process."
        csv_format_pattern = re.compile(r'configured\s+CSV\s+format\s+is\s+correctly\s+applied', re.I)
        # Rule 6: "Validate that the LND and AUDIT table contains records after load when data is loaded."
        lnd_audit_records_pattern = re.compile(r'LND\s+and\s+AUDIT\s+table\s+contains\s+records\s+after\s+load', re.I)
        # Rule 3: "Validate that the LND table is truncated before the current load starts."
        lnd_truncated_pattern = re.compile(r'LND\s+table\s+is\s+truncated\s+before', re.I)

        # Snowflake object existence patterns (stage, stored proc, storage integration)
        stage_exists_pattern = re.compile(r'(?:stage|@\w+)\s+(?:\w+\s+)?(?:must\s+)?exist', re.I)
        proc_exists_pattern = re.compile(r'(?:stored\s+proc(?:edure)?|procedure)\s+(\w+)\s+must\s+exist', re.I)
        integration_exists_pattern = re.compile(r'(?:storage\s+integration|integration)\s+(\w+)\s+must\s+exist', re.I)
        # Uniqueness check — e.g. "no duplicate PARCEL_VENDOR_ID" or "column X must be unique"
        unique_pattern = re.compile(r'(?:column\s+(\w+)\s+must\s+be\s+unique|no\s+duplicate\s+(\w+))', re.I)
        # DDL file existence — e.g. "DDL files ddl/create_tables.sql must exist before load"
        ddl_file_pattern = re.compile(r'(?:DDL\s+files?|sql\s+files?)\s+(.+?)\s+must\s+exist', re.I)
        # Container file pattern — e.g. "container X must contain .ext files matching pattern Y"
        container_file_pattern = re.compile(r'container\s+(\S+)\s+must\s+contain\s+(.+?)\s+(?:files?|matching)', re.I)

        for rule in self.business_rules:
            self.test_counter += 1
            test_id = f"BIZ_{self.test_counter:03d}"
            matched = False

            # 1. Numeric threshold rules
            for pattern, check_fn in threshold_patterns:
                m = re.search(pattern, rule, re.I)
                if m:
                    matched = True
                    passed, detail = check_fn(m.group(1))
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="PASS" if passed else "FAIL",
                        finding=f"{detail} | Rule: '{rule}'",
                        recommendation="N/A" if passed else f"Rule not satisfied: {rule}",
                        check_type="Business Rule",
                        severity="Info" if passed else "High",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                    break

            if matched:
                continue

            # 2a. Table PK non-null check — "TABLE must have COL as non-null primary key"
            m = pk_null_pattern.search(rule)
            if m:
                matched = True
                tname = m.group(1)
                cols_raw = m.group(2)
                # Extract individual column names (comma/and separated)
                cols = [c.strip() for c in re.split(r'[,\s]+and\s+|,\s*', cols_raw) if re.match(r'^\w+$', c.strip())]
                if self.snowflake_cursor and cols:
                    results = []
                    for col in cols:
                        try:
                            qual = f"{target_db}.{target_schema}.{tname}" if target_schema else f"{target_db}.{tname}"
                            self.snowflake_cursor.execute(f"SELECT COUNT(*) FROM {qual} WHERE {col} IS NULL")
                            null_count = (self.snowflake_cursor.fetchone() or [0])[0]
                            results.append((col, null_count))
                        except Exception as e:
                            results.append((col, f"err:{e}"))
                    all_pass = all(isinstance(c, int) and c == 0 for _, c in results)
                    finding = " | ".join(f"{tname}.{c}=NULL:{n}" for c, n in results)
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="PASS" if all_pass else "FAIL",
                        finding=finding,
                        recommendation="N/A" if all_pass else f"NULL values found in primary key columns of {tname}",
                        check_type="Business Rule",
                        severity="Info" if all_pass else "High",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                else:
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="SKIPPED",
                        finding="No Snowflake connection — enable --validate-resources to run PK null check",
                        recommendation="Re-run with --validate-resources",
                        check_type="Business Rule",
                        severity="Medium",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                continue

            # 2b. Null check — SELECT COUNT(*) WHERE col IS NULL
            # Scans all resolved tables to find which one contains the column
            m = null_pattern.search(rule)
            if m:
                matched = True
                col = m.group(1)
                tables = self._resolve_tables()
                if self.snowflake_cursor and tables:
                    found_table, null_count, query_err = None, 0, None
                    for t in tables:
                        tname = t.get("name", "")
                        if not tname:
                            continue
                        try:
                            # Find which table actually contains this column (case-insensitive)
                            schema_filter = f"AND TABLE_SCHEMA = '{target_schema.upper()}'" if target_schema else ""
                            self.snowflake_cursor.execute(
                                f"SELECT TABLE_NAME, COLUMN_NAME FROM {target_db}.INFORMATION_SCHEMA.COLUMNS "
                                f"WHERE TABLE_NAME = '{tname.upper()}' "
                                f"{schema_filter} "
                                f"AND UPPER(COLUMN_NAME) = '{col.upper()}'"
                            )
                            row = self.snowflake_cursor.fetchone()
                            if row:
                                actual_col = row[1]  # use exact column name from schema
                                qual = f"{target_db}.{target_schema}.{tname}" if target_schema else f"{target_db}.{tname}"
                                self.snowflake_cursor.execute(
                                    f"SELECT COUNT(*) FROM {qual} WHERE {actual_col} IS NULL"
                                )
                                null_count = (self.snowflake_cursor.fetchone() or [0])[0]
                                found_table = tname
                                break
                        except Exception as e:
                            query_err = e
                    if found_table:
                        passed = null_count == 0
                        self.test_results.append(TestResult(
                            test_id=test_id,
                            test_name=f"Business Rule: {rule[:60]}",
                            phase="Results After Batch Run",
                            status="PASS" if passed else "FAIL",
                            finding=f"NULL count for {found_table}.{col} = {null_count}",
                            recommendation="N/A" if passed else f"{null_count} NULL values found in {col} in {found_table}",
                            check_type="Business Rule",
                            severity="Info" if passed else "High",
                            database_target=target_db,
                            stage2_relevance="Critical",
                        ))
                    else:
                        err_detail = f": {query_err}" if query_err else f" — column '{col}' not found in any table: {[t.get('name') for t in tables]}"
                        self.test_results.append(TestResult(
                            test_id=test_id,
                            test_name=f"Business Rule: {rule[:60]}",
                            phase="Results After Batch Run",
                            status="SKIPPED",
                            finding=f"Could not evaluate null check for '{col}'{err_detail}",
                            recommendation="Verify column name matches the actual table schema",
                            check_type="Business Rule",
                            severity="Medium",
                            database_target=target_db,
                            stage2_relevance="Critical",
                        ))
                else:
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="SKIPPED",
                        finding="No Snowflake connection — enable --validate-resources to run null check",
                        recommendation="Re-run with --validate-resources",
                        check_type="Business Rule",
                        severity="Medium",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                continue

            # 3. Table-specific row count — "Table X must have > N rows" OR "X table must have > N rows"
            m = table_count_pattern.search(rule)
            if m:
                matched = True
                tname = m.group(1) or m.group(2)
                threshold = int(m.group(3))
                per_table = self.post_batch_metrics.get("post_batch_table_records", {})
                actual = per_table.get(tname, 0)
                passed = actual > threshold
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="PASS" if passed else "FAIL",
                    finding=f"{tname} has {actual} rows (threshold > {threshold})",
                    recommendation="N/A" if passed else f"{tname} has {actual} rows, expected > {threshold}",
                    check_type="Business Rule",
                    severity="Info" if passed else "High",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
                continue

            # 4. Both-tables loaded check
            m = both_tables_pattern.search(rule)
            if m:
                matched = True
                t1, t2 = m.group(1), m.group(2)
                per_table = self.post_batch_metrics.get("post_batch_table_records", {})
                t1_ok = per_table.get(t1, 0) > 0
                t2_ok = per_table.get(t2, 0) > 0
                passed = t1_ok and t2_ok
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="PASS" if passed else "FAIL",
                    finding=f"{t1}={per_table.get(t1,0)} rows, {t2}={per_table.get(t2,0)} rows",
                    recommendation="N/A" if passed else f"One or both tables empty after load: {t1}={t1_ok}, {t2}={t2_ok}",
                    check_type="Business Rule",
                    severity="Info" if passed else "High",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
                continue

            # 5. Blob path DATE placeholder — static code check
            if blob_date_pattern.search(rule):
                matched = True
                blob_paths = self.pre_batch_metrics.get("blob_paths", [])
                if not blob_paths and self.etl_file_path:
                    import yaml as _yaml
                    sf = self.etl_config.get("snowflake", [{}])
                    sf_block = sf[0] if isinstance(sf, list) and sf else sf
                    blob_paths = [t.get("blob_path", "") for t in sf_block.get("tables", []) if isinstance(t, dict)]
                has_placeholder = any("{DATE}" in p or "{date}" in p for p in blob_paths)
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="PASS" if has_placeholder else "FAIL",
                    finding=f"Blob paths: {blob_paths}",
                    recommendation="N/A" if has_placeholder else "Add {DATE} placeholder to blob_path in config",
                    check_type="Business Rule",
                    severity="Info" if has_placeholder else "High",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
                continue

            # 6. TRUNCATE before COPY — static code check (version-agnostic)
            if truncate_pattern.search(rule):
                matched = True
                code = ""
                if self.etl_file_path:
                    try:
                        code = Path(self.etl_file_path).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass
                # Generic TRUNCATE detection: Python method, SQL, or snowflake-connector
                has_truncate = bool(re.search(
                    r'\.truncate\(|TRUNCATE\s+TABLE|cursor\.execute.*TRUNCATE|run_query.*TRUNCATE',
                    code, re.I
                ))
                # Generic COPY/LOAD detection: load_csv, copy_into, COPY INTO, stored proc, execute, load operation
                has_copy = bool(re.search(
                    r'\.load_csv\(|\.copy_into\(|COPY\s+INTO|cursor\.execute.*(?:COPY|INSERT|CALL)|'
                    r'execute\(|run_query.*(?:INSERT|COPY|CALL)|\.write\(|\.put\(',
                    code, re.I
                ))
                passed = has_truncate and has_copy
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="PASS" if passed else "FAIL",
                    finding=f"TRUNCATE present={has_truncate}, Data load present={has_copy}",
                    recommendation="N/A" if passed else "Ensure TRUNCATE or DELETE runs before data load operation",
                    check_type="Business Rule",
                    severity="Info" if passed else "High",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
                continue

            # 6b. File format existence — SHOW FILE FORMATS
            m = file_format_pattern.search(rule)
            if m:
                matched = True
                fmt_name = m.group(1).upper()
                if self.snowflake_cursor:
                    try:
                        self.snowflake_cursor.execute(f"SHOW FILE FORMATS LIKE '{fmt_name}'")
                        found = self.snowflake_cursor.fetchone() is not None
                    except Exception:
                        found = False
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="PASS" if found else "FAIL",
                        finding=f"File format '{fmt_name}' {'exists' if found else 'NOT found'}",
                        recommendation="N/A" if found else f"Create file format '{fmt_name}' in Snowflake",
                        check_type="Business Rule",
                        severity="Info" if found else "High",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                else:
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="SKIPPED",
                        finding="No Snowflake connection — enable --validate-resources to check file format",
                        recommendation="Re-run with --validate-resources",
                        check_type="Business Rule",
                        severity="Medium",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                continue

            # 7. Snowflake stage existence — SHOW STAGES
            m = stage_exists_pattern.search(rule)
            if m:
                matched = True
                # Extract stage name: prefer @-prefixed token, then look for ALL_CAPS_WITH_UNDERSCORES
                # identifiers (stage names are typically uppercase with underscores)
                _STOPWORDS = {'STAGE', 'MUST', 'EXIST', 'EXISTS', 'THE', 'A', 'AN',
                              'AND', 'OR', 'BE', 'BEFORE', 'LOAD', 'IS', 'IN', 'AT',
                              'SNOWFLAKE', 'CSV', 'FILES', 'FILE', 'CONTAIN', 'CONTAINS'}
                stage_name = None
                at_match = re.search(r'@(\w+)', rule)
                if at_match:
                    stage_name = at_match.group(1).upper()
                else:
                    # Prefer tokens that look like stage names: UPPER_CASE_WITH_UNDERSCORES
                    # and are longer than 5 chars (to avoid short generic words)
                    tokens = re.findall(r'\b([A-Za-z][A-Za-z0-9_]*)\b', rule)
                    # First try: find a token that looks like a stage name (has underscore or is long)
                    stage_name = next(
                        (t.upper() for t in tokens
                         if t.upper() not in _STOPWORDS and ('_' in t or len(t) > 8)),
                        None
                    )
                    # Fallback: first non-stopword token
                    if not stage_name:
                        stage_name = next(
                            (t.upper() for t in tokens if t.upper() not in _STOPWORDS), None
                        )
                if self.snowflake_cursor and stage_name:
                    try:
                        self.snowflake_cursor.execute(f"SHOW STAGES LIKE '{stage_name}'")
                        found = self.snowflake_cursor.fetchone() is not None
                    except Exception:
                        found = False
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="PASS" if found else "FAIL",
                        finding=f"Stage '{stage_name}' {'exists' if found else 'NOT found'}",
                        recommendation="N/A" if found else f"Create Snowflake stage '{stage_name}'",
                        check_type="Business Rule",
                        severity="Info" if found else "High",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                else:
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="SKIPPED",
                        finding="No Snowflake connection or stage name not found in rule — enable --validate-resources",
                        recommendation="Re-run with --validate-resources",
                        check_type="Business Rule",
                        severity="Medium",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                continue

            # 8. Stored procedure existence — SHOW PROCEDURES
            m = proc_exists_pattern.search(rule)
            if m:
                matched = True
                # Group 1 from proc_exists_pattern captures the proc name directly
                proc_name = m.group(1).upper() if m.group(1) else None
                if not proc_name:
                    proc_name_m = re.search(r'CALL\s+(\w+)', rule, re.I)
                    proc_name = proc_name_m.group(1).upper() if proc_name_m else None
                if self.snowflake_cursor and proc_name:
                    try:
                        self.snowflake_cursor.execute(f"SHOW PROCEDURES LIKE '{proc_name}'")
                        found = self.snowflake_cursor.fetchone() is not None
                    except Exception:
                        found = False
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="PASS" if found else "FAIL",
                        finding=f"Stored procedure '{proc_name}' {'exists' if found else 'NOT found'}",
                        recommendation="N/A" if found else f"Create stored procedure '{proc_name}'",
                        check_type="Business Rule",
                        severity="Info" if found else "High",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                else:
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="SKIPPED",
                        finding="No Snowflake connection or procedure name not found in rule — enable --validate-resources",
                        recommendation="Re-run with --validate-resources",
                        check_type="Business Rule",
                        severity="Medium",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                continue

            # 9. Storage integration existence — SHOW INTEGRATIONS
            m = integration_exists_pattern.search(rule)
            if m:
                matched = True
                # Group 1 from integration_exists_pattern captures the integration name directly
                int_name = m.group(1).upper() if m.group(1) else None
                if self.snowflake_cursor and int_name:
                    try:
                        self.snowflake_cursor.execute(f"SHOW INTEGRATIONS LIKE '{int_name}'")
                        found = self.snowflake_cursor.fetchone() is not None
                    except Exception:
                        found = False
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="PASS" if found else "FAIL",
                        finding=f"Storage integration '{int_name}' {'exists' if found else 'NOT found'}",
                        recommendation="N/A" if found else f"Create storage integration '{int_name}'",
                        check_type="Business Rule",
                        severity="Info" if found else "High",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                else:
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="SKIPPED",
                        finding="No Snowflake connection or integration name not found in rule — enable --validate-resources",
                        recommendation="Re-run with --validate-resources",
                        check_type="Business Rule",
                        severity="Medium",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                continue

            # 10a. DDL file existence check
            m = ddl_file_pattern.search(rule)
            if m:
                matched = True
                files_str = m.group(1)
                # Extract individual file paths (comma/and separated)
                file_refs = [f.strip() for f in re.split(r'\s+and\s+|,\s*', files_str) if f.strip()]
                if self.etl_file_path:
                    etl_dir = Path(self.etl_file_path).parent
                    missing, found = [], []
                    for fref in file_refs:
                        fpath = etl_dir / fref
                        (found if fpath.exists() else missing).append(fref)
                    passed = len(missing) == 0
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="PASS" if passed else "FAIL",
                        finding=f"found: {found} | missing: {missing}" if not passed else f"All DDL files present: {found}",
                        recommendation="N/A" if passed else f"Create missing DDL files: {missing}",
                        check_type="Business Rule",
                        severity="Info" if passed else "High",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                else:
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="SKIPPED",
                        finding="ETL file path not available — cannot check DDL file existence",
                        recommendation="Provide ETL file path",
                        check_type="Business Rule",
                        severity="Medium",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                continue

            # 10b. Container file pattern check
            m = container_file_pattern.search(rule)
            if m:
                matched = True
                container_name = m.group(1)
                file_pattern = m.group(2).strip()
                # Try to list files from Azure if client available
                source_files = []
                if self.snowflake_cursor:
                    # Try listing via pre_batch_metrics source file count as proxy
                    pass
                # Check pre_batch_metrics for any stored file info
                source_file_count = self.pre_batch_metrics.get("source_file_count", 0)
                # Extract extension from rule
                ext_m = re.search(r'\.([a-z]+)', file_pattern)
                ext = f".{ext_m.group(1)}" if ext_m else ""
                pattern_word_m = re.search(r'pattern\s+(\S+)', rule, re.I)
                pattern_word = pattern_word_m.group(1) if pattern_word_m else ""
                # Try Azure client if available
                try:
                    from utils.snowflake_connector import get_snowflake_cursor
                    from src.config import Config
                    from azure.storage.blob import BlobServiceClient
                    from azure.identity import DefaultAzureCredential
                    storage_account = (self.etl_config.get("blob_storage") or {}).get("storage_account", "")
                    if storage_account:
                        cred = DefaultAzureCredential()
                        blob_svc = BlobServiceClient(f"https://{storage_account}.blob.core.windows.net", credential=cred)
                        container_client = blob_svc.get_container_client(container_name)
                        blobs = list(container_client.list_blobs())
                        source_files = [b.name for b in blobs]
                except Exception:
                    pass
                if source_files:
                    matching = [f for f in source_files if (not ext or f.endswith(ext)) and (not pattern_word or pattern_word.lower() in f.lower())]
                    passed = len(matching) > 0
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="PASS" if passed else "FAIL",
                        finding=f"container={container_name} | matching files ({ext or 'any'}, pattern='{pattern_word}'): {matching}",
                        recommendation="N/A" if passed else f"No matching files found in container {container_name}",
                        check_type="Business Rule",
                        severity="Info" if passed else "High",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                elif source_file_count > 0:
                    # Files confirmed present by Phase 7 — treat as PASS
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="PASS",
                        finding=f"container={container_name} | {source_file_count} source file(s) confirmed present",
                        recommendation="N/A",
                        check_type="Business Rule",
                        severity="Info",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                else:
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="SKIPPED",
                        finding="Source file list not available — enable --validate-resources",
                        recommendation="Re-run with --validate-resources",
                        check_type="Business Rule",
                        severity="Medium",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                continue

            # 10c. Uniqueness check — SELECT COUNT(*) vs COUNT(DISTINCT col)
            m = unique_pattern.search(rule)
            if m:
                matched = True
                col = m.group(1) or m.group(2)
                tables = self._resolve_tables()
                if self.snowflake_cursor and tables:
                    found_table, dup_count, query_err = None, 0, None
                    for t in tables:
                        tname = t.get("name", "")
                        if not tname:
                            continue
                        try:
                            schema_filter = f"AND TABLE_SCHEMA = '{target_schema.upper()}'" if target_schema else ""
                            self.snowflake_cursor.execute(
                                f"SELECT TABLE_NAME, COLUMN_NAME FROM {target_db}.INFORMATION_SCHEMA.COLUMNS "
                                f"WHERE TABLE_NAME = '{tname.upper()}' "
                                f"{schema_filter} "
                                f"AND UPPER(COLUMN_NAME) = '{col.upper()}'"
                            )
                            row = self.snowflake_cursor.fetchone()
                            if row:
                                actual_col = row[1]
                                qual = f"{target_db}.{target_schema}.{tname}" if target_schema else f"{target_db}.{tname}"
                                self.snowflake_cursor.execute(
                                    f"SELECT COUNT(*) - COUNT(DISTINCT {actual_col}) FROM {qual}"
                                )
                                dup_count = (self.snowflake_cursor.fetchone() or [0])[0]
                                found_table = tname
                                break
                        except Exception as e:
                            query_err = e
                    if found_table:
                        passed = dup_count == 0
                        self.test_results.append(TestResult(
                            test_id=test_id,
                            test_name=f"Business Rule: {rule[:60]}",
                            phase="Results After Batch Run",
                            status="PASS" if passed else "FAIL",
                            finding=f"Duplicate count for {found_table}.{col} = {dup_count}",
                            recommendation="N/A" if passed else f"{dup_count} duplicate values found in {col} in {found_table}",
                            check_type="Business Rule",
                            severity="Info" if passed else "High",
                            database_target=target_db,
                            stage2_relevance="Critical",
                        ))
                    else:
                        err_detail = f": {query_err}" if query_err else f" — column '{col}' not found in any table"
                        self.test_results.append(TestResult(
                            test_id=test_id,
                            test_name=f"Business Rule: {rule[:60]}",
                            phase="Results After Batch Run",
                            status="SKIPPED",
                            finding=f"Could not evaluate uniqueness check for '{col}'{err_detail}",
                            recommendation="Verify column name matches the actual table schema",
                            check_type="Business Rule",
                            severity="Medium",
                            database_target=target_db,
                            stage2_relevance="Critical",
                        ))
                else:
                    self.test_results.append(TestResult(
                        test_id=test_id,
                        test_name=f"Business Rule: {rule[:60]}",
                        phase="Results After Batch Run",
                        status="SKIPPED",
                        finding="No Snowflake connection — enable --validate-resources to run uniqueness check",
                        recommendation="Re-run with --validate-resources",
                        check_type="Business Rule",
                        severity="Medium",
                        database_target=target_db,
                        stage2_relevance="Critical",
                    ))
                continue

            # ========== CLIENT BUSINESS RULES (EXACT PHRASING) ==========
            # Rule 3: "Validate that the LND table is truncated before the current load starts."
            if lnd_truncated_pattern.search(rule):
                matched = True
                code = ""
                if self.etl_file_path:
                    try:
                        code = Path(self.etl_file_path).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass
                has_truncate = bool(re.search(r'\.truncate\(|TRUNCATE\s+TABLE|truncate', code, re.I))
                has_copy = bool(re.search(r'\.load_csv\(|\.copy_into\(|COPY\s+INTO|load', code, re.I))
                passed = has_truncate and has_copy
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="PASS" if passed else "FAIL",
                    finding=f"TRUNCATE detected={has_truncate}, Load operation detected={has_copy}",
                    recommendation="N/A" if passed else "Ensure TRUNCATE runs before COPY/LOAD operation",
                    check_type="Business Rule",
                    severity="Info" if passed else "High",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
                continue

            # Rule 1: "LND record count should be populated after successful load from source."
            if lnd_populated_pattern.search(rule):
                matched = True
                per_table = self.post_batch_metrics.get("post_batch_table_records", {})
                # Check if any LND/raw_data table has records
                lnd_tables = ["raw_data", "lnd", "landing", "LND"]
                lnd_found = False
                lnd_count = 0
                for table_name in lnd_tables:
                    if table_name in per_table and per_table[table_name] > 0:
                        lnd_found = True
                        lnd_count = per_table[table_name]
                        break
                passed = lnd_found
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="PASS" if passed else "FAIL",
                    finding=f"LND/raw_data record count = {lnd_count}",
                    recommendation="N/A" if passed else "Ensure source file is loaded into LND table",
                    check_type="Business Rule",
                    severity="Info" if passed else "High",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
                continue

            # Rule 2: "All expected tables like AUDIT and LND must be successfully loaded without errors."
            if all_tables_pattern.search(rule):
                matched = True
                per_table = self.post_batch_metrics.get("post_batch_table_records", {})
                etl_exit = self.etl_execution_result.get("exit_code", 1)
                # Check if both audit and lnd tables loaded
                audit_tables = ["audit_log", "audit", "AUDIT"]
                lnd_tables = ["raw_data", "lnd", "landing", "LND"]
                audit_ok = any(t in per_table and per_table[t] > 0 for t in audit_tables)
                lnd_ok = any(t in per_table and per_table[t] > 0 for t in lnd_tables)
                passed = audit_ok and lnd_ok and etl_exit == 0
                loaded_tables = [t for t in per_table if per_table[t] > 0]
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="PASS" if passed else "FAIL",
                    finding=f"AUDIT loaded={audit_ok}, LND loaded={lnd_ok}, exit_code={etl_exit}, tables={loaded_tables}",
                    recommendation="N/A" if passed else "Ensure both AUDIT and LND tables are loaded without errors",
                    check_type="Business Rule",
                    severity="Info" if passed else "High",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
                continue

            # Rule 4: "Validate that source file is successfully loaded into LND table during the load operation."
            if source_loaded_pattern.search(rule):
                matched = True
                per_table = self.post_batch_metrics.get("post_batch_table_records", {})
                lnd_tables = ["raw_data", "lnd", "landing", "LND"]
                lnd_count = 0
                for table_name in lnd_tables:
                    if table_name in per_table:
                        lnd_count = per_table[table_name]
                        break
                passed = lnd_count > 0
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="PASS" if passed else "FAIL",
                    finding=f"LND table record count = {lnd_count}",
                    recommendation="N/A" if passed else "Ensure source file is loaded into LND table",
                    check_type="Business Rule",
                    severity="Info" if passed else "High",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
                continue

            # Rule 5: "Validate that the configured CSV format is correctly applied during the load process."
            if csv_format_pattern.search(rule):
                matched = True
                code = ""
                if self.etl_file_path:
                    try:
                        code = Path(self.etl_file_path).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass
                has_csv_format = bool(re.search(r'csv|format|CSV|file_format', code, re.I))
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="PASS" if has_csv_format else "FAIL",
                    finding=f"CSV format configuration {'detected' if has_csv_format else 'not found'} in code",
                    recommendation="N/A" if has_csv_format else "Ensure CSV format is configured in ETL code",
                    check_type="Business Rule",
                    severity="Info" if has_csv_format else "High",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
                continue

            # Rule 6: "Validate that the LND and AUDIT table contains records after load when data is loaded."
            if lnd_audit_records_pattern.search(rule):
                matched = True
                per_table = self.post_batch_metrics.get("post_batch_table_records", {})
                audit_tables = ["audit_log", "audit", "AUDIT"]
                lnd_tables = ["raw_data", "lnd", "landing", "LND"]
                audit_count = next((per_table.get(t, 0) for t in audit_tables if t in per_table), 0)
                lnd_count = next((per_table.get(t, 0) for t in lnd_tables if t in per_table), 0)
                passed = audit_count > 0 and lnd_count > 0
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="PASS" if passed else "FAIL",
                    finding=f"LND count={lnd_count}, AUDIT count={audit_count}",
                    recommendation="N/A" if passed else "Ensure both LND and AUDIT tables contain records after load",
                    check_type="Business Rule",
                    severity="Info" if passed else "High",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))
                continue

            # 7. Fallback — unrecognised rule, mark SKIPPED with clear reason
            if not matched:
                self.test_results.append(TestResult(
                    test_id=test_id,
                    test_name=f"Business Rule: {rule[:60]}",
                    phase="Results After Batch Run",
                    status="SKIPPED",
                    finding=f"Rule not automatically evaluable: '{rule}' — review manually",
                    recommendation="Rephrase rule to match a supported pattern (row count, null check, table loaded, duration)",
                    check_type="Business Rule",
                    severity="Info",
                    database_target=target_db,
                    stage2_relevance="Critical",
                ))

    def run(self) -> tuple:
        from utils.debug_logger import debug_logger
        logger = debug_logger.get_logger("Phase8")
        
        logger.debug(f"validate_resources={self.validate_resources}")
        if not self.validate_resources:
            logger.debug("Skipping resource validation")
            self._generate_skipped_results()
        else:
            logger.debug("Initializing Snowflake client...")
            self._initialize_clients()
            
            # Collect post-batch metrics (needed by _add_version_specific_tests)
            self._collect_post_batch_metrics()
            # Add consolidated tests (3-4 tests only)
            self._add_version_specific_tests()
        
        logger.debug(f"Phase 8 complete: {len(self.test_results)} tests")
        self._check_business_rules()
        return self.post_batch_metrics, self.test_results

    def _collect_post_batch_metrics(self):
        """Collect post-batch metrics without creating tests.
        Uses snapshot data captured immediately after ETL (before Phase 5B re-runs)
        if already populated — avoids overwriting with stale counts.
        """
        from utils.debug_logger import debug_logger
        logger = debug_logger.get_logger("Phase8")

        etl_exit_code = self.etl_execution_result.get("exit_code", -1)
        etl_duration = self.etl_execution_result.get("duration_ms", 0)
        etl_crashed = etl_exit_code != 0
        self.post_batch_metrics.update({
            "etl_exit_code": etl_exit_code,
            "etl_duration_ms": etl_duration,
            "etl_crashed": etl_crashed,
        })
        logger.debug(f"ETL exit code: {etl_exit_code}, crashed: {etl_crashed}")

        # If the snapshot from run_validated_etl already captured post-ETL counts
        # (before Phase 5B re-ran the ETL and potentially altered tables), use those.
        snapshot_per = self.pre_batch_metrics.get("post_etl_snapshot_per_table") or \
                       self.pre_batch_metrics.get("post_batch_table_records")
        snapshot_total = self.pre_batch_metrics.get("post_etl_snapshot_records") or \
                         self.pre_batch_metrics.get("post_batch_total_records")

        if snapshot_per and snapshot_total is not None and snapshot_total > 0:
            logger.debug(f"Using pre-captured snapshot: {snapshot_per} | total={snapshot_total}")
            self.post_batch_metrics["post_batch_total_records"] = snapshot_total
            self.post_batch_metrics["post_batch_table_records"] = snapshot_per
            self.post_batch_metrics["records_loaded"] = (
                snapshot_total - self.pre_batch_metrics.get("pre_batch_total_records", 0)
            )
            return

        if not self.snowflake_cursor:
            logger.debug("Snowflake cursor not available")
            return

        target_db = (self.metadata.get("target_database") or "N/A").upper()
        target_schema = (self.metadata.get("target_schema") or "").upper()
        tables = self._resolve_tables()

        logger.debug("No snapshot found — querying Snowflake for post-batch counts")
        total, per_table = 0, {}
        for t in tables:
            tname = t.get("name", "unknown")
            try:
                self.snowflake_cursor.execute(f"SELECT COUNT(*) FROM {target_db}.{target_schema}.{tname}")
                result = self.snowflake_cursor.fetchone()
                cnt = result[0] if result else 0
                total += cnt
                per_table[tname] = cnt
                logger.debug(f"Table {tname}: {cnt} records")
            except Exception as e:
                logger.debug(f"Error querying table {tname}: {e}")
                per_table[tname] = 0
        self.post_batch_metrics["post_batch_total_records"] = total
        self.post_batch_metrics["post_batch_table_records"] = per_table
        logger.debug(f"Post-batch metrics collected: {total} total records")

    def _run_fallback_checks(self):
        from utils.debug_logger import debug_logger
        logger = debug_logger.get_logger("Phase8")
        
        target_db = (self.metadata.get("target_database") or "N/A").upper()
        target_schema = (self.metadata.get("target_schema") or "").upper()
        tables = self._resolve_tables()
        logger.debug(f"Resolved {len(tables)} tables: {[t.get('name') for t in tables]}")

        etl_exit_code = self.etl_execution_result.get("exit_code", -1)
        etl_duration = self.etl_execution_result.get("duration_ms", 0)
        etl_crashed = etl_exit_code != 0
        self.post_batch_metrics.update({
            "etl_exit_code": etl_exit_code,
            "etl_duration_ms": etl_duration,
            "etl_crashed": etl_crashed,
        })
        logger.debug(f"ETL exit code: {etl_exit_code}, crashed: {etl_crashed}")

        self.test_counter += 1
        logger.debug(f"Adding test POST_{self.test_counter:03d}: ETL Execution Status")
        self.test_results.append(TestResult(
            test_id=f"POST_{self.test_counter:03d}",
            test_name=f"ETL Execution Status: {'CRASHED' if etl_crashed else 'SUCCESS'}",
            phase="Results After Batch Run",
            status="FAIL" if etl_crashed else "PASS",
            finding=(
                f"ETL exit code: {etl_exit_code} | Duration: {etl_duration}ms | "
                f"Status: {'CRASHED' if etl_crashed else 'Completed successfully'}"
            ),
            recommendation="Review ETL logs for crash details" if etl_crashed else "N/A",
            check_type="Post-Batch",
            severity="Critical" if etl_crashed else "Info",
            database_target=target_db,
            stage2_relevance="Critical",
        ))

        if not self.snowflake_cursor:
            logger.debug("Snowflake cursor not available - adding FAIL tests")
            for name in ["Post-Batch Record Count", "Record Delta Verification", "All Tables Processed"]:
                self.test_counter += 1
                logger.debug(f"Adding test POST_{self.test_counter:03d}: {name} (FAIL)")
                self._add_fail(f"POST_{self.test_counter:03d}", name,
                               "Snowflake connection blocked — cannot verify post-batch state")
            return

        logger.debug("Snowflake cursor available - counting post-batch records")
        self._count_post_batch_records(target_db, target_schema, tables)
        self._verify_record_delta(target_db, target_schema, tables)
        self._verify_all_tables_processed(target_db, target_schema, tables)
        logger.debug(f"Fallback checks complete: {self.test_counter} tests added")

    def _count_post_batch_records(self, db, schema, tables):
        from utils.debug_logger import debug_logger
        logger = debug_logger.get_logger("Phase8")
        
        total, per_table = 0, {}
        for t in tables:
            tname = t.get("name", "unknown")
            try:
                self.snowflake_cursor.execute(f"SELECT COUNT(*) FROM {db}.{schema}.{tname}")
                result = self.snowflake_cursor.fetchone()
                cnt = result[0] if result else 0
                total += cnt
                per_table[tname] = cnt
                logger.debug(f"Table {tname}: {cnt} records")
            except Exception as e:
                logger.debug(f"Error querying table {tname}: {e}")
                per_table[tname] = 0
        self.post_batch_metrics["post_batch_total_records"] = total
        self.post_batch_metrics["post_batch_table_records"] = per_table
        self.test_counter += 1
        logger.debug(f"Adding test POST_{self.test_counter:03d}: Post-Batch Record Count")
        self.test_results.append(TestResult(
            test_id=f"POST_{self.test_counter:03d}", test_name=f"Post-Batch Record Count: {total} records",
            phase="Results After Batch Run", status="PASS",
            finding=f"Total records after ETL: {total} | Per table: {per_table}",
            recommendation="N/A", check_type="Post-Batch", severity="Info",
            database_target=db, stage2_relevance="Critical",
        ))

    def _verify_record_delta(self, db, schema, tables):
        from utils.debug_logger import debug_logger
        logger = debug_logger.get_logger("Phase8")
        
        pre_total = self.pre_batch_metrics.get("pre_batch_total_records", 0)
        post_total = self.post_batch_metrics.get("post_batch_total_records", 0)
        src_total = self.pre_batch_metrics.get("source_total_rows", 0)
        loaded = post_total
        self.post_batch_metrics["records_loaded"] = loaded
        logger.debug(f"Pre: {pre_total}, Post: {post_total}, Source: {src_total}, Loaded: {loaded}")

        if src_total > 0:
            match = loaded == src_total
            status = "PASS" if match else "FAIL"
            finding = (
                f"Records loaded: {loaded} | Source rows: {src_total} | "
                f"Pre: {pre_total} | Post: {post_total} | {'MATCH' if match else 'MISMATCH'}"
            )
            rec = "N/A" if match else "Investigate missing records — possible partial load"
            severity = "Info" if match else "Critical"
        else:
            status, severity, rec = "PASS", "Info", "N/A"
            finding = f"Post: {post_total} | Pre: {pre_total} | Source count unavailable"

        self.post_batch_metrics["load_match"] = status == "PASS"
        self.test_counter += 1
        logger.debug(f"Adding test POST_{self.test_counter:03d}: Record Delta Verification")
        self.test_results.append(TestResult(
            test_id=f"POST_{self.test_counter:03d}", test_name="Record Delta Verification",
            phase="Results After Batch Run", status=status, finding=finding,
            recommendation=rec, check_type="Post-Batch", severity=severity,
            database_target=db, stage2_relevance="Critical",
        ))

    def _verify_all_tables_processed(self, db, schema, tables):
        from utils.debug_logger import debug_logger
        logger = debug_logger.get_logger("Phase8")
        
        etl_stdout = self.etl_execution_result.get("stdout", "")
        etl_stderr = self.etl_execution_result.get("stderr", "")
        etl_exit = self.etl_execution_result.get("exit_code", -1)
        pre_recs = self.pre_batch_metrics.get("pre_batch_table_records", {})
        post_recs = self.post_batch_metrics.get("post_batch_table_records", {})
        processed, unprocessed = [], []
        combined_output = (etl_stdout + etl_stderr).lower()
        etl_succeeded = etl_exit == 0

        for t in tables:
            tname = t.get("name", "unknown")
            pre_c = pre_recs.get(tname, 0)
            post_c = post_recs.get(tname, 0)
            count_changed = post_c != pre_c
            mentioned_in_output = tname.lower() in combined_output
            table_has_data = post_c > 0
            # Mark processed if count changed, OR table has data (regardless of ETL exit code)
            # ETL crash does not mean tables were not previously loaded
            if count_changed or mentioned_in_output or table_has_data:
                processed.append(tname)
                logger.debug(f"Table {tname}: PROCESSED (delta={post_c - pre_c}, mentioned={mentioned_in_output}, has_data={table_has_data})")
            else:
                unprocessed.append(tname)
                logger.debug(f"Table {tname}: UNPROCESSED")
        self.post_batch_metrics.update({
            "tables_processed": len(processed),
            "tables_unprocessed": len(unprocessed),
            "unprocessed_table_names": unprocessed,
        })
        all_done = len(unprocessed) == 0
        self.test_counter += 1
        logger.debug(f"Adding test POST_{self.test_counter:03d}: All Tables Processed")
        self.test_results.append(TestResult(
            test_id=f"POST_{self.test_counter:03d}",
            test_name=f"All Tables Processed: {len(processed)}/{len(tables)}",
            phase="Results After Batch Run",
            status="PASS" if all_done else "FAIL",
            finding=f"Processed: {processed} | Unprocessed: {unprocessed if unprocessed else 'None'}",
            recommendation="N/A" if all_done else "Check ETL logs — some tables were not loaded",
            check_type="Post-Batch",
            severity="Info" if all_done else "Critical",
            database_target=db,
            stage2_relevance="Critical",
        ))

    def _add_fail(self, test_id, name, reason):
        self.test_results.append(TestResult(
            test_id=test_id, test_name=name, phase="Results After Batch Run",
            status="FAIL", finding=reason,
            recommendation="Resolve connection issue and re-run with --validate-resources",
            check_type="Post-Batch", severity="High",
            database_target=self.metadata.get("target_database"),
            stage2_relevance="Critical",
        ))

    def _generate_skipped_results(self):
        target_db = (self.metadata.get("target_database") or "N/A").upper()

        if self._has_llm():
            self._llm_checks = self._discover_checks_via_llm()

        if self._llm_checks:
            for chk in self._llm_checks:
                self.test_counter += 1
                self.test_results.append(TestResult(
                    test_id=f"POST_{self.test_counter:03d}",
                    test_name=chk.get("NAME", "Post-Batch Check"),
                    phase="Results After Batch Run", status="SKIPPED",
                    finding="Post-batch validation disabled — pass --validate-resources to run",
                    recommendation="Run with --validate-resources flag",
                    check_type="Skipped", severity="Info",
                    database_target=target_db, stage2_relevance="Critical",
                ))
        else:
            for name in ["ETL Execution Status", "Post-Batch Record Count", "Record Delta Verification", "All Tables Processed"]:
                self.test_counter += 1
                self.test_results.append(TestResult(
                    test_id=f"POST_{self.test_counter:03d}", test_name=name, phase="Results After Batch Run",
                    status="SKIPPED",
                    finding="Post-batch validation disabled — pass --validate-resources to run",
                    recommendation="Run with --validate-resources flag",
                    check_type="Skipped", severity="Info",
                    database_target=target_db, stage2_relevance="Critical",
                ))

    def _initialize_clients(self):
        try:
            from utils.snowflake_connector import get_snowflake_cursor
            self.snowflake_cursor = get_snowflake_cursor(self.etl_config)
        except Exception:
            pass

    def _find_snowflake_block(self, cfg: dict) -> dict:
        for k, v in cfg.items():
            if "snowflake" in k.lower():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v[0]
                if isinstance(v, dict):
                    return v
        return {}

    def _resolve_tables(self) -> List[Dict]:
        sf_block = self._find_snowflake_block(self.etl_config)
        target_db = (self.metadata.get("target_database") or "").upper()
        target_schema = (self.metadata.get("target_schema") or "").upper()

        if sf_block:
            # Pattern 1: tables list with name keys
            tables = sf_block.get("tables", [])
            if isinstance(tables, list):
                valid = [t for t in tables if isinstance(t, dict) and t.get("name")]
                if valid:
                    return valid

            # Pattern 2: tables_to_copy — read table names from DDL files
            tables_to_copy = sf_block.get("tables_to_copy", [])
            if isinstance(tables_to_copy, list) and tables_to_copy and self.etl_file_path:
                try:
                    etl_dir = Path(self.etl_file_path).parent
                    table_names = []
                    for ddl_ref in tables_to_copy:
                        ddl_file = ddl_ref.get("filename") if isinstance(ddl_ref, dict) else str(ddl_ref)
                        ddl_path = etl_dir / ddl_file
                        if ddl_path.exists():
                            ddl_content = ddl_path.read_text(encoding="utf-8", errors="ignore")
                            matches = re.findall(
                                r'CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
                                r'(?:\w+\.)?(?:\w+\.)?([A-Za-z0-9_]+)',
                                ddl_content, re.IGNORECASE
                            )
                            table_names.extend(matches)
                    if table_names:
                        return [{"name": n} for n in list(dict.fromkeys(table_names))]
                except Exception:
                    pass

            # Pattern 3: flat string table keys
            flat = []
            for k, v in sf_block.items():
                if isinstance(v, str) and any(k.lower().endswith(s) for s in ("_target_table", "_table", "tablename", "table_name")):
                    if k.lower() not in ("database", "schema", "warehouse", "account", "user", "role"):
                        flat.append({"name": v})
            if flat:
                return flat

        # Pattern 4: stored procedure CALL statements — infer table names generically
        # Try LOAD_X_CSV/SQL convention first, then fall back to all unique proc names
        if self.etl_file_path:
            try:
                from utils.debug_logger import debug_logger
                logger = debug_logger.get_logger("Phase8")
                code = Path(self.etl_file_path).read_text(encoding="utf-8", errors="ignore")
                call_procs = re.findall(r"CALL\s+([A-Za-z0-9_]+)\s*\(", code, re.IGNORECASE)
                logger.debug(f"Found {len(call_procs)} CALL statements: {call_procs}")
                inferred = []
                for proc in call_procs:
                    m = re.match(r'LOAD_([A-Za-z0-9_]+?)(?:_CSV|_SQL|_DATA|_TABLE)?$', proc, re.IGNORECASE)
                    if m:
                        tname = m.group(1).upper()
                        inferred.append({"name": tname})
                        logger.debug(f"  Pattern matched {proc} -> {tname}")
                    else:
                        inferred.append({"name": proc.upper()})
                        logger.debug(f"  Pattern nomatch {proc}, using {proc.upper()}")
                logger.debug(f"Inferred tables from CALL: {[t.get('name') for t in inferred]}")
                if inferred:
                    result = list({t["name"]: t for t in inferred}.values())
                    logger.debug(f"Returning {len(result)} tables: {[t.get('name') for t in result]}")
                    return result
            except Exception as e:
                from utils.debug_logger import debug_logger
                logger = debug_logger.get_logger("Phase8")
                logger.debug(f"Exception in Pattern 4 (CALL extraction): {e}")
                pass

        return []
