from pydantic import BaseModel
from typing import Optional, List, Any
from enum import Enum

class InvoiceStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PROCESSING = "PROCESSING"

class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    total: float

class Invoice(BaseModel):
    id: str
    filename: str
    vendor_name: str
    invoice_number: str
    invoice_date: Optional[str]
    due_date: Optional[str]
    total_amount: float
    currency: str
    line_items: List[LineItem] = []
    tax_amount: float = 0
    subtotal: float = 0
    payment_terms: str = ""
    notes: str = ""
    status: InvoiceStatus
    approver: Optional[str] = None
    approver_comment: Optional[str] = None
    created_at: str
    updated_at: str

class ApprovalRequest(BaseModel):
    action: str  # "approve" or "reject"
    approver: str
    comment: Optional[str] = ""

class ChatMessage(BaseModel):
    message: str
    session_id: Optional[str] = "default"

class ChatResponse(BaseModel):
    message: str
    action_taken: Optional[Any] = None