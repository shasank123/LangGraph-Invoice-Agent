import httpx
import os
import operator
from typing import TypedDict, Optional, List, Annotated
from dotenv import load_dotenv

# LLM Imports
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from langgraph.checkpoint.memory import MemorySaver

# Load Environment Variables
load_dotenv()

# Initialize LLM
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

# --- HELPER: REDUCER ---
def replace(old_value, new_value):
    return new_value

# --- 1. STATE DEFINITION ---
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
    ai_analysis: Annotated[Optional[str], replace]
    flags: Annotated[List[str], replace]

# --- 2. BIGTOOL PICKER ---
class BigToolPicker:
    @staticmethod
    def select(capability: str, context: dict = None) -> str:
        context = context or {}
        context_str = str(context)

        tool_pools = {
            "ocr": ["google_vision (best for images)", "aws_textract (best for pdfs)", "tesseract"],
            "enrichment": ["clearbit", "people_data_labs"],
            "erp": ["sap_connector", "quickbooks"]
        }

        available = tool_pools.get(capability, ["default_tool"])

        prompt = ChatPromptTemplate.from_template("""
            You are an expert Systems Architect.
            Capability Needed: {capability}
            Available Tools: {available}
            Context: {context}
            
            Rules:
            - If file is image (.png/.jpg), use google_vision.
            - If file is pdf, use aws_textract.
            - Return ONLY the tool name.
        """)
        
        chain = prompt | llm | StrOutputParser()

        try:
            tool_name = chain.invoke({
                "capability": capability,
                "available": str(available),
                "context": context_str
            })
            return tool_name.strip().lower().split()[0]
        except:
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
    state["logs"].append(f"🧠 STAGE 2: AI selected '{tool}'")
    ocr_res = call_post(ATLAS_URL, "ocr_extract", params={"filename": state["invoice_file"], "tool": tool})
    state["ocr_text"] = ocr_res.get("text", "")
    parse_res = call_post(COMMON_URL, "parse_invoice", params={"text": state["ocr_text"]})
    state["extracted_data"] = parse_res
    state["logs"].append(f"   -> Extracted: ${parse_res.get('amount')}")
    return state

def node_prepare(state: InvoiceState):
    vendor = state["extracted_data"].get("vendor", "unknown")
    tool = BigToolPicker.select("enrichment", context={"vendor": vendor})
    state["logs"].append(f"🛠️ STAGE 3: AI selected '{tool}'")
    
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

def analyze_discrepancy(state: InvoiceState) -> str:
    inv = state["extracted_data"].get("amount", 0)
    po = state["po_data"]["amount"] if state["po_data"] else 0
    diff = inv - po
    prompt = f"Invoice: ${inv}, PO: ${po}, Diff: ${diff}. Recommend APPROVE if diff is small/tax. REJECT if large mismatch. Keep it short."
    return llm.invoke(prompt).content

def node_checkpoint_hitl(state: InvoiceState):
    state["logs"].append("⏸️ STAGE 6: Pausing for Human Review.")
    state["ai_analysis"] = analyze_discrepancy(state)
    state["logs"].append(f"🤖 AI Recommendation: {state['ai_analysis']}")
    return state

def node_hitl_decision(state: InvoiceState):
    state["logs"].append("👨‍💼 STAGE 7 [DECISION]: Waiting for user...")
    decision = interrupt({
        "msg": "Review Needed",
        "score": state["match_score"],
        "ai_analysis": state.get("ai_analysis"),
    })
    
    action = decision.get("action")
    note = decision.get("note", "")
    state["logs"].append(f" -> User Decision: {action} ({note})")

    # FIX: No more Command(goto). Just update status.
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
    # CAPTURE the ID (This is what was missing/empty before)
    txn_id = res.get("erp_txn_id", "ERROR_NO_ID")
    state["erp_txn_id"] = txn_id
    return state

def node_notify(state: InvoiceState):
    state["logs"].append("✉️ STAGE 11: Notifying...")
    call_post(ATLAS_URL, "notify", params={"email": "vendor@acme.com", "message": "Paid"})
    return state

def node_complete(state: InvoiceState):
    # If rejected, we might have skipped here directly
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

# Router 1: Match Score
def routing_match(state):
    if state["match_score"] >= 0.90:
        return "RECONCILE"
    return "CHECKPOINT_HITL"

workflow.add_conditional_edges("MATCH_TWO_WAY", routing_match)

workflow.add_edge("CHECKPOINT_HITL", "HITL_DECISION")

# Router 2: HITL Decision (The FIX)
def routing_hitl(state):
    if state["status"] == "REJECTED":
        return "COMPLETE" # Skip to end
    return "RECONCILE" # Continue flow

workflow.add_conditional_edges("HITL_DECISION", routing_hitl)

workflow.add_edge("RECONCILE", "APPROVE")
workflow.add_edge("APPROVE", "POSTING")
workflow.add_edge("POSTING", "NOTIFY")
workflow.add_edge("NOTIFY", "COMPLETE")
workflow.add_edge("COMPLETE", END)

checkpointer = MemorySaver()
app_graph = workflow.compile(checkpointer=checkpointer)