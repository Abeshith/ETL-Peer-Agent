ETL Test Case Agent - Azure to Snowflake Landing DB

Purpose:
The ETL Test Case Agent analyzes Python ETL code that loads data from Azure Blob to Snowflake Landing database. It validates code quality, generates test cases, and produces a comprehensive CSV report.

Project Structure:
├── src/
│   ├── agent.py              Main orchestrator
│   └── config.py             Configuration settings
├── phases/
│   ├── phase1_metadata.py    Metadata extraction (AST)
│   ├── phase2_azure.py       Azure logic validation
│   ├── phase3_snowflake.py   Snowflake logic validation
│   ├── phase4_resources.py   Resource validation (optional)
│   ├── phase5_etl_logic.py   ETL logic review (Groq LLM)
│   └── phase6_performance.py Performance review (Groq LLM)
├── utils/
│   ├── constants.py          Test definitions
│   ├── models.py             Data classes
│   └── report_generator.py   Report generation
├── test_samples/
│   └── sample_etl_load.py    Example ETL code
├── outputs/                  Generated reports
├── requirements.txt          Dependencies
└── .env.template            Environment variables template

Quick Start:

1. Install dependencies:
   pip install -r requirements.txt

2. Set up environment variables:
   Copy .env.template to .env and fill in your values
   export GROQ_API_KEY=your-key

3. Run analysis on your ETL file:
   python src/agent.py path/to/your/etl_file.py

4. Run with resource validation (requires Azure and Snowflake credentials):
   python src/agent.py path/to/your/etl_file.py --validate-resources

5. Find reports in outputs/ directory:
   - test_results_YYYYMMDD_HHMMSS.csv
   - test_analysis_YYYYMMDD_HHMMSS.json

Phase Breakdown:

Phase 1: Metadata Extraction (AST)
- Extracts Azure container name, Snowflake database/schema/table
- Validates targeting LANDING database (not MAIN)
- Tests: TC001-TC006
- Requires: None

Phase 2: Azure Logic Validation (AST + Regex)
- Validates container existence checks
- Validates file listing logic
- Validates error handling for Azure operations
- Tests: TC007-TC011
- Requires: None

Phase 3: Snowflake Logic Validation (AST + Regex)
- Validates Snowflake connection to LANDING
- Validates COPY INTO statement
- Validates audit log references
- Tests: TC012-TC017
- Requires: None

Phase 4: Environment Resource Validation (Optional)
- Checks Azure container actually exists
- Checks Snowflake database/schema/tables exist
- Safety check: ensures MAIN DB not targeted
- Tests: TC018-TC024
- Requires: --validate-resources flag, Azure and Snowflake credentials

Phase 5: ETL Logic Review (Groq LLM)
- Reviews duplicate prevention patterns
- Checks retry mechanism
- Validates audit logging
- Tests: TC025-TC029
- Requires: GROQ_API_KEY

Phase 6: Performance Review (Groq LLM)
- Checks for SELECT * usage
- Detects excessive DB calls
- Validates batch insert usage
- Tests: TC030-TC033
- Requires: GROQ_API_KEY

Output Format:

CSV Report (test_results_*.csv):
- Test_ID: TC001, TC002, etc.
- Test_Name: Description of test
- Phase: Phase1-6
- Status: PASS, FAIL, or SKIPPED
- Finding: What was found
- Recommendation: How to fix (if FAIL)
- Check_Type: Static, AST+Regex, Groq LLM, Resource
- Severity: Info, Medium, High, Critical
- Database_Target: LANDING.LND
- Stage2_Relevance: Critical, Important, Required
- Timestamp: ISO format datetime

JSON Report (test_analysis_*.json):
- Complete metadata extracted
- Phase summary (pass/fail/skip counts)
- Detailed test results with all fields
- Useful for automation/pipelines

Stage 2 Readiness:

Your ETL code is ready for Stage 2 transformation when:
- All Phase 1-3 tests PASS
- Phase 4 PASS (if enabled)
- Critical failures in Phase 5-6 resolved
- Audit logging properly implemented
- LANDING.LND tables properly created

Stage 2 will:
- Read from LANDING.LND.raw_data
- Transform data
- Write to STAGE.STG.raw_data
- Reference LANDING.LND.audit_log for lineage

Testing with Sample ETL:

1. Run analysis on sample:
   python src/agent.py test_samples/sample_etl_load.py

2. Expected results from sample:
   Phase 1: 6 tests (6 PASS)
   Phase 2: 5 tests (4 PASS, 1 FAIL - retry logic missing)
   Phase 3: 6 tests (5 PASS, 1 FAIL - no error handling)
   Phase 4: 7 tests (SKIPPED - needs credentials)
   Phase 5: 5 tests (SKIPPED - needs Groq API key)
   Phase 6: 4 tests (SKIPPED - needs Groq API key)

3. Review outputs/test_results_*.csv for detailed findings

Troubleshooting:

Issue: "ModuleNotFoundError: No module named 'groq'"
Fix: pip install groq

Issue: Phase 4 tests fail with connection errors
Fix: Ensure Azure and Snowflake credentials are correct in .env

Issue: Phase 5/6 all SKIPPED
Fix: Set GROQ_API_KEY environment variable with your Groq API key

Issue: Target database detected as wrong
Fix: Ensure your ETL code explicitly uses database="LANDING"

Configuration:

src/config.py controls:
- LANDING_DB = "LANDING"
- LANDING_SCHEMA = "LND"
- LANDING_TABLE = "raw_data"
- AUDIT_TABLE = "audit_log"
- GROQ_MODEL = "mixtral-8x7b-32768"
- VALIDATE_RESOURCES = False (set to True to enable Phase 4)

Environment Variables (.env):
- GROQ_API_KEY: Your Groq API key
- SNOWFLAKE_ACCOUNT: Snowflake account identifier
- SNOWFLAKE_USER: Snowflake username
- SNOWFLAKE_PASSWORD: Snowflake password
- VALIDATE_RESOURCES: true/false
- ENVIRONMENT: development/staging/production

Next Steps:

1. Test with sample_etl_load.py
2. Run against your actual ETL code
3. Fix FAIL items in Phases 1-3
4. Enable Phase 4 with real credentials to validate resources
5. Set up Groq API key for Phase 5-6 analysis
6. Use the CSV report for Stage 2 readiness checklist
7. Proceed with Stage 2 transformation when all critical items PASS

For questions or issues, review Phase output and findings in the CSV report.

Add TODO:
Future AST + Rules + LLM Enhancement
