from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import json
import uuid
import httpx
import re
from datetime import datetime
from pathlib import Path
from models import ApprovalRequest, ChatMessage, ChatResponse
from database import init_db, get_db_connection

app = FastAPI(title="Invoice Processing Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"


@app.on_event("startup")
async def startup():
    init_db()


async def call_lm_studio(messages: list, json_mode: bool = False) -> str:
    payload = {
        "model": "local-model",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 1000,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(LM_STUDIO_URL, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"LM Studio error: {str(e)}")


async def extract_invoice_data(text_content: str) -> dict:
    try:
        system_prompt = """Extract invoice data. Return ONLY valid JSON with these fields:
{"vendor_name":"","invoice_number":"","invoice_date":"","due_date":"","total_amount":0,"currency":"EUR","line_items":[],"tax_amount":0,"subtotal":0,"payment_terms":"","notes":""}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Extract from:\n{text_content[:1000]}"},
            {"role": "assistant", "content": "{"}
        ]
        raw = await call_lm_studio(messages, json_mode=True)
        if not raw.startswith("{"):
            raw = "{" + raw
        return json.loads(raw)

    except Exception:
        def find(pattern, text, default=""):
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(1).strip() if m else default

        amount_str = find(r'total[^:]*:\s*[^\d]*([\d,\.]+)', text_content, "0")
        try:
            amount = float(amount_str.replace(',', ''))
        except Exception:
            amount = 0.0

        return {
            "vendor_name": find(r'vendor[:\s]+(.+)', text_content),
            "invoice_number": find(r'invoice\s*number[:\s]+(.+)', text_content),
            "invoice_date": find(r'invoice\s*date[:\s]+(.+)', text_content),
            "due_date": find(r'due\s*date[:\s]+(.+)', text_content),
            "total_amount": amount,
            "currency": find(r'\b(EUR|USD|GBP)\b', text_content, "EUR"),
            "line_items": [],
            "tax_amount": 0,
            "subtotal": 0,
            "payment_terms": find(r'payment\s*terms[:\s]+(.+)', text_content),
            "notes": ""
        }


@app.post("/invoices/upload")
async def upload_invoice(file: UploadFile = File(...)):
    """Upload invoice file - extract with AI - store in DB"""
    content = await file.read()
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        text_content = f"[Binary file: {file.filename}]"

    extracted = await extract_invoice_data(text_content)
    invoice_id = str(uuid.uuid4())[:8].upper()
    now = datetime.utcnow().isoformat()

    conn = get_db_connection()
    conn.execute("""
        INSERT INTO invoices (id, filename, vendor_name, invoice_number, invoice_date,
        due_date, total_amount, currency, line_items, tax_amount, subtotal,
        payment_terms, notes, status, created_at, updated_at, raw_text)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        invoice_id, file.filename,
        extracted.get("vendor_name", ""),
        extracted.get("invoice_number", ""),
        extracted.get("invoice_date", ""),
        extracted.get("due_date", ""),
        extracted.get("total_amount") or 0,
        extracted.get("currency", "EUR"),
        json.dumps(extracted.get("line_items", [])),
        extracted.get("tax_amount") or 0,
        extracted.get("subtotal") or 0,
        extracted.get("payment_terms", ""),
        extracted.get("notes", ""),
        "PENDING",
        now, now,
        text_content[:2000]
    ))
    conn.commit()
    conn.close()

    return {"invoice_id": invoice_id, "status": "PENDING", "extracted": extracted}


@app.get("/invoices")
async def list_invoices(status: str = None):
    conn = get_db_connection()
    if status:
        rows = conn.execute("SELECT * FROM invoices WHERE status=? ORDER BY created_at DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM invoices ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Invoice not found")
    data = dict(row)
    data["line_items"] = json.loads(data["line_items"] or "[]")
    return data


@app.post("/invoices/{invoice_id}/approve")
async def approve_invoice(invoice_id: str, req: ApprovalRequest):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Invoice not found")
    new_status = "APPROVED" if req.action == "approve" else "REJECTED"
    now = datetime.utcnow().isoformat()
    conn.execute("UPDATE invoices SET status=?, approver=?, approver_comment=?, updated_at=? WHERE id=?",
                 (new_status, req.approver, req.comment, now, invoice_id))
    conn.commit()
    conn.close()
    return {"invoice_id": invoice_id, "status": new_status}


@app.get("/stats")
async def get_stats():
    conn = get_db_connection()
    total = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='PENDING'").fetchone()[0]
    approved = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='APPROVED'").fetchone()[0]
    rejected = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='REJECTED'").fetchone()[0]
    total_val = conn.execute("SELECT SUM(total_amount) FROM invoices WHERE status='APPROVED'").fetchone()[0] or 0
    conn.close()
    return {"total": total, "pending": pending, "approved": approved, "rejected": rejected, "approved_value": total_val}


@app.post("/chat", response_model=ChatResponse)
async def chat(msg: ChatMessage):
    conn = get_db_connection()
    invoices = conn.execute("SELECT * FROM invoices ORDER BY created_at DESC LIMIT 20").fetchall()
    conn.close()

    invoice_summary = json.dumps([{
        "id": r["id"], "vendor": r["vendor_name"], "amount": r["total_amount"],
        "currency": r["currency"], "status": r["status"], "due": r["due_date"],
        "invoice_number": r["invoice_number"]
    } for r in invoices], indent=2)

    system_prompt = f"""You are a helpful invoice assistant. Use this data:
{invoice_summary}

RULES:
- Reply ONLY in plain English sentences. No JSON. No curly braces.
- Good: "Invoice INV003 from Cloud Services Ltd is APPROVED, amount USD 899.99, due 2024-02-20."
- Keep replies under 3 sentences.
- If user wants to approve/reject, end with: ACTION: approve INV001"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": msg.message},
        {"role": "assistant", "content": "Sure! "}
    ]

    response_text = await call_lm_studio(messages)
    response_text = re.sub(r'\{[^}]+\}', '', response_text).strip()

    if not response_text or len(response_text) < 5:
        user_lower = msg.message.lower()
        for inv in invoices:
            if inv["id"].lower() in user_lower or (inv["vendor_name"] and inv["vendor_name"].lower() in user_lower):
                response_text = f"Invoice {inv['id']} from {inv['vendor_name']} is {inv['status']}. Amount: {inv['currency']} {inv['total_amount']}, due {inv['due_date']}."
                break
        if not response_text:
            response_text = "I could not find that invoice. Please check the ID and try again."

    action = None
    if "ACTION:" in response_text:
        try:
            action_line = response_text.split("ACTION:")[1].strip().split()
            action = {"type": action_line[0], "invoice_id": action_line[1]}
            response_text = response_text.split("ACTION:")[0].strip()
            if action.get("type") in ["approve", "reject"] and action.get("invoice_id"):
                conn = get_db_connection()
                new_status = "APPROVED" if action["type"] == "approve" else "REJECTED"
                now = datetime.utcnow().isoformat()
                conn.execute("UPDATE invoices SET status=?, approver=?, updated_at=? WHERE id=?",
                             (new_status, "Chat Agent", now, action["invoice_id"]))
                conn.commit()
                conn.close()
        except Exception:
            pass

    return ChatResponse(message=response_text, action_taken=action)


@app.post("/seed")
async def seed_demo_data():
    demo_invoices = [
        ("INV001", "Acme Supplies GmbH", "INV-2024-001", "2024-01-15", "2024-02-15", 4250.00, "EUR", "PENDING"),
        ("INV002", "TechParts AG", "TP-99821", "2024-01-10", "2024-01-25", 12800.00, "EUR", "APPROVED"),
        ("INV003", "Cloud Services Ltd", "CS-2024-0045", "2024-01-20", "2024-02-20", 899.99, "USD", "PENDING"),
        ("INV004", "Office Depot", "OD-447821", "2024-01-05", "2024-01-20", 340.50, "EUR", "REJECTED"),
        ("INV005", "DataCenter Pro", "DC-2024-112", "2024-01-22", "2024-03-01", 6500.00, "USD", "PENDING"),
    ]
    conn = get_db_connection()
    now = datetime.utcnow().isoformat()
    for inv in demo_invoices:
        existing = conn.execute("SELECT id FROM invoices WHERE id=?", (inv[0],)).fetchone()
        if not existing:
            conn.execute("""INSERT INTO invoices
                (id, filename, vendor_name, invoice_number, invoice_date, due_date,
                total_amount, currency, line_items, tax_amount, subtotal, payment_terms,
                notes, status, created_at, updated_at, raw_text)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (inv[0], f"{inv[0]}.pdf", inv[1], inv[2], inv[3], inv[4],
                 inv[5], inv[6], "[]", inv[5]*0.19, inv[5]*0.81, "Net 30", "", inv[7], now, now, ""))
    conn.commit()
    conn.close()
    return {"seeded": len(demo_invoices)}