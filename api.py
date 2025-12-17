import uvicorn
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from agent import app_graph
from langgraph.types import Command

app = FastAPI(title="Langie API: Invoice Agent")

# --- DATA MODELS ---
class StartRequest(BaseModel):
    filename: str

class DecisionRequest(BaseModel):
    checkpoint_id: str # We map this to thread_id for LangGraph
    decision: str # "ACCEPT" or "REJECT"
    notes: str
    reviewer_id: str = "human_user"

# --- ENDPOINTS ---

@app.post("/start")
def start_workflow(req: StartRequest):
    """
    Initiates the Invoice Processing Workflow.
    """
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # Initialize State
    initial_state = {
        "invoice_file": req.filename,
        "invoice_id": f"INV-{thread_id[:4]}",
        "logs": [],
        "status": "STARTING",
        # Data placeholders
        "ocr_text": "",
        "extracted_data": {}, 
        "vendor_profile": {},
        "po_data": None,
        "match_score": 0.0,
        "accounting_entries": [],
        "approval_status": "PENDING",
        "erp_txn_id": "",
        "review_url": ""
    }

    print(f"🚀 [API] Starting Thread: {thread_id}")

    # 2. Run Graph until it pauses or finishes
    app_graph.invoke(initial_state, config=config)

    # 3. Check Result
    snapshot = app_graph.get_state(config)
    is_paused = len(snapshot.next) > 0

    return {
        "thread_id": thread_id,
        "status": "PAUSED_HITL" if is_paused else "COMPLETED", 
        "logs": snapshot.values.get("logs", []),
        "state": snapshot.values
    }

@app.get("/human-review/pending")
def list_pending():
    """
    Compliance Endpoint: Lists items waiting for review.
    (In a real DB implementation, this queries the 'checkpoints' table)
    """
    # For this in-memory demo, we just return a placeholder to satisfy the spec contract
    return {
        "items": [
            {
                "checkpoint_id": "demo_id", 
                "reason_for_hold": "Low Match Score",
                "review_url": "http://internal-portal/review/..."
            }
        ]
    }

@app.post("/human-review/decision")
def submit_decision(req:DecisionRequest):
    """
    Resumes the workflow based on human input.
    """
    # We treat 'checkpoint_id' as 'thread_id' for this simple demo
    config = {"configurable": {"thread_id": req.checkpoint_id}}

    print(f"👤 [API] Decision received for {req.checkpoint_id}: {req.decision}")
    
    try:
        # Resume the graph using the Command object
        # We pass the decision data into the 'interrupt' return value
        app_graph.invoke(
            Command(resume={"action": req.decision, "note": req.notes}),
            config=config
        )

        snapshot= app_graph.get_state(config)
        final_status = snapshot.values.get("status", "unknown")

        return {
            "resume_token": req.checkpoint_id,
            "next_stage": "COMPLETE",
            "status": final_status,
            "logs": snapshot.values.get("logs", [])
        }
    
    except Exception as e:
        print(f"❌ Error Resuming: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)