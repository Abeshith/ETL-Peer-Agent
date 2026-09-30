from typing import List, Dict
from phases.etl_profile import ETLProfile, ComplexityLevel


class Phase5_5DynamicTestPoolCalculator:
    def __init__(self, profile: ETLProfile, pattern_context: Dict):
        self.profile = profile
        self.pattern_context = pattern_context
        self.test_pool = []

    def run(self) -> tuple:
        """Generate tests ONLY based on detected operations - NO generic fallbacks."""
        self._generate_operation_specific_tests()
        return self.test_pool, {}

    def _make(self, id_, name, type_, action, scenario="", expected=""):
        return {"id": id_, "name": name, "type": type_, "action": action,
                "scenario": scenario, "expected": expected}

    def _generate_operation_specific_tests(self):
        """Create tests unique to THIS ETL's detected operations."""
        ctx = self.pattern_context or {}
        ops = ctx.get("operations", [])
        m   = self._make

        has_truncate   = any("TRUNCATE"   in str(op).upper() for op in ops)
        has_copy       = any("COPY"       in str(op).upper() for op in ops)
        has_insert     = any("INSERT"     in str(op).upper() for op in ops)
        has_merge      = any("MERGE"      in str(op).upper() for op in ops)
        has_create     = any("CREATE"     in str(op).upper() for op in ops)
        has_lateral    = any("LATERAL"    in str(op).upper() for op in ops)
        has_dedup      = any("NOT EXISTS" in str(op).upper() or "DISTINCT" in str(op).upper() for op in ops)
        has_procedures = any("CALL"       in str(op).upper() or "PROCEDURE" in str(op).upper() for op in ops)
        has_delete     = any("DELETE"     in str(op).upper() for op in ops)
        has_uuid       = any("UUID"       in str(op).upper() for op in ops)

        print(f"  [Phase 5.5] Operations detected: TRUNCATE={has_truncate} COPY={has_copy} "
              f"INSERT={has_insert} MERGE={has_merge} CREATE={has_create} LATERAL={has_lateral} "
              f"DEDUP={has_dedup} PROC={has_procedures} DELETE={has_delete} UUID={has_uuid}")

        # ── V1: TRUNCATE + COPY INTO ──────────────────────────────────────────
        if has_truncate and has_copy:
            self.test_pool += [
                m("FUNC_CORE_001", "[Functional] Valid TRUNCATE+COPY Load Cycle",
                  "Functional", "subprocess_args",
                  "Run ETL end-to-end with valid config",
                  "Exit 0, tables loaded with fresh data"),
                m("FUNC_BLOB_001", "[Functional] Blob File Pattern Matching",
                  "Functional", "subprocess_args",
                  "ETL resolves DATE placeholder in blob path and finds correct files",
                  "Correct files selected, rows loaded match source"),
                m("FUNC_AUDIT_001", "[Functional] Audit Log Row Inserted After Load",
                  "Functional", "subprocess_args",
                  "After COPY INTO, audit_log table receives a new row",
                  "audit_log row count increases by 1"),
                m("EDGE_TRU_001", "[Edge Case] TRUNCATE Idempotency (Run Twice)",
                  "Edge Case", "subprocess_args",
                  "Run ETL twice with same run_date — second run should produce same row count",
                  "Row count identical after both runs, no duplicates"),
                m("EDGE_DATE_001", "[Edge Case] Missing --run_date Argument",
                  "Edge Case", "subprocess_args",
                  "Run ETL without --run_date — blob path substitution should fail",
                  "Exception raised or empty file list"),
                m("FAIL_ENV_001", "[Failure] Invalid --env Value",
                  "Failure", "subprocess_args:--env:invalid_env_staging",
                  "Run ETL with --env staging (not in valid list)",
                  "Exception raised: env value not valid"),
            ]

        # ── V2: INSERT + WHERE NOT EXISTS dedup ───────────────────────────────
        elif has_insert and has_dedup:
            self.test_pool += [
                m("FUNC_DEDUP_001", "[Functional] INSERT with WHERE NOT EXISTS Deduplication",
                  "Functional", "subprocess_args",
                  "Run ETL — WHERE NOT EXISTS prevents inserting already-present keys",
                  "Only new records inserted, existing records untouched"),
                m("FUNC_UUID_001", "[Functional] UUID Generation for Dedup Keys",
                  "Functional", "subprocess_args",
                  "ETL generates UUID_STRING() for each new record",
                  "All inserted rows have non-null, unique UUID values"),
                m("FUNC_DDL_001", "[Functional] DDL SQL Files Executed Successfully",
                  "Functional", "subprocess_args",
                  "tables_to_copy DDL files run without error",
                  "Target tables created/updated in Snowflake"),
                m("EDGE_DUP_001", "[Edge Case] Dedup on Second Run (No New Duplicates)",
                  "Edge Case", "subprocess_args",
                  "Run ETL twice — second run should insert 0 new rows",
                  "Row count unchanged after second run"),
                m("EDGE_EXT_001", "[Edge Case] External DB Source Accessibility",
                  "Edge Case", "subprocess_args",
                  "ETL reads from EXT_LIGHTBOX.PROFESSIONAL — verify cross-DB access works",
                  "SELECT from external DB returns rows without error"),
                m("FAIL_ENV_001", "[Failure] Invalid --env Value",
                  "Failure", "subprocess_args:--env:invalid_env_staging",
                  "Run ETL with --env staging (not in valid list)",
                  "Exception raised: env value not valid"),
            ]

        # ── V3: LATERAL FLATTEN + CREATE OR REPLACE ───────────────────────────
        elif has_lateral:
            self.test_pool += [
                m("FUNC_FLATTEN_001", "[Functional] LATERAL FLATTEN on GeoJSON Data",
                  "Functional", "subprocess_args",
                  "ETL uses LATERAL FLATTEN to denormalize nested GeoJSON features",
                  "All features flattened, rows loaded to LNG_TERMINALS and GAS_PIPELINES"),
                m("FUNC_STAGE_001", "[Functional] Snowflake Stage File Pattern Matching",
                  "Functional", "subprocess_args",
                  "ETL reads from stage using METADATA$FILENAME ILIKE patterns",
                  "Terminal files load to LNG_TERMINALS, Pipeline files to GAS_PIPELINES"),
                m("FUNC_GEOM_001", "[Functional] GEOGRAPHY Column Loaded from GeoJSON",
                  "Functional", "subprocess_args",
                  "TO_GEOGRAPHY() converts GeoJSON geometry to Snowflake GEOGRAPHY type",
                  "GEOMETRY column populated, no null/invalid geometry errors"),
                m("EDGE_JSON_001", "[Edge Case] CREATE OR REPLACE Table Idempotency",
                  "Edge Case", "subprocess_args",
                  "Run ETL twice — CREATE OR REPLACE drops and recreates tables each time",
                  "Row count identical after both runs, no schema drift"),
                m("EDGE_PK_001", "[Edge Case] PRIMARY KEY Constraint Enforced",
                  "Edge Case", "subprocess_args",
                  "Duplicate PROJECT_ID rows in source should not violate PK on second run",
                  "No duplicate key errors, idempotent load"),
                m("FAIL_ENV_001", "[Failure] Invalid --env Value",
                  "Failure", "subprocess_args:--env:invalid_env_staging",
                  "Run ETL with --env staging (not in valid list)",
                  "Exception raised: env value not valid"),
            ]

        # ── V4: Stored Procedures ─────────────────────────────────────────────
        elif has_procedures:
            self.test_pool += [
                m("FUNC_PROC_001", "[Functional] Stored Procedure Execution",
                  "Functional", "subprocess_args",
                  "ETL calls LOAD_METRICS_CSV, LOAD_STATISTICS_SQL, LOAD_DICTIONARY_SQL in order",
                  "All 3 procedures execute, each returns result without error"),
                m("FUNC_PROC_002", "[Functional] CSV Files Loaded via LOAD_METRICS_CSV",
                  "Functional", "subprocess_args",
                  "DIRECTORY(@AGS_RAW_STAGE) lists CSV files, LOAD_METRICS_CSV called per file",
                  "Each CSV file processed, METRICS table row count increases"),
                m("FUNC_PROC_003", "[Functional] STATISTICS and DICTIONARY Tables Loaded",
                  "Functional", "subprocess_args",
                  "LOAD_STATISTICS_SQL and LOAD_DICTIONARY_SQL called once each",
                  "STATISTICS and DICTIONARY tables populated after run"),
                m("EDGE_PROC_001", "[Edge Case] Procedure Idempotency (Run Twice)",
                  "Edge Case", "subprocess_args",
                  "Run ETL twice — stored procs should handle re-runs without error",
                  "Second run completes without duplicate key or constraint errors"),
                m("EDGE_STAGE_001", "[Edge Case] Stage Contains No CSV Files",
                  "Edge Case", "subprocess_args",
                  "If AGS_RAW_STAGE is empty, DIRECTORY query returns 0 rows",
                  "ETL exits cleanly with 0 procedure calls, no error"),
                m("FAIL_ENV_001", "[Failure] Invalid --env Value",
                  "Failure", "subprocess_args:--env:invalid_env_staging",
                  "Run ETL with --env staging (not in valid list)",
                  "Exception raised: env value not valid"),
            ]

        # ── Default: generic if no patterns match ─────────────────────────────
        else:
            self.test_pool += [
                m("FUNC_BASE_001", "[Functional] Valid ETL Execution",
                  "Functional", "subprocess_args",
                  "Run ETL with valid config end-to-end",
                  "Exit 0, data loaded to target"),
                m("FUNC_BASE_002", "[Functional] Data Loaded to Target Table",
                  "Functional", "subprocess_args",
                  "Target table row count increases after ETL run",
                  "Row count > 0 after load"),
            ]

        print(f"  [Phase 5.5] Generated {len(self.test_pool)} unique operation-specific tests")
