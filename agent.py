import httpx
from typing import TypedDict, Optional, List, Any
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from langgraph.checkpoint.memory import MemorySaver

# --- 1. STATE DEFINITION ---
class InvoiceState(TypedDict):
    # Input
    invoice_file: str
    invoice_id: str
    
    # Stage Data
    ocr_text: str
    extracted_data: dict      # Amount, Vendor
    vendor_profile: dict      # Enriched data
    po_data: Optional[dict]   # ERP PO
    match_score: float
    accounting_entries: list
    approval_status: str
    erp_txn_id: str

    # Workflow Metadata
    logs: List[str]
    status: str
    review_url: Optional[str]

# --- 2. BIGTOOL PICKER (Dynamic Tool Selection) ---
class BigToolPicker:
    @staticmethod
    def select(capability: str, context: dict = None) -> str:
        """
        Selects a tool based on heuristics (simulating AI reasoning).
        """
        context = context or {}
        # 1. OCR Selection Logic
        if capability == "ocr":
            filename = context.get("filename", "").lower()

            # Heuristic: Images need powerful Vision AI, PDFs use standard Textract
            if filename.endswith(".png") or filename.endswith(".jpg"):
                return "google_vision"
            elif filename.endswith(".pdf"):
                return "aws_textract"
            else:
                return "tesseract"
            
        # 2. Enrichment Selection Logic
        if capability == "enrichment":
            # Heuristic: Use different providers based on vendor region
           vendor = context.get("vendor", "").upper()
           if "CORP" in vendor:
               return "clearbit"
           else:
               return "people_data_labs"
           
        # 3. ERP Selection Logic
        if capability == "erp":
            return "sap_connector"
        
        return "default_tool"

# --- 3. MCP CLIENT HELPERS ---
COMMON_URL = "http://127.0.0.1:8001"
ATLAS_URL = "http://127.0.0.1:8002"

def call_post(url, endpoint, json=None, params=None):
    try:
        return httpx.post(f"{url}/{endpoint}", json=json, params=params).json()
    except:
        return {}

def call_get(url, endpoint, params=None):
    try:
        return httpx.post(f"{url}/{endpoint}", params=params).json()
    except:
        return {}
    
# --- 4. THE 12 NODES ---

# 1. INTAKE
def node_intake(state:InvoiceState):
    state['logs'].append(f"📥 STAGE 1 [INTAKE]: Validating schema for {state['invoice_file']}...")
    # Validation logic (simulated)
    if not state['invoice_file']: raise ValueError("Missing Invoice ID")
    return state

# 2. UNDERSTAND
def node_understand(state:InvoiceState):
    tool = BigToolPicker("ocr", context= {"filename": state["invoice_file"]})
    state["logs"].append(f"🧠 STAGE 2 [UNDERSTAND]: OCR via {tool}")

    # OCR
    ocr_res = call_post(ATLAS_URL, "ocr_extract", params={"filename":state["invoice_file"], "tool": tool})
    state["ocr_text"] = ocr_res.get("text", "")

    # Parse
    parse_res = call_post(COMMON_URL, "parse_invoice", params={"text": state["ocr_text"]})
    state["extracted_data"] = parse_res
    state["logs"].append(f"  -> Extracted: ${parse_res.get('amount')} from {parse_res.get('vendor')}")
    return state

# 3. PREPARE
def node_prepare(state:InvoiceState):
    tool = BigToolPicker.select("enrichment")
    state["logs"].append(f"🛠️ STAGE 3 [PREPARE]: Enriching vendor via{tool}")
    vendor = state["extracted_data"].get("vendor", "unknown")
    enrich_res = call_post(ATLAS_URL, "enrich_vendor", params={"vendor_name": vendor})
    state["vendor_profile"] = enrich_res
    return state

# 4. RETRIEVE
def node_retrieve(state:InvoiceState):
    state["logs"].append("📚 STAGE 4 [RETRIEVE]: Fetching POs from ERP...")
    vendor = state["extracted_data"].get("vendor", "unknown")
    
    po_res = call_get(ATLAS_URL, "fetch_po", params={"vendor": vendor})
    if po_res.get("found"):
        state["po_data"] = po_res
        state["logs"].append(f"  -> Found PO:{po_res['po_number']} (${po_res['amount']})")

    else:
        state["po_data"] = None
        state["logs"].append("   -> No PO found.")
        return state
    
# 5. MATCH_TWO_WAY
def node_match(state:InvoiceState):
    state["logs"].append("⚖️ STAGE 5 [MATCH]: Comparing Invoice vs PO...")
    inv_amt = state["extracted_data"].get("amount", 0)   
    po_amt = state["po_data"]["amount"] if state["po_data"] else 0
    
    match_res = call_post(COMMON_URL, "compute_match_score", json={"invoice_amount": inv_amt, "po_amount": po_amt})
    state["match_score"] = match_res.get("score", 0)
    state["logs"].append(f"  -> Match Score: {state['match_score']}")
    return state

# 6. CHECKPOINT_HITL
def node_checkpoint_hitl(state:InvoiceState):
    state["logs"].append("⏸️ STAGE 6 [CHECKPOINT]: Pausing for Human Review.")
    state["review_url"] = f"http://internal-portal/review/{state['invoice_id']}"
    # This node just sets up the state. The graph interrupt happens in the next transition
    return state

# 7. HITL_DECISION (Non-Deterministic)
def node_hitl_decision(state:InvoiceState):
    # This node calls 'interrupt' which halts execution until resume
    state["logs"].append("👨‍💼 STAGE 7 [DECISION]: Waiting for user...")
    decision = interrupt({
        "msg": "Review Needed",
        "score": state["match_score"],
        "review": state.get("review_url")
    })
    # We resume here
    action = decision.get("action")
    note = decision.get("note", "")
    state["logs"].append(f" -> User Decision: {action} {note}")

    if action == "REJECT":
        return Command(goto="COMPLETE", update={"status": "MANUAL_HANDLING"})
    
    # If ACCEPT, continue to RECONCILE
    return state

# 8. RECONCILE
def node_reconcile(state:InvoiceState):
    state["logs"].append("📘 STAGE 8 [RECONCILE]: Building Ledger Entries...")
    res = call_post(COMMON_URL, "build_accounting_entries", params={"amount": state["extracted_data"]["amount"], "vendor": state["extracted_data"]["vendor"]})
    state["accounting_entries"] = res.get("entries", [])
    return state

# 9. APPROVE
def node_approve(state:InvoiceState):
    state["logs"].append("🔄 STAGE 9 [APPROVE]: Applying Approval Policy...")
    amt = state["extracted_data"]["amount"]
    if amt > 10000:
        state["approval_status"] = "ESCALATED_TO_CFO"
    else:
        state["approval_status"] = "AUTO_APPROVED"
    state["logs"].append(f"   -> Policy: {state['approval_status']}")
    return state

# 10. POSTING
def node_posting(state:InvoiceState):
    state["logs"].append("🏃 STAGE 10 [POSTING]: Posting to ERP...")
    res = call_post(ATLAS_URL, "post_to_erp", params={"invoice_id": state["invoice_id"]})
    state["erp_txn_id"] = res.get("erp_txn_id")
    return state

# 11. NOTIFY
def node_notify(state:InvoiceState):
    state["logs"].append("✉️ STAGE 11 [NOTIFY]: Sending Emails...")
    call_post(ATLAS_URL, "notify", params={"email": "vendor@acme.com", "message": "Paid"})
    return state

# 12. COMPLETE
def node_complete(state:InvoiceState):
    state["logs"].append("✅ STAGE 12 [COMPLETE]: Workflow Finalized.")
    final_status = state.get("status", "SUCCESS")
    state["status"] = final_status
    return state

# --- 5. BUILD GRAPH ---
workflow = StateGraph(InvoiceState)

# Add all 12 Nodes
nodes= [
    ("INTAKE", node_intake), ("UNDERSTAND", node_understand), ("PREPARE", node_prepare),
    ("RETRIEVE", node_retrieve), ("MATCH_TWO_WAY", node_match), ("CHECKPOINT_HITL", node_checkpoint_hitl),
    ("HITL_DECISION", node_hitl_decision), ("RECONCILE", node_reconcile), ("APPROVE", node_approve),
    ("POSTING", node_posting), ("NOTIFY", node_notify), ("COMPLETE", node_complete)
]

for name, func in nodes:
    workflow.add_node(name, func)

# Linear flow 1-5 (Adding edges to workflow)
workflow.add_edge(START, "INTAKE")
workflow.add_edge("INTAKE", "UNDERSTAND")
workflow.add_edge("UNDERSTAND", "PREPARE")
workflow.add_edge("PREPARE", "RETRIEVE")
workflow.add_edge("RETRIEVE", "MATCH_TWO_WAY")

# Conditional Edge after Match
def rounting_logic(state):
    if state["match_score"] >= 0.90:
        return "RECONCILE" # Skip HITL
    return "CHECKPOINT_HITL"

workflow.add_conditional_edges("MATCH_TWO_WAY", rounting_logic)

# HITL Flow
workflow.add_edge("CHECKPOINT_HITL", "HITL_DECISION")
workflow.add_edge("HITL_DECISION", "RECONCILE") # If resumed (Reject handled inside node via Command)

# Final Flow 8-12
workflow.add_edge("RECONCILE", "APPROVE")
workflow.add_edge("APPROVE", "POSTING")
workflow.add_edge("POSTING", "NOTIFY")
workflow.add_edge("NOTIFY", "COMPLETE")
workflow.add_edge("COMPLETE", END)

# Persistence
checkpointer = MemorySaver()
app_graph = workflow.compile(checkpointer=checkpointer)






    








