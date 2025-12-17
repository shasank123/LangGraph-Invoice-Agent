import httpx
from typing import TypedDict, Optional, List, Annotated
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from langgraph.checkpoint.memory import MemorySaver

# --- HELPER: REDUCER ---
def replace(old_value, new_value):
    return new_value

# --- 1. STATE DEFINITION (Robust) ---
class InvoiceState(TypedDict):
    invoice_file: Annotated[str, replace]
    invoice_id: Annotated[str, replace]
    ocr_text: Annotated[str, replace]
    extracted_data: Annotated[dict, replace]
    vendor_profile: Annotated[dict, replace]
    po_data: Annotated[Optional[dict], replace]
    match_score: Annotated[float, replace]
    accounting_entries: Annotated[list, replace]
    approval_status: Annotated[str, replace]
    erp_txn_id: Annotated[str, replace]
    logs: Annotated[List[str], replace]
    status: Annotated[str, replace]
    review_url: Annotated[Optional[str], replace]
    flags: Annotated[List[str], replace]

# --- 2. BIGTOOL PICKER (Heuristic V1) ---
class BigToolPicker:
    @staticmethod
    def select(capability: str, context: dict = None) -> str:
        context = context or {}
        # 1. OCR Selection Logic
        if capability == "ocr":
            filename = context.get("filename", "").lower()
            if filename.endswith(".png") or filename.endswith(".jpg"):
                return "google_vision"
            elif filename.endswith(".pdf"):
                return "aws_textract"
            else:
                return "tesseract"
            
        # 2. Enrichment Selection Logic
        if capability == "enrichment":
            vendor = context.get("vendor", "").upper()
            if "CORP" in vendor:
                return "clearbit"
            else:
                return "people_data_labs"
            
        # 3. ERP Selection Logic
        if capability == "erp":
            return "sap_connector"
        
        return "default_tool"

# --- 3. HELPERS ---
COMMON_URL = "http://127.0.0.1:8001"
ATLAS_URL = "http://127.0.0.1:8002"

def call_post(url, endpoint, json=None, params=None):
    try: return httpx.post(f"{url}/{endpoint}", json=json, params=params).json()
    except: return {}

def call_get(url, endpoint, params=None):
    try: return httpx.get(f"{url}/{endpoint}", params=params).json()
    except: return {}

# --- 4. NODES ---

def node_intake(state: InvoiceState):
    state['logs'].append(f"📥 STAGE 1: Validating {state['invoice_file']}")
    if not state['invoice_file']: raise ValueError("Missing File")
    return state

def node_understand(state: InvoiceState):
    tool = BigToolPicker.select("ocr", context={"filename": state["invoice_file"]})
    state["logs"].append(f"🧠 STAGE 2: Heuristic selected '{tool}'")
    ocr_res = call_post(ATLAS_URL, "ocr_extract", params={"filename": state["invoice_file"], "tool": tool})
    state["ocr_text"] = ocr_res.get("text", "")
    parse_res = call_post(COMMON_URL, "parse_invoice", params={"text": state["ocr_text"]})
    state["extracted_data"] = parse_res
    state["logs"].append(f"   -> Extracted: ${parse_res.get('amount')}")
    return state

def node_prepare(state: InvoiceState):
    vendor = state["extracted_data"].get("vendor", "unknown")
    tool = BigToolPicker.select("enrichment", context={"vendor": vendor})
    state["logs"].append(f"🛠️ STAGE 3: Selected '{tool}' for enrichment.")
    
    # 2. Call the Mock Server
    profile = call_post(ATLAS_URL, "enrich_vendor", params={"vendor_name": vendor})
    state["vendor_profile"] = profile
    
    # 3. COMPUTE FLAGS (The New Logic)
    flags = []
    score = profile.get("credit_score", 0)
    
    state["logs"].append(f"   -> Vendor Score: {score} ({profile.get('risk_level')})")

    if score < 600:
        flags.append("RISK_LOW_CREDIT_SCORE")
    if profile.get("risk_level") == "HIGH":
        flags.append("RISK_CATEGORY_HIGH")
        
    # Save flags to state
    state["flags"] = flags
    
    if flags:
        state["logs"].append(f"   ⚠️ FLAGS DETECTED: {', '.join(flags)}")
        
    return state

def node_retrieve(state: InvoiceState):
    state["logs"].append("📚 STAGE 4: Fetching POs...")
    vendor = state["extracted_data"].get("vendor", "unknown")
    po_res = call_get(ATLAS_URL, "fetch_po", params={"vendor": vendor})
    if po_res.get("found"):
        state["po_data"] = po_res
        state["logs"].append(f"   -> Found PO: {po_res['po_number']}")
    else:
        state["po_data"] = None
        state["logs"].append("   -> No PO found.")
    return state

def node_match(state: InvoiceState):
    state["logs"].append("⚖️ STAGE 5: Matching...")
    inv_amt = state["extracted_data"].get("amount", 0)   
    po_amt = state["po_data"]["amount"] if state["po_data"] else 0
    match_res = call_post(COMMON_URL, "compute_match_score", json={"invoice_amount": inv_amt, "po_amount": po_amt})
    state["match_score"] = match_res.get("score", 0)
    state["logs"].append(f"   -> Match Score: {state['match_score']}")
    return state

def node_checkpoint_hitl(state: InvoiceState):
    state["logs"].append("⏸️ STAGE 6: Pausing for Human Review.")
    state["review_url"] = f"http://internal/review/{state['invoice_id']}"
    return state

def node_hitl_decision(state: InvoiceState):
    state["logs"].append("👨‍💼 STAGE 7 [DECISION]: Waiting for user...")
    decision = interrupt({
        "msg": "Review Needed",
        "score": state["match_score"]
    })
    
    action = decision.get("action")
    note = decision.get("note", "")
    state["logs"].append(f" -> User Decision: {action} ({note})")

    if action == "REJECT":
        state["status"] = "REJECTED"
    else:
        state["status"] = "APPROVED"
    
    return state

def node_reconcile(state: InvoiceState):
    state["logs"].append("📘 STAGE 8: Reconciling...")
    res = call_post(COMMON_URL, "build_accounting_entries", params={"amount": state["extracted_data"]["amount"], "vendor": state["extracted_data"]["vendor"]})
    state["accounting_entries"] = res.get("entries", [])
    return state

def node_approve(state: InvoiceState):
    state["logs"].append("🔄 STAGE 9: Approving...")
    # Logic: If status is already "APPROVED" (from HITL node), mark as HUMAN.
    # Otherwise (if it came straight from Match), mark as AUTO.
    
    if state.get("status") == "APPROVED":
        state["approval_status"] = "HUMAN_APPROVED"
    else:
        state["approval_status"] = "AUTO_APPROVED"
        
    state["logs"].append(f"   -> Final Decision: {state['approval_status']}")
    return state

def node_posting(state: InvoiceState):
    state["logs"].append("🏃 STAGE 10: Posting to ERP...")
    res = call_post(ATLAS_URL, "post_to_erp", params={"invoice_id": state["invoice_id"]})

    # DEBUG PRINT: Watch your terminal when this runs!
    print(f"🔍 DEBUG: ERP Response for {state['invoice_id']}: {res}")

    # Use a fallback if the ID is missing
    state["erp_txn_id"] = res.get("erp_txn_id", "ERROR_MISSING_ID")
    return state

def node_notify(state: InvoiceState):
    state["logs"].append("✉️ STAGE 11: Notifying...")
    call_post(ATLAS_URL, "notify", params={"email": "vendor@acme.com", "message": "Paid"})
    return state

def node_complete(state: InvoiceState):
    final_msg = "REJECTED" if state.get("status") == "REJECTED" else "SUCCESS"
    state["logs"].append(f"✅ STAGE 12 [COMPLETE]: Workflow Finalized ({final_msg}).")
    state["status"] = final_msg
    return state

# --- 5. BUILD GRAPH ---
workflow = StateGraph(InvoiceState)

nodes = [
    ("INTAKE", node_intake), ("UNDERSTAND", node_understand), ("PREPARE", node_prepare),
    ("RETRIEVE", node_retrieve), ("MATCH_TWO_WAY", node_match), ("CHECKPOINT_HITL", node_checkpoint_hitl),
    ("HITL_DECISION", node_hitl_decision), ("RECONCILE", node_reconcile), ("APPROVE", node_approve),
    ("POSTING", node_posting), ("NOTIFY", node_notify), ("COMPLETE", node_complete)
]

for name, func in nodes:
    workflow.add_node(name, func)

workflow.add_edge(START, "INTAKE")
workflow.add_edge("INTAKE", "UNDERSTAND")
workflow.add_edge("UNDERSTAND", "PREPARE")
workflow.add_edge("PREPARE", "RETRIEVE")
workflow.add_edge("RETRIEVE", "MATCH_TWO_WAY")

def routing_match(state):
    if state["match_score"] >= 0.90:
        return "RECONCILE"
    return "CHECKPOINT_HITL"

workflow.add_conditional_edges("MATCH_TWO_WAY", routing_match)

workflow.add_edge("CHECKPOINT_HITL", "HITL_DECISION")

# --- Conditional Router for HITL ---
def routing_hitl(state):
    if state["status"] == "REJECTED":
        return "COMPLETE"
    return "RECONCILE"

workflow.add_conditional_edges("HITL_DECISION", routing_hitl)

workflow.add_edge("RECONCILE", "APPROVE")
workflow.add_edge("APPROVE", "POSTING")
workflow.add_edge("POSTING", "NOTIFY")
workflow.add_edge("NOTIFY", "COMPLETE")
workflow.add_edge("COMPLETE", END)

checkpointer = MemorySaver()
app_graph = workflow.compile(checkpointer=checkpointer)