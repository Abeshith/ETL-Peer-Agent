import sys
import tempfile
import shutil
import threading
import queue
import re
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

sys.path.append(str(Path(__file__).parent.absolute()))

st.set_page_config(
    page_title="Smart Review - ETL | CGI",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CGI Visual Identity & Design System CSS ──────────────────────────────────
st.markdown("""
<style>
/* ── CGI Brand Color Tokens ─────────────────────────────────────────── */
:root {
  --cgi-purple: #5236AB;
  --cgi-purple-900: #200A58;
  --cgi-purple-800: #2D1E5E;
  --cgi-purple-700: #3A2679;
  --cgi-purple-500: #755EBC;
  --cgi-purple-400: #9E83F5;
  --cgi-purple-300: #AFA3D8;
  --cgi-purple-200: #CBC3E6;
  --cgi-purple-100: #E6E3F3;
  --cgi-purple-50: #F2F1F9;

  --cgi-red: #E31937;
  --cgi-red-900: #600A17;
  --cgi-red-800: #7D0D1E;
  --cgi-red-700: #A21127;
  --cgi-red-600: #CF1632;
  --cgi-red-400: #E9465F;
  --cgi-red-300: #ED6479;
  --cgi-red-200: #F395A3;
  --cgi-red-100: #F7B7C1;
  --cgi-red-50: #FCE8EB;

  --cgi-gray-900: #151515;
  --cgi-gray-800: #1C1C1C;
  --cgi-gray-700: #242424;
  --cgi-gray-600: #2E2E2E;
  --cgi-gray: #333333;
  --cgi-gray-400: #5C5C5C;
  --cgi-gray-300: #767676;
  --cgi-gray-200: #A8A8A8;
  --cgi-gray-100: #C0C0C0;
  --cgi-gray-50: #EFEFEF;

  --cgi-white: #FFFFFF;
  --cgi-white-700: #EBEEF2;
  --cgi-white-600: #F0F3F6;
  --cgi-white-500: #F2F4F7;
  --cgi-white-400: #F5F7F9;
  --cgi-white-300: #F6F8F9;
  --cgi-white-200: #F9FAFB;
  --cgi-white-100: #FCFDFD;

  --cgi-success: #1AB977;
  --cgi-success-900: #0B4E32;
  --cgi-success-800: #0E6641;
  --cgi-success-700: #128354;
  --cgi-success-600: #18A86C;
  --cgi-success-400: #48C792;
  --cgi-success-200: #96DFC0;
  --cgi-success-100: #B8E9D5;
  --cgi-success-50: #E8F8F1;

  --cgi-warning: #F1A425;
  --cgi-warning-900: #654510;
  --cgi-warning-800: #855A14;
  --cgi-warning-700: #AB741A;
  --cgi-warning-600: #DB9522;
  --cgi-warning-200: #F9D59B;
  --cgi-warning-100: #FBE3BB;
  --cgi-warning-50: #FEF6E9;

  --cgi-error: #B00020;
  --cgi-error-900: #4A000D;
  --cgi-error-800: #610012;
  --cgi-error-700: #7D0017;
  --cgi-error-600: #A0001D;
  --cgi-error-200: #DB8A98;
  --cgi-error-100: #E7B0BA;
  --cgi-error-50: #F7E6E9;

  --cgi-magenta: #A82465;
  --cgi-gradient-h: linear-gradient(90deg, #E31937 0%, #A82465 60%, #5236AB 100%);
}

/* Global Font & Body */
html, body, [class*="css"] {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  color: var(--cgi-gray);
}

/* ── Branded Top Gradient Bar ────────────────────────────────────────── */
.cgi-brand-bar {
  height: 6px;
  width: 100%;
  background: var(--cgi-gradient-h);
  border-radius: 3px;
  margin-bottom: 1.2rem;
}

/* ── Standalone CGI Logo ─────────────────────────────────────────────── */
.cgi-standalone-logo-container {
  display: flex;
  align-items: center;
  margin-bottom: 0.85rem;
  padding: 0.1rem 0;
}

.cgi-standalone-logo {
  background: var(--cgi-red);
  color: #FFFFFF;
  font-weight: 900;
  font-size: 1.35rem;
  letter-spacing: 0.12em;
  padding: 0.4rem 0.95rem;
  border-radius: 6px;
  line-height: 1;
  box-shadow: 0 2px 5px rgba(227, 25, 55, 0.28);
  display: inline-block;
}

/* ── Branded Header Card ─────────────────────────────────────────────── */
.cgi-header-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.1rem 1.5rem;
  background: linear-gradient(135deg, #FFFFFF 0%, #F9FAFB 100%);
  border: 1px solid var(--cgi-white-700);
  border-left: 6px solid var(--cgi-purple);
  border-radius: 8px;
  margin-bottom: 1.5rem;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.cgi-header-title-text h1 {
  margin: 0 !important;
  padding: 0 !important;
  font-size: 1.65rem !important;
  font-weight: 700 !important;
  color: var(--cgi-purple-900) !important;
  line-height: 1.2 !important;
}

.cgi-header-title-text p {
  margin: 0.25rem 0 0 0 !important;
  font-size: 0.9rem !important;
  color: var(--cgi-gray-400) !important;
  font-weight: 500 !important;
}

.cgi-stage-badge {
  background: var(--cgi-purple-50);
  color: var(--cgi-purple);
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 0.4rem 0.85rem;
  border-radius: 20px;
  border: 1.5px solid var(--cgi-purple-200);
  text-transform: uppercase;
}

/* ── Typography & Headings ───────────────────────────────────────────── */
h1, h2, h3, [data-testid="stHeadingWithActionElements"] h1, [data-testid="stHeadingWithActionElements"] h2, [data-testid="stHeadingWithActionElements"] h3 {
  color: var(--cgi-purple-900) !important;
  font-weight: 700 !important;
  letter-spacing: -0.01em !important;
}

h4, h5, h6 {
  color: var(--cgi-gray-700) !important;
  font-weight: 600 !important;
}

/* ── Sidebar Theming ─────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
  background-color: var(--cgi-white-200) !important;
  border-right: 1px solid var(--cgi-white-700) !important;
}

[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
  color: var(--cgi-purple-900) !important;
  border-bottom: 2px solid var(--cgi-purple-100);
  padding-bottom: 0.4rem;
  margin-top: 0.6rem;
}

[data-testid="stSidebar"] label {
  color: var(--cgi-gray-700) !important;
  font-weight: 600 !important;
}

/* ── Main CTA Primary Button (CGI Purple) ────────────────────────────── */
button[kind="primary"], .stButton > button[kind="primary"], div[data-testid="stButton"] > button[kind="primary"] {
  background-color: var(--cgi-purple) !important;
  color: #FFFFFF !important;
  border: none !important;
  border-radius: 6px !important;
  font-weight: 700 !important;
  font-size: 0.95rem !important;
  padding: 0.65rem 1.4rem !important;
  letter-spacing: 0.02em !important;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
  box-shadow: 0 2px 8px rgba(82, 54, 171, 0.3) !important;
}

button[kind="primary"]:hover, .stButton > button[kind="primary"]:hover, div[data-testid="stButton"] > button[kind="primary"]:hover {
  background-color: var(--cgi-purple-700) !important;
  box-shadow: 0 4px 14px rgba(82, 54, 171, 0.42) !important;
  transform: translateY(-1px);
}

button[kind="primary"]:active, .stButton > button[kind="primary"]:active {
  background-color: var(--cgi-purple-800) !important;
  transform: translateY(0);
}

/* ── Secondary / Download Buttons (CGI Outline Style) ────────────────── */
button[kind="secondary"], .stDownloadButton > button, div[data-testid="stDownloadButton"] > button {
  background-color: #FFFFFF !important;
  color: var(--cgi-purple) !important;
  border: 1.5px solid var(--cgi-purple-200) !important;
  border-radius: 6px !important;
  font-weight: 600 !important;
  padding: 0.55rem 1.1rem !important;
  transition: all 0.2s ease !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05) !important;
}

button[kind="secondary"]:hover, .stDownloadButton > button:hover, div[data-testid="stDownloadButton"] > button:hover {
  background-color: var(--cgi-purple-50) !important;
  border-color: var(--cgi-purple) !important;
  color: var(--cgi-purple-900) !important;
  box-shadow: 0 3px 8px rgba(82, 54, 171, 0.16) !important;
}

/* ── Form Inputs, Selects & TextAreas ────────────────────────────────── */
div[data-baseweb="input"] input, div[data-baseweb="select"] {
  border-color: var(--cgi-purple-200) !important;
  color: var(--cgi-gray-900) !important;
  border-radius: 6px !important;
}

div[data-baseweb="input"]:focus-within, div[data-baseweb="select"]:focus-within {
  border-color: var(--cgi-purple) !important;
  box-shadow: 0 0 0 2px rgba(82, 54, 171, 0.2) !important;
}

div[data-baseweb="textarea"] textarea {
  border-color: var(--cgi-purple-200) !important;
  color: var(--cgi-gray-900) !important;
  border-radius: 6px !important;
}

div[data-baseweb="textarea"]:focus-within {
  border-color: var(--cgi-purple) !important;
  box-shadow: 0 0 0 2px rgba(82, 54, 171, 0.2) !important;
}

/* File Uploader styling */
div[data-testid="stFileUploader"] section {
  border: 1.5px dashed var(--cgi-purple-200) !important;
  background-color: var(--cgi-purple-50) !important;
  border-radius: 8px !important;
  transition: all 0.2s ease !important;
}

div[data-testid="stFileUploader"] section:hover {
  border-color: var(--cgi-purple) !important;
  background-color: #ECE9F7 !important;
}

/* ── Checkbox & Radio active states ──────────────────────────────────── */
span[data-baseweb="checkbox"] span:first-child {
  border-color: var(--cgi-purple-300) !important;
}

input[type="checkbox"]:checked + span:first-child {
  background-color: var(--cgi-purple) !important;
  border-color: var(--cgi-purple) !important;
}

/* ── Streamlit Status Callouts / Alert Boxes ─────────────────────────── */
div[data-testid="stAlert"] {
  border-radius: 6px !important;
  border-width: 1px 1px 1px 5px !important;
  border-style: solid !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03) !important;
}

div[data-testid="stAlert"]:has([data-testid="stNotificationContentInfo"]) {
  background-color: var(--cgi-purple-50) !important;
  border-color: var(--cgi-purple-200) var(--cgi-purple-200) var(--cgi-purple-200) var(--cgi-purple) !important;
  color: var(--cgi-purple-900) !important;
}

div[data-testid="stAlert"]:has([data-testid="stNotificationContentSuccess"]) {
  background-color: var(--cgi-success-50) !important;
  border-color: var(--cgi-success-100) var(--cgi-success-100) var(--cgi-success-100) var(--cgi-success) !important;
  color: var(--cgi-success-900) !important;
}

div[data-testid="stAlert"]:has([data-testid="stNotificationContentWarning"]) {
  background-color: var(--cgi-warning-50) !important;
  border-color: var(--cgi-warning-100) var(--cgi-warning-100) var(--cgi-warning-100) var(--cgi-warning) !important;
  color: var(--cgi-warning-900) !important;
}

div[data-testid="stAlert"]:has([data-testid="stNotificationContentError"]) {
  background-color: var(--cgi-error-50) !important;
  border-color: var(--cgi-error-100) var(--cgi-error-100) var(--cgi-error-100) var(--cgi-error) !important;
  color: var(--cgi-error-900) !important;
}

/* ── Metric Box Styling ──────────────────────────────────────────────── */
div[data-testid="stMetric"] {
  background-color: #FFFFFF !important;
  border: 1px solid var(--cgi-white-700) !important;
  border-radius: 8px !important;
  padding: 0.9rem 1.15rem !important;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.03) !important;
  border-top: 4px solid var(--cgi-purple) !important;
}

div[data-testid="stMetric"] label {
  color: var(--cgi-gray-400) !important;
  font-size: 0.82rem !important;
  font-weight: 700 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.05em !important;
}

div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
  color: var(--cgi-purple-900) !important;
  font-weight: 800 !important;
  font-size: 1.85rem !important;
}

/* ── Expander Cards ──────────────────────────────────────────────────── */
div[data-testid="stExpander"] {
  border: 1px solid var(--cgi-white-700) !important;
  border-radius: 8px !important;
  background-color: #FFFFFF !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02) !important;
}

/* ── Custom Phase Metric Card ────────────────────────────────────────── */
.cgi-phase-card {
  background: #FFFFFF;
  border: 1px solid var(--cgi-white-700);
  border-radius: 8px;
  padding: 0.85rem 0.9rem;
  box-shadow: 0 1px 4px rgba(0,0,0,0.03);
  text-align: center;
  margin-bottom: 0.5rem;
}
.cgi-phase-card.pass {
  border-top: 3px solid var(--cgi-success);
}
.cgi-phase-card.fail {
  border-top: 3px solid var(--cgi-error);
}
.cgi-phase-card.skipped {
  border-top: 3px solid var(--cgi-warning);
}
.cgi-phase-title {
  font-size: 0.82rem;
  font-weight: 700;
  color: var(--cgi-purple-900);
  margin-bottom: 0.4rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cgi-phase-counts {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--cgi-gray);
}
.cgi-p-count { color: var(--cgi-success-800); }
.cgi-f-count { color: var(--cgi-error); }
.cgi-s-count { color: var(--cgi-warning-800); }
</style>
""", unsafe_allow_html=True)

# ── Top Brand Gradient Bar & Standalone Logo & Header ────────────────────────
st.markdown("""
<div class="cgi-brand-bar"></div>
<div class="cgi-standalone-logo-container">
  <div class="cgi-standalone-logo">CGI</div>
</div>
<div class="cgi-header-banner">
  <div class="cgi-header-title-text">
    <h1>Smart Review - ETL</h1>
    <p>Azure Blob → Snowflake Landing DB Verification Suite</p>
  </div>
  <div class="cgi-stage-badge">Stage 1 Validator</div>
</div>
""", unsafe_allow_html=True)

# ── Load business logic blocks from file ──────────────────────────────────────
_BIZ_LOGIC_FILE = Path(__file__).parent / "Business Logic.txt"
_biz_blocks: dict[str, list[str]] = {}

if _BIZ_LOGIC_FILE.exists():
    raw = _BIZ_LOGIC_FILE.read_text(encoding="utf-8")
    # Split on blank lines — each block is one ETL version's rules
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw) if b.strip()]
    for i, block in enumerate(blocks, 1):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if lines:
            _biz_blocks[f"Version {i}"] = lines

# ── Sidebar: inputs ───────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="display:flex; align-items:center; gap:0.6rem; margin-bottom: 0.6rem;">
      <span style="background:var(--cgi-purple); width:4px; height:18px; border-radius:2px; display:inline-block;"></span>
      <h3 style="margin:0; font-size:1.15rem; color:var(--cgi-purple-900);">Configuration</h3>
    </div>
    """, unsafe_allow_html=True)

    etl_file = st.file_uploader("ETL Python File (.py)", type=["py"])
    config_file = st.file_uploader("Config File (.yml / .yaml)", type=["yml", "yaml"])

    run_date = st.text_input("Run Date (optional)", placeholder="YYYYMMDD",
                             help="Leave blank to skip --run_date argument")

    validate_resources = st.checkbox("Validate Resources (Phase 4 + 7 + 8)",
                                     help="Requires Azure and Snowflake credentials in .env")

    st.markdown("""
    <div style="display:flex; align-items:center; gap:0.6rem; margin-top: 1rem; margin-bottom: 0.6rem;">
      <span style="background:var(--cgi-purple); width:4px; height:18px; border-radius:2px; display:inline-block;"></span>
      <h3 style="margin:0; font-size:1.15rem; color:var(--cgi-purple-900);">Business Logic Rules</h3>
    </div>
    """, unsafe_allow_html=True)

    rules_text = st.text_area(
        "Rules (one per line)",
        value="",
        height=180,
        help="Enter business rules to evaluate against the ETL run",
    )

    run_btn = st.button("▶ Run Validation", type="primary", use_container_width=True)

# ── Main area ─────────────────────────────────────────────────────────────────
if not run_btn:
    st.info("Upload your ETL Python file and configuration YAML in the left panel, then click **▶ Run Validation** to begin the verification suite.")
    st.stop()

if not etl_file:
    st.error("Please upload an ETL Python file.")
    st.stop()
if not config_file:
    st.error("Please upload a Config file.")
    st.stop()

# Auto-extract environment key from config file
env_key = None
try:
    cfg_bytes = config_file.read()
    parsed = yaml.safe_load(cfg_bytes)
    if isinstance(parsed, dict):
        env_options = [k for k, v in parsed.items() if isinstance(v, dict)]
        if env_options:
            env_key = env_options[0]  # Use first environment key
        else:
            st.error("Config file does not contain any valid environment keys.")
            st.stop()
    else:
        st.error("Config file is not a valid YAML dictionary.")
        st.stop()
except Exception as e:
    st.error(f"Failed to parse config file: {e}")
    st.stop()

st.info(f"🔧 Using environment: **{env_key}**")

# Parse business rules from textbox
business_rules = [r.strip() for r in rules_text.splitlines() if r.strip()] if rules_text else []

# Write uploaded files to a temp directory inside test_samples/ so relative imports resolve
tmp_dir = Path(tempfile.mkdtemp(dir=Path(__file__).parent / "test_samples"))
etl_path = tmp_dir / etl_file.name
config_path = tmp_dir / "config.yml"  # Always write as config.yml (V1 expects this exact name)
etl_path.write_bytes(etl_file.read())
config_path.write_bytes(cfg_bytes)  # Use cfg_bytes already read above

# Symlink/copy utils and src packages into tmp_dir so ETL imports resolve
project_root = Path(__file__).parent
for pkg in ["utils", "src"]:
    src_pkg = project_root / pkg
    dst_pkg = tmp_dir / pkg
    if src_pkg.exists() and not dst_pkg.exists():
        try:
            dst_pkg.symlink_to(src_pkg.resolve())
        except Exception:
            shutil.copytree(str(src_pkg), str(dst_pkg))

# Copy .env so credentials are available inside tmp_dir
env_src = project_root / ".env"
if env_src.exists():
    shutil.copy(env_src, tmp_dir / ".env")

st.markdown("<h3 style='color:var(--cgi-purple-900);'>Validation Progress</h3>", unsafe_allow_html=True)

# ── Run pipeline in a background thread ───────────────────────────────────────
result_queue: queue.Queue = queue.Queue()


def _run(etl_str, env, rdate, val_res, rules, q):
    import io
    import contextlib
    from run_validated_etl import run_validated_etl
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            results, csv_file, json_file = run_validated_etl(
                etl_str,
                validate_resources=val_res,
                run_date=rdate or None,
                env=env,
                business_rules=rules,
            )
        q.put(("ok", results, csv_file, json_file, buf.getvalue()))
    except Exception as exc:
        import traceback
        q.put(("err", str(exc), traceback.format_exc(), buf.getvalue()))


thread = threading.Thread(
    target=_run,
    args=(str(etl_path), env_key, run_date, validate_resources, business_rules, result_queue),
    daemon=True,
)
thread.start()

with st.spinner("Executing ETL Validation Suite…"):
    thread.join(timeout=700)

if result_queue.empty():
    st.error("Pipeline timed out after 700 seconds.")
    st.stop()

payload = result_queue.get()

if payload[0] == "err":
    _, err_msg, tb, stdout = payload
    st.error(f"Pipeline error: {err_msg}")
    with st.expander("Traceback"):
        st.code(tb)
    if stdout:
        with st.expander("Console output"):
            st.code(stdout)
    st.stop()

_, all_results, csv_file, json_file, stdout = payload

# ── Console log ───────────────────────────────────────────────────────────────
with st.expander("Console Output Log", expanded=False):
    st.code(stdout or "(no output)")

# ── Summary metrics ───────────────────────────────────────────────────────────
passed  = sum(1 for r in all_results if r.status == "PASS")
failed  = sum(1 for r in all_results if r.status == "FAIL")
skipped = sum(1 for r in all_results if r.status == "SKIPPED")
total   = len(all_results)
active  = total - skipped

st.markdown("<h3 style='color:var(--cgi-purple-900); margin-top:1rem;'>Executive Summary</h3>", unsafe_allow_html=True)
c1, c2, c3, c4 = st.columns(4)

with c1:
    st.markdown(f"""
    <div style="background:#FFFFFF; border:1px solid #EBEEF2; border-top:4px solid #5236AB; border-radius:8px; padding:1rem; box-shadow:0 2px 4px rgba(0,0,0,0.03);">
      <div style="color:#767676; font-size:0.8rem; font-weight:700; text-transform:uppercase; letter-spacing:0.04em;">Total Tests</div>
      <div style="color:#5236AB; font-size:2rem; font-weight:800; margin-top:0.2rem;">{total}</div>
    </div>
    """, unsafe_allow_html=True)

with c2:
    st.markdown(f"""
    <div style="background:#FFFFFF; border:1px solid #EBEEF2; border-top:4px solid #1AB977; border-radius:8px; padding:1rem; box-shadow:0 2px 4px rgba(0,0,0,0.03);">
      <div style="color:#767676; font-size:0.8rem; font-weight:700; text-transform:uppercase; letter-spacing:0.04em;">Passed</div>
      <div style="color:#0E6641; font-size:2rem; font-weight:800; margin-top:0.2rem;">{passed}</div>
    </div>
    """, unsafe_allow_html=True)

with c3:
    st.markdown(f"""
    <div style="background:#FFFFFF; border:1px solid #EBEEF2; border-top:4px solid #B00020; border-radius:8px; padding:1rem; box-shadow:0 2px 4px rgba(0,0,0,0.03);">
      <div style="color:#767676; font-size:0.8rem; font-weight:700; text-transform:uppercase; letter-spacing:0.04em;">Failed</div>
      <div style="color:#B00020; font-size:2rem; font-weight:800; margin-top:0.2rem;">{failed}</div>
    </div>
    """, unsafe_allow_html=True)

with c4:
    rate_str = f"{passed/active*100:.1f}%" if active else "N/A"
    st.markdown(f"""
    <div style="background:#FFFFFF; border:1px solid #EBEEF2; border-top:4px solid #755EBC; border-radius:8px; padding:1rem; box-shadow:0 2px 4px rgba(0,0,0,0.03);">
      <div style="color:#767676; font-size:0.8rem; font-weight:700; text-transform:uppercase; letter-spacing:0.04em;">Pass Rate</div>
      <div style="color:#200A58; font-size:2rem; font-weight:800; margin-top:0.2rem;">{rate_str}</div>
    </div>
    """, unsafe_allow_html=True)

# ── Phase breakdown ───────────────────────────────────────────────────────────
st.markdown("<h3 style='color:var(--cgi-purple-900); margin-top:1.5rem;'>Phase Breakdown</h3>", unsafe_allow_html=True)
phases: dict = {}
for r in all_results:
    phases.setdefault(r.phase, {"PASS": 0, "FAIL": 0, "SKIPPED": 0})
    phases[r.phase][r.status] = phases[r.phase].get(r.status, 0) + 1

if phases:
    cols = st.columns(len(phases))
    for col, (phase, counts) in zip(cols, phases.items()):
        status_cls = "fail" if counts["FAIL"] > 0 else ("pass" if counts["PASS"] > 0 else "skipped")
        with col:
            st.markdown(f"""
            <div class="cgi-phase-card {status_cls}">
              <div class="cgi-phase-title" title="{phase}">{phase}</div>
              <div class="cgi-phase-counts">
                <span class="cgi-p-count">{counts['PASS']}P</span> / 
                <span class="cgi-f-count">{counts['FAIL']}F</span> / 
                <span class="cgi-s-count">{counts['SKIPPED']}S</span>
              </div>
            </div>
            """, unsafe_allow_html=True)

# ── Business rules results ────────────────────────────────────────────────────
biz_results = [r for r in all_results if r.check_type == "Business Rule"]
if biz_results:
    st.markdown("<h3 style='color:var(--cgi-purple-900); margin-top:1.5rem;'>Business Rule Verification</h3>", unsafe_allow_html=True)
    biz_df = pd.DataFrame([{
        "Rule":           r.test_name.replace("Business Rule: ", ""),
        "Status":         r.status,
        "Finding":        r.finding,
        "Recommendation": r.recommendation,
    } for r in biz_results])

    def _biz_colour(val):
        if val == "PASS":
            return "background-color: #E8F8F1; color: #0B4E32; font-weight: 700; border-radius: 4px; padding: 2px 6px;"
        elif val == "FAIL":
            return "background-color: #F7E6E9; color: #610012; font-weight: 700; border-radius: 4px; padding: 2px 6px;"
        elif val == "SKIPPED":
            return "background-color: #FEF6E9; color: #654510; font-weight: 700; border-radius: 4px; padding: 2px 6px;"
        return ""

    st.dataframe(biz_df.style.map(_biz_colour, subset=["Status"]),
                 use_container_width=True, hide_index=True)
elif business_rules:
    st.warning("Business rules were provided but no results were returned — check console output.")

# ── Full results table ────────────────────────────────────────────────────────
st.markdown("<h3 style='color:var(--cgi-purple-900); margin-top:1.5rem;'>All Test Results</h3>", unsafe_allow_html=True)

display_results = [r for r in all_results if r.status != "GENERATED"]
df = pd.DataFrame([{
    "Test ID":        r.test_id,
    "Phase":          r.phase,
    "Test Name":      r.test_name,
    "Status":         r.status,
    "Severity":       r.severity,
    "Finding":        r.finding,
    "Recommendation": r.recommendation,
} for r in display_results])

status_filter = st.multiselect(
    "Filter by Status", ["PASS", "FAIL", "SKIPPED"],
    default=["PASS", "FAIL", "SKIPPED"],
)
if status_filter:
    df = df[df["Status"].isin(status_filter)]


def _colour(val):
    if val == "PASS":
        return "background-color: #E8F8F1; color: #0B4E32; font-weight: 700; border-radius: 4px; padding: 2px 6px;"
    elif val == "FAIL":
        return "background-color: #F7E6E9; color: #610012; font-weight: 700; border-radius: 4px; padding: 2px 6px;"
    elif val == "SKIPPED":
        return "background-color: #FEF6E9; color: #654510; font-weight: 700; border-radius: 4px; padding: 2px 6px;"
    return ""


st.dataframe(df.style.map(_colour, subset=["Status"]), use_container_width=True, hide_index=True)

# ── Downloads ─────────────────────────────────────────────────────────────────
st.markdown("<h3 style='color:var(--cgi-purple-900); margin-top:1.5rem;'>Export Reports</h3>", unsafe_allow_html=True)
d1, d2, d3 = st.columns(3)

csv_path = Path(csv_file)
if csv_path.exists():
    d1.download_button(
        "📥 Download CSV Report",
        data=csv_path.read_bytes(),
        file_name=csv_path.name,
        mime="text/csv",
        use_container_width=True,
    )

json_path = Path(json_file)
if json_path.exists():
    d2.download_button(
        "📥 Download JSON Report",
        data=json_path.read_bytes(),
        file_name=json_path.name,
        mime="application/json",
        use_container_width=True,
    )

# ── Excel download (if generated) ─────────────────────────────────────────────
xlsx_path = csv_path.with_suffix(".xlsx")
if not xlsx_path.exists():
    xlsx_path = Path(str(csv_file).replace(".csv", ".xlsx"))
if xlsx_path.exists():
    d3.download_button(
        "📥 Download Excel Report",
        data=xlsx_path.read_bytes(),
        file_name=xlsx_path.name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
