import streamlit as st
import requests
import time

API = "http://127.0.0.1:8000"

st.set_page_config(page_title="Langie Invoice Agent", layout="wide")
st.title("🤖 Langie: 12-Stage Invoice Agent")

if "logs" not in st.session_state: st.session_state.logs = []
if "thread_id" not in st.session_state: st.session_state.thread_id = None
if "status" not in st.session_state: st.session_state.status = "IDLE"

# --- SIDEBAR ---
with st.sidebar:
    st.header("Upload Invoice")
    scenario = st.select_box("Choose Scenario", ["good_invoice.pdf", "bad_invoice.pdf"])
    if st.button("🚀 Process Invoice"):
        st.session_state.logs = []
        with st.spinner("Running Stages 1-5..."):
            res = requests.post(f"{API}/start", json= {"filename": scenario}).json()
            st.session_state.thread_id = res["thread_id"]
            st.session_state.logs = res["logs"]
            st.session_state.status = res["status"]
            st.session_state.data = res.get("state", {})

# --- MAIN ---
c1, c2 = st.columns([2, 1])

with c1:
    st.subheader("Workflow Execution")
    for log in st.session_state.logs:
        if 'STAGE' in log: st.info(log)
        else: st.write(log)

with c2:
    st.subheader("Agent Status")
    st.metric("Current Status", st.session_state.status)

    if st.session_state.status == "PAUSED_HTML":
        st.error("Action Required: Low Match Score")

        # Show comparison
        data = st.session_state.data
        inv_amt = data.get("extracted_data", {}).get("amount", 0)
        po_amt = data.get("po_data", {}).get("amount", 0) if data.get("po_data") else 0

        st.write(f"**Invoice:** ${inv_amt}")
        st.write(f"**PO:** ${po_amt}")
        st.write(f"**Score:** {data.get('match_score')}")

        with st.form("decison"):
            note = st.text_input("Reason")
            submitted = st.form_submit_button("Approve Variance")
            if submitted:
                requests.post(f"{API}/decision", json={
                    "thread_id": st.session_state.thread_id,
                    "action": "ACCEPT",
                    "note": note
                })
                st.success("Decision Sent! Please re-run to see final logs.")
                st.session_state.status = "RESUMED"
