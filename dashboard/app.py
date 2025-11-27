# dashboard/app.py
import streamlit as st
import requests
import json
import pandas as pd
from pathlib import Path
from db import init_db, save_history, load_history
import re

def extract_reason(raw):
    if not raw:
        return ""
    match = re.search(r"REASON=([A-Za-z0-9_-]+)", raw)
    return match.group(1) if match else ""

API_URL = "http://localhost:8001"

# ---------------------------------------------------------
# Streamlit Setup
# ---------------------------------------------------------
st.set_page_config(page_title="AKS Logs & RAG Diagnosis", layout="wide")
init_db()

st.title(" AKS Logs Browser & Diagnosis")

# ---------------------------------------------------------
# UI Styling (Azure + Soft Glassmorphism)
# ---------------------------------------------------------
st.markdown("""
<style>

body {
    background-color: #f3f2f1 !important;
    font-family: Segoe UI, sans-serif;
}

/* Global card */
.azure-card {
    background: #ffffff;
    padding: 18px 22px;
    border-radius: 8px;
    border: 1px solid #e1e1e1;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    margin-bottom: 18px;
}

/* Section header */
.azure-header {
    font-size: 20px !important;
    font-weight: 600 !important;
    color: #0078D4;
    margin-top: 15px;
    margin-bottom: 10px;
}

/* Metadata box */
.meta-box {
    background: #fafafa;
    border-left: 4px solid #0078D4;
    padding: 12px;
    border-radius: 6px;
    font-size: 13px;
    margin-top: 10px;
}

/* Diagnosis result box */
.diag-box {
    background: rgba(0, 0, 0, 0.55);
    border-left: 4px solid #4aa3ff;
    border-radius: 8px;
    padding: 14px;
    border: 1px solid rgba(255,255,255,0.08);
    backdrop-filter: blur(6px) saturate(140%);
    -webkit-backdrop-filter: blur(6px) saturate(140%);

    /* Typography same as .section-header */
    font-size: 20px !important;
    font-weight: 600 !important;

    /* But use WHITE text instead of blue */
    color: #ffffff !important;

    line-height: 1.4;
}


/* Buttons */
.stButton>button {
    background-color: #0078D4 !important;
    color: white !important;
    border-radius: 6px !important;
    height: 42px;
    border: none;
    font-size: 15px;
}

.stButton>button:hover {
    background-color: #005a9e !important;
}

/* Data editor header */
[data-testid="stDataFrame"] th {
    background-color: #f9f9f9 !important;
    color: #333 !important;
}

</style>
""", unsafe_allow_html=True)



# ---------------------------------------------------------
# Auto-refresh function
# ---------------------------------------------------------
def auto_refresh():
    st.session_state["_refresh"] = True
    


# ---------------------------------------------------------
# Sidebar Filters
# ---------------------------------------------------------
with st.sidebar:
    st.header("🔍 Filters")

    with st.expander("Basic Filters", expanded=True):
        ns_filter = st.text_input(
            "Namespace",
            value=st.session_state.get("ns_filter", ""),
            placeholder="e.g. kube-system",
            on_change=auto_refresh,
            key="ns_filter",
        )

        pod_filter = st.text_input(
            "Pod Name",
            value=st.session_state.get("pod_filter", ""),
            placeholder="Search pod",
            on_change=auto_refresh,
            key="pod_filter",
        )

        reason_filter = st.text_input(
            "Reason",
            value=st.session_state.get("reason_filter", ""),
            placeholder="Warning / FailedScheduling / BackOff",
            on_change=auto_refresh,
            key="reason_filter",
        )

    with st.expander("Advanced Query"):
        search_text = st.text_input(
            "Vector Search",
            value=st.session_state.get("search_text", ""),
            placeholder="Search log content using embeddings...",
            on_change=auto_refresh,
            key="search_text",
        )

    with st.expander("Sorting & Pagination"):
        sort_by = st.selectbox(
            "Sort By", ["start_ts", "namespace", "pod", "severity_hint", "id"],
            key="sort_by",
        )

        order = st.radio("Order", ["desc", "asc"], key="order", horizontal=True)

        limit = st.number_input(
            "Page size", min_value=10, max_value=500, value=100, step=10, key="limit"
        )

    if st.button("🔄 Clear Filters"):
        for k in ["ns_filter", "pod_filter", "reason_filter", "search_text"]:
            st.session_state[k] = ""
        st.rerun()


# ---------------------------------------------------------
# Fetch Logs
# ---------------------------------------------------------
def fetch_logs():
    params = {
        "namespace": st.session_state.get("ns_filter") or None,
        "pod": st.session_state.get("pod_filter") or None,
        "reason": st.session_state.get("reason_filter") or None,
        "q": st.session_state.get("search_text") or None,
        "sort_by": st.session_state.get("sort_by"),
        "order": st.session_state.get("order"),
        "limit": int(st.session_state.get("limit")),
        "offset": 0,
    }

    try:
        resp = requests.get(
            f"{API_URL}/logs",
            params={k: v for k, v in params.items() if v is not None},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"Failed to fetch logs: {e}")
        return {"count": 0, "items": []}


# ---------------------------------------------------------
# Load logs
# ---------------------------------------------------------
logs_resp = fetch_logs()

if logs_resp["count"] == 0:
    st.warning("No logs found. Adjust filters or ensure backend is running.")
    st.stop()

items = logs_resp["items"]


# ---------------------------------------------------------
# Convert logs
# ---------------------------------------------------------
rows = []
for it in items:
    meta = it.get("metadata", {}) or {}
    doc = it.get("document", "")
    preview = (doc or "").replace("\n", " ")[:200]

    rows.append({
        "Select": False,
        "id": it["id"],
        "timestamp": meta.get("start_ts") or meta.get("timestamp", ""),
        "namespace": meta.get("namespace", ""),
        "message": preview,
        "pod": meta.get("pod", ""),
        "node": meta.get("node", ""),
        "reason": meta.get("reason", ""),
        "severity_hint": meta.get("severity_hint", ""),
        "raw_doc": doc,
        "full_meta": meta,
    })


# ---------------------------------------------------------
# Display table
# ---------------------------------------------------------
st.subheader(f"Logs ({logs_resp['count']}) — Select a row to diagnose")

df = pd.DataFrame(rows)[[
    "Select", "id", "timestamp", "namespace",
    "message", "pod", "node", "reason", "severity_hint",
]]

table = st.data_editor(
    df,
    hide_index=True,
    height=360,
    width="stretch",
    column_config={"Select": st.column_config.CheckboxColumn(required=False)},
)

selected_rows = table[table["Select"] == True]

if selected_rows.empty:
    st.info("Select a log above to view details.")
    st.stop()

selected_id = selected_rows.iloc[0]["id"]
selected_row = next(r for r in rows if r["id"] == selected_id)


# ---------------------------------------------------------
# Selected Log Details
# ---------------------------------------------------------
st.markdown("### Selected Log")
st.code(selected_row["raw_doc"])

st.markdown("### Metadata")
st.json(selected_row["full_meta"])


# ---------------------------------------------------------
# Diagnose
# ---------------------------------------------------------
if st.button("Diagnose Selected Log", width="stretch"):
    with st.spinner("Running analysis…"):
        try:
            payload = {"chunk_id": selected_id}
            resp = requests.post(f"{API_URL}/diagnose", json=payload, timeout=180)
            resp.raise_for_status()
            data = resp.json()

            st.subheader("Diagnosis Result")
            st.markdown(data.get("diagnosis", "No diagnosis returned."))

            save_history(selected_id, data.get("diagnosis", ""))

        except Exception as e:
            st.error(f"Diagnosis failed: {e}")


# ---------------------------------------------------------
# Diagnosis History (NOW IN A DROPDOWN)
# ---------------------------------------------------------
st.markdown("---")

with st.expander("Show Diagnosis History", expanded=False):   # 🔥 NEW DROPDOWN
    history = load_history()

    if not history:
        st.info("No previous diagnosis found.")
    else:
        for rec in history:
            unique_key, chunk_id, diagnosis_text, _ = rec

            st.markdown(f"**Log ID:** {chunk_id}")
            st.code(
                diagnosis_text[:1200]
                + ("..." if len(diagnosis_text) > 1200 else "")
            )
            st.markdown("---")
