import uvicorn
import sqlite3
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from multiprocessing import Process

# --- DATABASE SETUP ---
def init_erp_db():
    conn = sqlite3.connect("erp_system.db") 
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_orders
                 (po_number TEXT PRIMARY KEY, vendor TEXT, amount REAL, status TEXT)''')
    
    # Seeding Data
    data = [
        ("PO-1001", "ACME CORP", 5000.00, "APPROVED"),
        ("PO-1002", "GLOBEX INC", 1250.50, "PENDING"),
        ("PO-9999", "MEGA CORP", 10000.00, "APPROVED")
    ]
    c.executemany("INSERT OR IGNORE INTO purchase_orders VALUES(?,?,?,?)", data)
    
    conn.commit() 
    conn.close()

# Initialize DB immediately
init_erp_db()

# ==========================================
# SERVER 1: COMMON (Internal Logic) [Port 8001]
# ==========================================
common_app = FastAPI(title="COMMON Server")

class MatchRequest(BaseModel):
    invoice_amount: float
    po_amount: float

@common_app.post("/parse_invoice")
def parse_invoice(text: str):
    # Heuristic parsing logic
    lines = text.split('\n')
    extracted = {"amount": 0.0, "vendor": "Unknown", "date": "2024-01-01"}
    for line in lines:
        lower = line.lower()
        if "amount" in lower or "total" in lower:
            try:
                words = line.replace('$', '').split()
                for w in reversed(words):
                    try:
                        extracted['amount'] = float(w.replace(',', ''))
                        break
                    except: continue
            except: pass

        if "vendor" in lower:
            extracted['vendor'] = line.split(":")[-1].strip().upper()

    return extracted

@common_app.post("/compute_match_score")
def compute_match_score(req: MatchRequest):
    if req.po_amount == 0: return {"score": 0.0}
    diff = abs(req.invoice_amount - req.po_amount)
    pct = (diff / req.po_amount) * 100
    # Strict matching: 0% diff = 1.0 score. >5% diff = 0.0 score.
    score = 1.0 if pct == 0 else max(0, 1.0 - (pct / 5))
    return {"score": round(score, 2)}

@common_app.post("/build_accounting_entries")
def build_accounting_entries(amount: float, vendor: str):
    return {
        "entries": [
            {"type": "DEBIT", "account": "EXPENSE_General", "amount": amount},
            {"type": "CREDIT", "account": "AP_Trade", "amount": amount, "vendor": vendor}
        ]
    }

# ==========================================
# SERVER 2: ATLAS (External Tools) [Port 8002]
# ==========================================
atlas_app = FastAPI(title="ATLAS Server")

@atlas_app.post("/ocr_extract")
def ocr_extract(filename: str, tool: str = "google_vision"):
    time.sleep(1)
    # Demo Logic: Filename dictates content
    if "good" in filename:
        return {"text": "INVOICE #001\nVENDOR: ACME CORP\nTOTAL: $5000.00"}
    elif "bad" in filename:
        return {"text": "INVOICE #002\nVENDOR: ACME CORP\nTOTAL: $5500.00"}
    return {"text": "UNREADABLE"}

@atlas_app.post("/enrich_vendor")
def enrich_vendor(vendor_name: str, tool: str = "clearbit"):
    # Mock Enrichment
    return {"tax_id": "US-99-99999", "credit_score": 850, "risk": "LOW"}

@atlas_app.get("/fetch_po")
def fetch_po(vendor: str):
    conn = sqlite3.connect("erp_system.db")
    c = conn.cursor()
    c.execute("SELECT * FROM purchase_orders WHERE vendor LIKE ?", (f"%{vendor}%",))
    row = c.fetchone()
    conn.close()
    if row: return {"po_number": row[0], "amount": row[2], "found": True}
    return {"found": False}

@atlas_app.post("/post_to_erp")
def post_to_erp(invoice_id: str):
    return {"erp_txn_id": f"TXN-{int(time.time())}", "status": "POSTED"}

@atlas_app.post("/notify")
def notify(email: str, message: str):
    return {"status": "SENT", "provider": "SendGrid"}

# ==========================================
# RUNNER
# ==========================================
def run_services():
    p1 = Process(target=uvicorn.run, args=(common_app,), kwargs={"host":"127.0.0.1", "port":8001})
    p2 = Process(target=uvicorn.run, args=(atlas_app,), kwargs={"host":"127.0.0.1", "port":8002})
    p1.start(); p2.start()
    p1.join(); p2.join()

if __name__ == "__main__":
    run_services()