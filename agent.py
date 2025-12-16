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
    

    








