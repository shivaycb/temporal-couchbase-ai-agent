"""Account repository with Couchbase ACID transaction support."""

from typing import Dict, Optional, List, Any, Tuple, Union
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from couchbase.exceptions import (
    DocumentNotFoundException,
    CouchbaseException,
    TransactionFailed,
    TransactionExpired,
    TransactionCommitAmbiguous
)
from couchbase.transactions import TransactionConfig
from database.account_schemas import Account, BalanceHold, BalanceUpdate, TransactionJournal
from database.connection import get_sync_cluster, get_sync_scope
from utils.config import config
from utils.logger import transaction_logger, logger
from utils.decimal_utils import to_decimal, from_decimal, add_money, subtract_money
import uuid


class InsufficientFundsError(Exception):
    """Raised when account has insufficient funds."""
    pass


class AccountNotFoundError(Exception):
    """Raised when account is not found."""
    pass


class AccountRepository:
    """Repository for account operations with Couchbase ACID support."""

    @staticmethod
    def get_or_create_account_sync(
        account_number: str,
        customer_name: str,
        initial_balance: Union[float, Decimal, str] = 10000.0
    ) -> Account:
        """Get or create an account using Couchbase key-value operations."""
        scope = get_sync_scope()
        accounts_collection = scope.collection(config.ACCOUNTS_COLLECTION)

        # Try to get existing account using key-value GET
        account_key = f"account::{account_number}"

        try:
            result = accounts_collection.get(account_key)
            account_doc = result.content_as[dict]
            return Account(**account_doc)
        except DocumentNotFoundException:
            # Account doesn't exist, create new one
            pass

        # Create new account with initial balance as Decimal
        initial_balance_decimal = to_decimal(initial_balance)

        account = Account(
            account_number=account_number,
            customer_id=f"CUST_{uuid.uuid4().hex[:8].upper()}",
            customer_name=customer_name,
            balance=initial_balance_decimal,
            available_balance=initial_balance_decimal,
            kyc_verified=True  # For demo purposes
        )

        # Insert account document
        accounts_collection.insert(account_key, account.model_dump())
        logger.info(f"Created new account {account_number} with balance ${float(initial_balance_decimal):.2f}")

        return account

    @staticmethod
    def check_sufficient_funds_sync(
        account_number: str,
        amount: Union[float, Decimal, str]
    ) -> Tuple[bool, float]:
        """Check if account has sufficient funds using key-value GET."""
        scope = get_sync_scope()
        accounts_collection = scope.collection(config.ACCOUNTS_COLLECTION)

        account_key = f"account::{account_number}"

        try:
            result = accounts_collection.get(account_key)
            account = result.content_as[dict]
        except DocumentNotFoundException:
            raise AccountNotFoundError(f"Account {account_number} not found")

        # Convert to Decimal for comparison
        available = from_decimal(account.get("available_balance", 0))
        overdraft_limit = from_decimal(account.get("overdraft_limit", 0))
        amount_decimal = from_decimal(amount)

        # Check if amount can be covered including overdraft
        has_funds = available + overdraft_limit >= amount_decimal

        if not has_funds:
            transaction_logger.log_insufficient_funds(
                account_number=account_number,
                transaction_id="CHECK",
                requested_amount=float(amount_decimal),
                available_balance=float(available)
            )

        return has_funds, float(available)

    @staticmethod
    def execute_transfer_with_acid(
        sender_account: str,
        recipient_account: str,
        amount: Union[float, Decimal, str],
        transaction_id: str,
        description: str = "Transfer"
    ) -> bool:
        """
        Execute a transfer between accounts using Couchbase ACID transactions.

        This implements double-entry bookkeeping with full ACID guarantees:
        - Atomicity: All operations succeed or none do
        - Consistency: Account balances remain consistent
        - Isolation: Concurrent transactions don't interfere
        - Durability: Once committed, changes are permanent
        """
        cluster = get_sync_cluster()
        scope = get_sync_scope()

        # Generate session ID for audit trail
        session_id = f"SESSION_{uuid.uuid4().hex[:8].upper()}"

        transaction_logger.log_acid_transaction(
            session_id=session_id,
            operation="TRANSFER_START",
            status="INITIATED",
            details={
                "from": sender_account,
                "to": recipient_account,
                "amount": float(from_decimal(amount)),
                "transaction_id": transaction_id
            }
        )

        # Get transaction manager from cluster
        transactions = cluster.transactions()

        # Define the transaction logic
        def txn_logic(ctx):
            """Transaction logic executed within Couchbase distributed transaction."""

            # Get collection references
            accounts_collection = scope.collection(config.ACCOUNTS_COLLECTION)
            journal_collection = scope.collection(config.JOURNAL_COLLECTION)
            balance_updates_collection = scope.collection(config.BALANCE_UPDATES_COLLECTION)

            # Document keys
            sender_key = f"account::{sender_account}"
            recipient_key = f"account::{recipient_account}"
            journal_key = f"journal::{transaction_id}"

            # 1. Get sender account within transaction
            try:
                sender_doc = ctx.get(accounts_collection, sender_key)
                sender = sender_doc.content_as[dict]
            except DocumentNotFoundException:
                raise AccountNotFoundError(f"Sender account {sender_account} not found")

            # 2. Check sufficient funds
            sender_available = from_decimal(sender.get("available_balance", 0))
            amount_decimal = from_decimal(amount)

            if sender_available < amount_decimal:
                raise InsufficientFundsError(
                    f"Insufficient funds: Available ${float(sender_available):.2f}, "
                    f"Requested ${float(amount_decimal):.2f}"
                )

            # 3. Get recipient account within transaction
            try:
                recipient_doc = ctx.get(accounts_collection, recipient_key)
                recipient = recipient_doc.content_as[dict]
            except DocumentNotFoundException:
                raise AccountNotFoundError(f"Recipient account {recipient_account} not found")

            # 4. Calculate new balances for sender (debit)
            try:
                new_sender_balance = subtract_money(sender.get("balance", 0), amount)
                new_sender_available = subtract_money(sender.get("available_balance", 0), amount)
            except Exception as e:
                logger.error(f"Error in subtract_money: {e}")
                logger.error(f"sender balance type: {type(sender.get('balance'))}")
                logger.error(f"amount type: {type(amount)}")
                raise

            # Update sender document
            sender["balance"] = new_sender_balance
            sender["available_balance"] = new_sender_available
            sender["last_transaction_at"] = datetime.now(timezone.utc).isoformat()
            sender["updated_at"] = datetime.now(timezone.utc).isoformat()
            sender["transaction_count"] = sender.get("transaction_count", 0) + 1
            sender["total_withdrawals"] = float(from_decimal(sender.get("total_withdrawals", 0))) + float(amount_decimal)

            # Replace sender document within transaction
            ctx.replace(sender_doc, sender)

            # 5. Create sender balance update record
            sender_update_id = f"balance_update::{transaction_id}::sender"
            sender_update = BalanceUpdate(
                account_number=sender_account,
                transaction_id=transaction_id,
                operation="debit",
                amount=amount_decimal,
                previous_balance=from_decimal(sender_doc.content_as[dict].get("balance", 0)),
                new_balance=new_sender_balance,
                session_id=session_id
            )

            # Insert balance update within transaction
            ctx.insert(balance_updates_collection, sender_update_id, sender_update.model_dump())

            # 6. Calculate new balances for recipient (credit)
            new_recipient_balance = add_money(recipient.get("balance", 0), amount)
            new_recipient_available = add_money(recipient.get("available_balance", 0), amount)

            # Update recipient document
            recipient["balance"] = new_recipient_balance
            recipient["available_balance"] = new_recipient_available
            recipient["last_transaction_at"] = datetime.now(timezone.utc).isoformat()
            recipient["updated_at"] = datetime.now(timezone.utc).isoformat()
            recipient["transaction_count"] = recipient.get("transaction_count", 0) + 1
            recipient["total_deposits"] = float(from_decimal(recipient.get("total_deposits", 0))) + float(amount_decimal)

            # Replace recipient document within transaction
            ctx.replace(recipient_doc, recipient)

            # 7. Create recipient balance update record
            recipient_update_id = f"balance_update::{transaction_id}::recipient"
            recipient_update = BalanceUpdate(
                account_number=recipient_account,
                transaction_id=transaction_id,
                operation="credit",
                amount=amount_decimal,
                previous_balance=from_decimal(recipient_doc.content_as[dict].get("balance", 0)),
                new_balance=new_recipient_balance,
                session_id=session_id
            )

            # Insert balance update within transaction
            ctx.insert(balance_updates_collection, recipient_update_id, recipient_update.model_dump())

            # 8. Create journal entry for double-entry bookkeeping
            journal_entry = TransactionJournal(
                transaction_id=transaction_id,
                debit_account=sender_account,
                debit_amount=amount_decimal,
                credit_account=recipient_account,
                credit_amount=amount_decimal,
                description=description,
                status="completed",
                session_id=session_id,
                committed=True
            )

            # Insert journal entry within transaction
            ctx.insert(journal_collection, journal_key, journal_entry.model_dump())

            # Log balance updates (outside transaction for audit)
            transaction_logger.log_balance_update(
                account_number=sender_account,
                transaction_id=transaction_id,
                old_balance=sender_doc.content_as[dict].get("balance"),
                new_balance=new_sender_balance,
                amount=amount_decimal,
                operation="DEBIT"
            )

            transaction_logger.log_balance_update(
                account_number=recipient_account,
                transaction_id=transaction_id,
                old_balance=recipient_doc.content_as[dict].get("balance"),
                new_balance=new_recipient_balance,
                amount=amount_decimal,
                operation="CREDIT"
            )

            return True

        # Execute the transaction with automatic retries
        try:
            result = transactions.run(txn_logic)

            transaction_logger.log_acid_transaction(
                session_id=session_id,
                operation="TRANSFER_COMPLETE",
                status="SUCCESS",
                details={
                    "transaction_id": transaction_id,
                    "committed": True
                }
            )

            return result

        except InsufficientFundsError as e:
            transaction_logger.log_acid_transaction(
                session_id=session_id,
                operation="TRANSFER_FAILED",
                status="INSUFFICIENT_FUNDS",
                details={
                    "transaction_id": transaction_id,
                    "error": str(e)
                }
            )
            raise

        except AccountNotFoundError as e:
            transaction_logger.log_acid_transaction(
                session_id=session_id,
                operation="TRANSFER_FAILED",
                status="ACCOUNT_NOT_FOUND",
                details={
                    "transaction_id": transaction_id,
                    "error": str(e)
                }
            )
            raise

        except (TransactionFailed, TransactionExpired, TransactionCommitAmbiguous) as e:
            transaction_logger.log_acid_transaction(
                session_id=session_id,
                operation="TRANSFER_FAILED",
                status="TRANSACTION_ERROR",
                details={
                    "transaction_id": transaction_id,
                    "error": str(e),
                    "error_type": type(e).__name__
                }
            )
            logger.error(f"Couchbase transaction failed: {e}")
            raise

        except Exception as e:
            transaction_logger.log_acid_transaction(
                session_id=session_id,
                operation="TRANSFER_FAILED",
                status="ERROR",
                details={
                    "transaction_id": transaction_id,
                    "error": str(e)
                }
            )
            logger.error(f"ACID transaction failed: {e}")
            raise

    @staticmethod
    def place_hold_sync(
        account_number: str,
        amount: Union[float, Decimal, str],
        transaction_id: str,
        reason: str = "Transaction processing",
        duration_hours: int = 24
    ) -> str:
        """Place a hold on account funds using Couchbase operations."""
        scope = get_sync_scope()
        accounts_collection = scope.collection(config.ACCOUNTS_COLLECTION)
        holds_collection = scope.collection(config.HOLDS_COLLECTION)

        account_key = f"account::{account_number}"

        # Get account
        try:
            result = accounts_collection.get(account_key)
            account = result.content_as[dict]
        except DocumentNotFoundException:
            raise AccountNotFoundError(f"Account {account_number} not found")

        # Convert to Decimal for comparison
        available_decimal = from_decimal(account.get("available_balance", 0))
        amount_decimal = from_decimal(amount)

        if available_decimal < amount_decimal:
            raise InsufficientFundsError(f"Insufficient available balance for hold")

        # Create hold record
        hold = BalanceHold(
            account_number=account_number,
            transaction_id=transaction_id,
            amount=amount_decimal,
            reason=reason,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=duration_hours)
        )

        # Calculate new available balance
        new_available = subtract_money(available_decimal, amount_decimal)

        # Update account available balance
        account["available_balance"] = new_available

        # Add hold to account's holds list
        if "holds" not in account:
            account["holds"] = []
        account["holds"].append(hold.model_dump())

        # Replace account document
        accounts_collection.replace(account_key, account)

        # Store hold record separately for easier querying
        hold_key = f"hold::{hold.hold_id}"
        holds_collection.insert(hold_key, hold.model_dump())

        logger.info(f"Placed hold {hold.hold_id} for ${float(amount_decimal):.2f} on account {account_number}")

        return hold.hold_id

    @staticmethod
    def release_hold_sync(hold_id: str) -> bool:
        """Release a hold on account funds using Couchbase operations."""
        scope = get_sync_scope()
        accounts_collection = scope.collection(config.ACCOUNTS_COLLECTION)
        holds_collection = scope.collection(config.HOLDS_COLLECTION)

        hold_key = f"hold::{hold_id}"

        # Get the hold
        try:
            result = holds_collection.get(hold_key)
            hold = result.content_as[dict]
        except DocumentNotFoundException:
            return False

        # Check if already released
        if hold.get("released"):
            return False

        # Mark hold as released
        hold["released"] = True
        hold["released_at"] = datetime.now(timezone.utc).isoformat()
        holds_collection.replace(hold_key, hold)

        # Update account available balance
        account_key = f"account::{hold['account_number']}"

        try:
            result = accounts_collection.get(account_key)
            account = result.content_as[dict]
        except DocumentNotFoundException:
            logger.error(f"Account {hold['account_number']} not found when releasing hold")
            return False

        # Add hold amount back to available balance
        amount_decimal = from_decimal(hold["amount"])
        new_available = add_money(account.get("available_balance", 0), amount_decimal)
        account["available_balance"] = new_available

        # Remove hold from account's holds list
        if "holds" in account:
            account["holds"] = [h for h in account["holds"] if h.get("hold_id") != hold_id]

        # Replace account document
        accounts_collection.replace(account_key, account)

        logger.info(f"Released hold {hold_id} for ${float(amount_decimal):.2f} on account {hold['account_number']}")

        return True

    @staticmethod
    def get_account_balance_sync(account_number: str) -> Dict[str, float]:
        """Get account balances using key-value GET."""
        scope = get_sync_scope()
        accounts_collection = scope.collection(config.ACCOUNTS_COLLECTION)

        account_key = f"account::{account_number}"

        try:
            result = accounts_collection.get(account_key)
            account = result.content_as[dict]
        except DocumentNotFoundException:
            raise AccountNotFoundError(f"Account {account_number} not found")

        return {
            "balance": account.get("balance", 0),
            "available_balance": account.get("available_balance", 0),
            "overdraft_limit": account.get("overdraft_limit", 0)
        }

    @staticmethod
    def get_account_sync(account_number: str) -> Account:
        """Get account details including holds using key-value GET."""
        scope = get_sync_scope()
        accounts_collection = scope.collection(config.ACCOUNTS_COLLECTION)

        account_key = f"account::{account_number}"

        try:
            result = accounts_collection.get(account_key)
            account_doc = result.content_as[dict]
        except DocumentNotFoundException:
            raise AccountNotFoundError(f"Account {account_number} not found")

        # Convert document to Account model
        return Account(**account_doc)

    @staticmethod
    def get_transaction_history_sync(account_number: str, limit: int = 10) -> List[Dict]:
        """Get transaction history for an account using N1QL query."""
        cluster = get_sync_cluster()

        # Query balance updates using N1QL
        query = f"""
            SELECT bu.*
            FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.BALANCE_UPDATES_COLLECTION}` AS bu
            WHERE bu.account_number = $account_number
            ORDER BY bu.timestamp DESC
            LIMIT $limit
        """

        try:
            result = cluster.query(
                query,
                account_number=account_number,
                limit=limit
            )

            updates = [row for row in result]
            return updates

        except CouchbaseException as e:
            logger.error(f"Error querying transaction history: {e}")
            return []
