"""Account schemas for financial transactions - Couchbase compatible."""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone
from enum import Enum
from decimal import Decimal
import uuid

class AccountType(str, Enum):
    """Account types."""
    CHECKING = "checking"
    SAVINGS = "savings"
    BUSINESS = "business"
    INVESTMENT = "investment"

class AccountStatus(str, Enum):
    """Account status."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    FROZEN = "frozen"
    CLOSED = "closed"

class Account(BaseModel):
    """Account model for financial transactions."""
    model_config = {"arbitrary_types_allowed": True}

    account_number: str
    account_type: AccountType = AccountType.CHECKING
    customer_id: str
    customer_name: str
    balance: Union[Decimal, float] = Field(description="Current account balance")
    available_balance: Union[Decimal, float] = Field(description="Available balance (considering holds)")
    currency: str = "USD"
    status: AccountStatus = AccountStatus.ACTIVE

    # Limits
    daily_withdrawal_limit: Union[Decimal, float] = 10000.0
    daily_transfer_limit: Union[Decimal, float] = 50000.0
    overdraft_limit: Union[Decimal, float] = 0.0  # Allow negative balance up to this amount

    # Statistics
    total_deposits: Union[Decimal, float] = 0.0
    total_withdrawals: Union[Decimal, float] = 0.0
    transaction_count: int = 0
    last_transaction_at: Optional[datetime] = None

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    kyc_verified: bool = False
    risk_score: Union[Decimal, float] = 0.0
    
    # Holds
    holds: List[Dict[str, Any]] = Field(default_factory=list)  # List of transaction holds

class BalanceHold(BaseModel):
    """Hold on account balance for pending transactions."""
    model_config = {"arbitrary_types_allowed": True}

    hold_id: str = Field(default_factory=lambda: f"HOLD_{uuid.uuid4().hex[:8].upper()}")
    account_number: str
    transaction_id: str
    amount: Union[Decimal, float]
    reason: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    released: bool = False
    released_at: Optional[datetime] = None

class BalanceUpdate(BaseModel):
    """Record of balance update."""
    model_config = {"arbitrary_types_allowed": True}

    update_id: str = Field(default_factory=lambda: f"UPD_{uuid.uuid4().hex[:8].upper()}")
    account_number: str
    transaction_id: str
    operation: str  # "debit" or "credit"
    amount: Union[Decimal, float]
    previous_balance: Union[Decimal, float]
    new_balance: Union[Decimal, float]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: Optional[str] = None  # Couchbase transaction ID for ACID transactions
    
class TransactionJournal(BaseModel):
    """Double-entry bookkeeping journal entry."""
    model_config = {"arbitrary_types_allowed": True}

    journal_id: str = Field(default_factory=lambda: f"JRN_{uuid.uuid4().hex[:8].upper()}")
    transaction_id: str

    # Debit entry
    debit_account: str
    debit_amount: Union[Decimal, float]

    # Credit entry
    credit_account: str
    credit_amount: Union[Decimal, float]
    
    description: str
    status: str = "pending"  # pending, completed, failed, reversed
    
    # ACID transaction tracking
    session_id: Optional[str] = None
    committed: bool = False
    
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))