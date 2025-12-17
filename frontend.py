import streamlit as st
import requests
import time

# Configuration
API_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="Langie: Invoice Agent", layout="wide", page_icon="🧾")

# --- CUSTOM CSS FOR "VIBE" ---
st.markdown("""
    <style>
    .stAlert { border-radius: 10px; }
    .stButton>button { width: 100%; border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)

st.title("🤖 Langie: Autonomous Invoice Agent")
st.markdown("### 12-Stage Workflow with HITL & MCP")

# --- SESSION STATE INITIALIZATION ---
if "logs" not in st.session_state:
    st.session_state.logs = []
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "status" not in st.session_state:
    st.session_state.status = "IDLE"
if "data" not in st.session_state:
    st.session_state.data = {}

# --- SIDEBAR: INPUT ---
with st.sidebar:
    st.header("1. Upload Invoice")
    st.info("Select a simulated file to test dynamic tool selection.")

    scenario = st.selectbox(
        "Choose Invoice File", 
        ["good_invoice.pdf", "bad_invoice.pdf", "receipt.png"],
        help="Select 'receipt.png' to test Google Vision tool selection."
    )

    st.divider()
    
    if st.button("🚀 Start Processing", type="primary"):
        # Reset State
        st.session_state.logs = []
        st.session_state.status = "RUNNING"
        st.session_state.data = {}

        with st.spinner("Initializing Agent & MCP Servers..."):
            try:
                # Call /start endpoint
                resp = requests.post(f"{API_URL}/start", json={"filename": scenario})

                if resp.status_code == 200:
                    res_json = resp.json()
                    st.session_state.thread_id = res_json["thread_id"]
                    st.session_state.logs = res_json["logs"]
                    st.session_state.status = res_json["status"]
                    st.session_state.data = res_json.get("state", {})
                else:
                    st.error(f"API Error: {resp.text}")

            except Exception as e:
                st.error(f"Failed to connect to backend: {e}")

# --- MAIN DASHBOARD ---
col1, col2 = st.columns([1.5, 1])

# LEFT COLUMN: LIVE LOGS
with col1:
    st.subheader("📜 Workflow Execution Log")
    
    # Create a container for logs
    log_container = st.container(height=500, border=True)

    # Render Logs with Colors based on Keywords
    for log in st.session_state.logs:
        if "STAGE" in log:
            log_container.markdown(f"**{log}**")
        elif "PAUSED" in log:
            log_container.warning(log)
        elif "Error" in log or "REJECTED" in log:
            log_container.error(log)
        else:
            log_container.text(f"  {log}")

# RIGHT COLUMN: STATE & INTERACTION
with col2:
    st.subheader("⚙️ Agent State")

    flags = st.session_state.data.get("flags", [])
    if flags:
        st.error(f"🚩 **RISK DETECTED:** {', '.join(flags)}")
    
    # Status Badge
    status_color = "green" if st.session_state.status in ["COMPLETED", "SUCCESS"] else "orange"
    st.markdown(f"**Status:** :{status_color}[{st.session_state.status}]")

    # --- HITL INTERFACE (Paused State) ---
    if st.session_state.status == "PAUSED_HITL":
        st.divider()
        st.warning("🛑 **Action Required: Stage 6 (Checkpoint)**")
        st.write("The agent detected a discrepancy in Stage 5.")
        
        # Display Data for Decision
        curr_data = st.session_state.data
        inv_amt = curr_data.get("extracted_data", {}).get("amount", "N/A")
        
        # Safe access to PO data
        po_data = curr_data.get("po_data")
        po_amt = po_data.get("amount", "N/A") if po_data else "No PO"

        c1, c2 = st.columns(2)
        c1.metric("Invoice Amount", f"${inv_amt}")
        c2.metric("PO Amount", f"${po_amt}")
        
        st.metric("Match Score", f"{curr_data.get('match_score', 0)}")
        
        st.markdown("#### Review Decision")
        with st.form("hitl_form"):
            note = st.text_input("Review Notes", placeholder="e.g., Shipping variance approved")
            
            col_approve, col_reject = st.columns(2)
            submitted_approve = col_approve.form_submit_button("✅ Approve")
            submitted_reject = col_reject.form_submit_button("❌ Reject")
            
            if submitted_approve or submitted_reject:
                action = "ACCEPT" if submitted_approve else "REJECT"

                with st.spinner("Resuming Workflow..."):
                    try:
                        # UPDATED TO MATCH PDF SPEC API
                        resp = requests.post(f"{API_URL}/human-review/decision", json={
                            "checkpoint_id": st.session_state.thread_id, # Mapping thread_id to checkpoint_id
                            "decision": action,
                            "notes": note
                        })

                        if resp.status_code == 200:
                            res_json = resp.json()
                            st.session_state.logs = res_json["logs"]
                            st.session_state.status = res_json["status"]
                            # CAPTURE THE NEW STATE 
                            st.session_state.data = res_json.get("state", {})
                            st.rerun()
                        else:
                            st.error(f"Failed: {resp.text}")

                    except Exception as e:
                        st.error(f"Error: {e}")

    # --- COMPLETED STATE (Success) ---
    elif st.session_state.status in ["COMPLETED", "SUCCESS"]:
        st.divider()
        st.success("🎉 Workflow Finalized Successfully")
        
        # Show Final Artifacts (Simulated)
        with st.expander("📂 View Final Output Payload"):
            # Show the actual useful data
            final_data = st.session_state.data
            
            # Filter out the huge logs list for a cleaner view
            display_payload = {k: v for k, v in final_data.items() if k != "logs"}
            
            st.json(display_payload)

    # --- IDLE STATE ---
    else:
        st.info("Waiting for input...")