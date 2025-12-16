# Langie: Autonomous Invoice Processing Agent 🧾🤖

**Langie** is an intelligent, 12-stage agentic workflow built with **LangGraph** that processes invoices end-to-end. It features **Human-in-the-Loop (HITL)** for handling discrepancies, **MCP (Model Context Protocol)** architecture for modular tooling, and **Bigtool** for dynamic tool selection.

---

## 🏗️ Architecture

The system is designed as a distributed application with three core components:

1.  **The Brain (LangGraph Agent):** A stateful graph orchestration engine that manages the 12-step lifecycle of an invoice.
2.  **The Muscle (MCP Servers):**
    * **COMMON Server (Port 8001):** Handles deterministic logic (Parsing, 2-Way Matching, Accounting).
    * **ATLAS Server (Port 8002):** Handles external interactions (OCR, ERP simulation, Email notifications).
3.  **The Interface (Streamlit UI):** A frontend for uploading invoices and handling human review requests.

### The 12-Stage Workflow
1.  **INTAKE:** Validates payload schema.
2.  **UNDERSTAND:** OCR extraction (via Atlas) & Line Item Parsing (via Common).
3.  **PREPARE:** Vendor normalization and enrichment.
4.  **RETRIEVE:** Fetches POs and GRNs from the ERP system.
5.  **MATCH_TWO_WAY:** Computes match score. **(Trigger: < 0.90 Score)**.
6.  **CHECKPOINT_HITL:** Pauses workflow and saves state if matching fails.
7.  **HITL_DECISION:** Resumes workflow based on Human Accept/Reject decision.
8.  **RECONCILE:** Generates accounting ledger entries.
9.  **APPROVE:** Applies approval policies (Auto vs. CFO Escalate).
10. **POSTING:** Posts to ERP and schedules payment.
11. **NOTIFY:** Sends email notifications.
12. **COMPLETE:** Final audit logging.

---

## 🚀 Installation & Setup

### Prerequisites
* Python 3.10+
* Git

### 1. Clone the Repository
```bash
git clone [https://github.com/shasank123/LangGraph-Invoice-Agent.git](https://github.com/shasank123/LangGraph-Invoice-Agent.git)
cd LangGraph-Invoice-Agent