import uvicorn
import uuid
from fastapi import FastAPI
from pydantic import BaseModel
from agent import app_graph
from langgraph.types import Command

app = FastAPI(title="Langie API")

class StartRequest(BaseModel):
    filename: str

class DecisionRequest(BaseModel):
    thread_id: str
    action: str
    note: str

@app.post("/start")
def start_workflow(req:StartRequest):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # Initialize State
    initial_state = {
        "invoice_file": req.filename,
        "invoice_id": f"INV-{thread_id[:4]}",
        "logs": [],
        "match_score": 0.0,
        "extracted_data": {},
        "po_data": None
    }

    app_graph.invoke(initial_state, config=config)

    # Return status
    snapshot = app_graph.get_state(config)
    status = "PAUSED_HITL" if len(snapshot.next) > 0 else "COMPLETED"

    return {
        "thread_id": thread_id,
        "status": status,
        "logs": snapshot.values.get("logs", []),
        "state": snapshot.values
    }

@app.post("/decision")
def submit_decision(req:DecisionRequest):
    config = {"configurable": {"thread_id": req.thread_id}}

    print(f"Resuming thread {req.thread_id} with {req.action}")

    # Resume Graph
    app_graph.invoke(
        Command(resume= {"action": req.action, "note": req.note}),
        config= config
    )

    snapshot= app_graph.get_state(config)
    return {
        "status": "COMPLETED" if not snapshot.next else "RUNNING",
        "logs": snapshot.values.get("logs", [])
    }

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)