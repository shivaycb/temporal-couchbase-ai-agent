"""Data access layer for Couchbase operations."""

from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from database.connection import get_cluster, get_scope, get_collection, db
from utils.decimal_utils import to_decimal, from_decimal, decimal_to_float
from database.schemas import (
    Transaction, TransactionDecision, AuditEvent, SystemMetric,
    Customer, Rule, HumanReview, Notification, RuleStatus, NotificationStatus
)
from utils.config import config
from couchbase.exceptions import DocumentNotFoundException
from couchbase.options import QueryOptions
import logging
import uuid
import json

logger = logging.getLogger(__name__)

def serialize_doc(doc: Dict) -> Dict:
    """Convert Couchbase document to JSON-serializable format."""
    if doc is None:
        return None

    result = doc.copy()

    # Convert datetime objects to ISO format strings and Decimal to string
    for key, value in result.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, Decimal):
            # Convert Decimal to string for JSON serialization
            result[key] = str(value)
        elif isinstance(value, dict):
            result[key] = serialize_doc(value)
        elif isinstance(value, list):
            result[key] = [
                serialize_doc(item) if isinstance(item, dict)
                else item.isoformat() if isinstance(item, datetime)
                else str(item) if isinstance(item, Decimal)
                else item
                for item in value
            ]

    return result


class CustomerRepository:
    """Repository for customer operations."""

    @staticmethod
    async def create_customer(customer: Customer) -> str:
        """Create a new customer."""
        collection = get_collection('customers')
        doc_data = customer.model_dump()

        # Use customer_id as document key
        doc_key = f"customer::{customer.customer_id}"
        collection.upsert(doc_key, doc_data)

        return customer.customer_id

    @staticmethod
    async def get_customer(customer_id: str) -> Optional[Dict]:
        """Get customer by ID."""
        try:
            collection = get_collection('customers')
            doc_key = f"customer::{customer_id}"
            result = collection.get(doc_key)
            return serialize_doc(result.content_as[dict])
        except DocumentNotFoundException:
            # Try N1QL query as fallback
            cluster = get_cluster()
            query = f"""
            SELECT c.*
            FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.CUSTOMERS_COLLECTION}` c
            WHERE c.customer_id = $customer_id
            LIMIT 1
            """
            result = cluster.query(query, QueryOptions(named_parameters={'customer_id': customer_id}))
            rows = list(result.rows())
            return serialize_doc(rows[0]['c']) if rows else None

    @staticmethod
    def get_customer_sync(customer_id: str) -> Optional[Dict]:
        """Get customer by ID (synchronous)."""
        try:
            collection = get_collection('customers')
            doc_key = f"customer::{customer_id}"
            result = collection.get(doc_key)
            return serialize_doc(result.content_as[dict])
        except DocumentNotFoundException:
            # Try N1QL query as fallback
            cluster = get_cluster()
            query = f"""
            SELECT c.*
            FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.CUSTOMERS_COLLECTION}` c
            WHERE c.customer_id = $customer_id
            LIMIT 1
            """
            result = cluster.query(query, QueryOptions(named_parameters={'customer_id': customer_id}))
            rows = list(result.rows())
            return serialize_doc(rows[0]['c']) if rows else None

    @staticmethod
    async def update_customer_profile(
        customer_id: str,
        transaction_count: int,
        total_amount: float,
        avg_amount: float,
        last_transaction_date: datetime
    ):
        """Update customer profile with transaction statistics."""
        try:
            cluster = get_cluster()

            # Use N1QL UPDATE with UPSERT
            query = f"""
            UPSERT INTO `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.CUSTOMERS_COLLECTION}` (KEY, VALUE)
            VALUES (
                $doc_key,
                {{
                    "customer_id": $customer_id,
                    "transaction_count": $transaction_count,
                    "total_amount": $total_amount,
                    "average_transaction_amount": $avg_amount,
                    "last_transaction_date": $last_transaction_date,
                    "updated_at": $updated_at,
                    "created_at": COALESCE(
                        (SELECT RAW created_at FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.CUSTOMERS_COLLECTION}`
                         WHERE customer_id = $customer_id LIMIT 1)[0],
                        $created_at
                    )
                }}
            )
            """

            now = datetime.now(timezone.utc).isoformat()
            doc_key = f"customer::{customer_id}"

            cluster.query(
                query,
                QueryOptions(named_parameters={
                    'doc_key': doc_key,
                    'customer_id': customer_id,
                    'transaction_count': transaction_count,
                    'total_amount': str(to_decimal(total_amount)),
                    'avg_amount': str(to_decimal(avg_amount)),
                    'last_transaction_date': last_transaction_date.isoformat(),
                    'updated_at': now,
                    'created_at': now
                })
            )

            logger.info(f"Updated customer profile for {customer_id}")

        except Exception as e:
            logger.error(f"Error updating customer profile for {customer_id}: {e}")
            raise

    @staticmethod
    async def get_or_create_customer(customer_data: Dict) -> str:
        """Get existing customer or create new one."""
        cluster = get_cluster()

        # Check if customer exists by legal_name
        query = f"""
        SELECT c.customer_id
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.CUSTOMERS_COLLECTION}` c
        WHERE c.legal_name = $name
        LIMIT 1
        """

        result = cluster.query(query, QueryOptions(named_parameters={'name': customer_data.get("name")}))
        rows = list(result.rows())

        if rows:
            return rows[0]['customer_id']

        # Create new customer
        customer = Customer(
            legal_name=customer_data.get("name", "Unknown"),
            display_name=customer_data.get("name", "Unknown"),
            customer_type="business" if "Corp" in customer_data.get("name", "") else "individual",
            country=customer_data.get("country", "US")
        )

        return await CustomerRepository.create_customer(customer)

    @staticmethod
    def get_or_create_customer_sync(customer_data: Dict) -> str:
        """Get existing customer or create new one (synchronous)."""
        cluster = get_cluster()

        # Check if customer exists by legal_name
        query = f"""
        SELECT c.customer_id
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.CUSTOMERS_COLLECTION}` c
        WHERE c.legal_name = $name
        LIMIT 1
        """

        result = cluster.query(query, QueryOptions(named_parameters={'name': customer_data.get("name")}))
        rows = list(result.rows())

        if rows:
            return rows[0]['customer_id']

        # Create new customer
        customer = Customer(
            legal_name=customer_data.get("name", "Unknown"),
            display_name=customer_data.get("name", "Unknown"),
            customer_type="business" if "Corp" in customer_data.get("name", "") else "individual",
            country=customer_data.get("country", "US")
        )

        collection = get_collection('customers')
        doc_key = f"customer::{customer.customer_id}"
        collection.upsert(doc_key, customer.model_dump())

        return customer.customer_id


class TransactionRepository:
    """Repository for transaction operations."""

    @staticmethod
    async def create_transaction(transaction: Transaction) -> str:
        """Create a new transaction."""
        # Ensure customer records exist
        if "customer_id" not in transaction.sender:
            transaction.sender["customer_id"] = await CustomerRepository.get_or_create_customer(
                transaction.sender
            )

        collection = get_collection('transactions')
        doc_key = f"transaction::{transaction.transaction_id}"
        doc_data = transaction.model_dump()
        collection.upsert(doc_key, doc_data)

        return transaction.transaction_id

    @staticmethod
    async def get_transaction(transaction_id: str) -> Optional[Dict]:
        """Get transaction by ID."""
        try:
            collection = get_collection('transactions')
            doc_key = f"transaction::{transaction_id}"
            result = collection.get(doc_key)
            return serialize_doc(result.content_as[dict])
        except DocumentNotFoundException:
            # Try N1QL query as fallback
            cluster = get_cluster()
            query = f"""
            SELECT t.*
            FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}` t
            WHERE t.transaction_id = $transaction_id
            LIMIT 1
            """
            result = cluster.query(query, QueryOptions(named_parameters={'transaction_id': transaction_id}))
            rows = list(result.rows())
            return serialize_doc(rows[0]['t']) if rows else None

    @staticmethod
    async def update_status(transaction_id: str, status: str, substatus: Optional[str] = None):
        """Update transaction status."""
        cluster = get_cluster()

        # N1QL UPDATE to set status and append to processing_stages array
        query = f"""
        UPDATE `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}`
        SET status = $status,
            updated_at = $updated_at,
            processing_stages = ARRAY_APPEND(IFMISSING(processing_stages, []), {{
                "stage": $status,
                "timestamp": $timestamp,
                "substatus": $substatus
            }})
        WHERE transaction_id = $transaction_id
        """

        now = datetime.now(timezone.utc).isoformat()

        cluster.query(
            query,
            QueryOptions(named_parameters={
                'transaction_id': transaction_id,
                'status': status,
                'substatus': substatus,
                'updated_at': now,
                'timestamp': now
            })
        )

    @staticmethod
    def update_status_sync(transaction_id: str, status: str, substatus: Optional[str] = None):
        """Update transaction status (synchronous)."""
        cluster = get_cluster()

        # N1QL UPDATE to set status and append to processing_stages array
        query = f"""
        UPDATE `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}`
        SET status = $status,
            updated_at = $updated_at,
            processing_stages = ARRAY_APPEND(IFMISSING(processing_stages, []), {{
                "stage": $status,
                "timestamp": $timestamp,
                "substatus": $substatus
            }})
        WHERE transaction_id = $transaction_id
        """

        now = datetime.now(timezone.utc).isoformat()

        cluster.query(
            query,
            QueryOptions(named_parameters={
                'transaction_id': transaction_id,
                'status': status,
                'substatus': substatus,
                'updated_at': now,
                'timestamp': now
            })
        )

    @staticmethod
    async def store_embedding(transaction_id: str, embedding: List[float], model: str = "cohere"):
        """Store vector embedding for transaction."""
        cluster = get_cluster()

        query = f"""
        UPDATE `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}`
        SET embedding = $embedding,
            embedding_model = $model,
            updated_at = $updated_at
        WHERE transaction_id = $transaction_id
        """

        cluster.query(
            query,
            QueryOptions(named_parameters={
                'transaction_id': transaction_id,
                'embedding': embedding,
                'model': model,
                'updated_at': datetime.now(timezone.utc).isoformat()
            })
        )

    @staticmethod
    def store_embedding_sync(transaction_id: str, embedding: List[float], model: str = "cohere"):
        """Store vector embedding for transaction (synchronous)."""
        cluster = get_cluster()

        query = f"""
        UPDATE `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}`
        SET embedding = $embedding,
            embedding_model = $model,
            updated_at = $updated_at
        WHERE transaction_id = $transaction_id
        """

        cluster.query(
            query,
            QueryOptions(named_parameters={
                'transaction_id': transaction_id,
                'embedding': embedding,
                'model': model,
                'updated_at': datetime.now(timezone.utc).isoformat()
            })
        )

    @staticmethod
    def get_customer_history_sync(customer_id: str) -> Dict:
        """Get customer transaction history (synchronous for Temporal)."""
        cluster = get_cluster()

        # First try to get customer profile
        customer_query = f"""
        SELECT c.*
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.CUSTOMERS_COLLECTION}` c
        WHERE c.customer_id = $customer_id
        LIMIT 1
        """

        result = cluster.query(customer_query, QueryOptions(named_parameters={'customer_id': customer_id}))
        customer_rows = list(result.rows())

        if customer_rows:
            customer = customer_rows[0]['c']
            # Use customer profile data
            base_data = {
                "customer_since": customer.get("created_at"),
                "risk_level": customer.get("risk_profile", {}).get("risk_level", "medium"),
                "kyc_status": customer.get("risk_profile", {}).get("kyc_status", "pending"),
                "avg_transaction_amount": customer.get("behavior_profile", {}).get("avg_transaction_amount", 0),
                "transaction_frequency": customer.get("behavior_profile", {}).get("transaction_frequency", "unknown"),
                "common_recipients": customer.get("behavior_profile", {}).get("common_recipients", [])
            }
        else:
            base_data = {
                "customer_since": None,
                "risk_level": "unknown",
                "kyc_status": "unknown"
            }

        # Get transaction history
        ninety_days_ago = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        transactions_query = f"""
        SELECT t.*
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}` t
        WHERE t.sender.customer_id = $customer_id
        AND t.created_at >= $cutoff_date
        LIMIT 100
        """

        result = cluster.query(
            transactions_query,
            QueryOptions(named_parameters={
                'customer_id': customer_id,
                'cutoff_date': ninety_days_ago
            })
        )
        transactions = [row['t'] for row in result.rows()]

        if transactions:
            # Handle Decimal values in sum operation
            total_amount = sum(from_decimal(t.get("amount", 0)) for t in transactions)
            risk_incidents = sum(1 for t in transactions if t.get("status") in ["rejected", "escalated"])

            recipients = []
            for t in transactions[:10]:
                if "recipient" in t and "name" in t["recipient"]:
                    recipients.append(t["recipient"]["name"])

            base_data.update({
                "total_transactions": len(transactions),
                "avg_amount": total_amount / len(transactions) if transactions else 0,
                "total_amount": total_amount,
                "risk_incidents": risk_incidents,
                "common_recipients": list(set(recipients)) if not base_data.get("common_recipients") else base_data["common_recipients"]
            })
        else:
            base_data.update({
                "total_transactions": 0,
                "avg_amount": 0,
                "total_amount": 0,
                "risk_incidents": 0
            })

        return base_data


class DecisionRepository:
    """Repository for decision operations."""

    @staticmethod
    async def create_decision(decision: TransactionDecision) -> str:
        """Create a new decision."""
        collection = get_collection('decisions')
        doc_key = f"decision::{decision.decision_id}"
        doc_data = decision.model_dump()
        collection.upsert(doc_key, doc_data)

        return decision.decision_id

    @staticmethod
    def create_decision_sync(decision: TransactionDecision) -> str:
        """Create a new decision (synchronous)."""
        collection = get_collection('decisions')
        doc_key = f"decision::{decision.decision_id}"
        doc_data = decision.model_dump()
        collection.upsert(doc_key, doc_data)

        return decision.decision_id

    @staticmethod
    async def get_decision_by_transaction(transaction_id: str) -> Optional[Dict]:
        """Get decision by transaction ID."""
        cluster = get_cluster()
        query = f"""
        SELECT d.*
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.DECISIONS_COLLECTION}` d
        WHERE d.transaction_id = $transaction_id
        LIMIT 1
        """

        result = cluster.query(query, QueryOptions(named_parameters={'transaction_id': transaction_id}))
        rows = list(result.rows())
        return serialize_doc(rows[0]['d']) if rows else None

    @staticmethod
    async def hybrid_search_similar_transactions(
        embedding: Optional[List[float]],
        transaction_details: Dict[str, Any],
        limit: int = 10
    ) -> List[Dict]:
        """Find similar transactions using hybrid search (vector + traditional indexes)."""
        cluster = get_cluster()

        # Extract search parameters
        transaction_type = transaction_details.get('transaction_type')
        amount = transaction_details.get('amount', 0)
        sender_country = transaction_details.get('sender', {}).get('country')
        recipient_country = transaction_details.get('recipient', {}).get('country')

        try:
            if embedding:
                # Hybrid search: Use Couchbase FTS with vector search + traditional filters
                # First, perform vector search using SEARCH() function
                vector_query = f"""
                SELECT META(t).id as doc_id,
                       t.transaction_id,
                       t.amount,
                       t.transaction_type,
                       t.sender,
                       t.recipient,
                       SEARCH_SCORE() as vector_score
                FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}` t
                WHERE t.transaction_type = $transaction_type
                AND SEARCH(t, {{
                    "query": {{"match_none": {{}}}},
                    "knn": [{{
                        "field": "embedding",
                        "vector": $embedding,
                        "k": $num_candidates
                    }}]
                }})
                LIMIT $vector_limit
                """

                # Get vector search results
                vector_result = cluster.query(
                    vector_query,
                    QueryOptions(named_parameters={
                        'transaction_type': transaction_type,
                        'embedding': embedding,
                        'num_candidates': limit * 5,
                        'vector_limit': limit // 2
                    })
                )
                vector_results = list(vector_result.rows())

                # Traditional search for exact matches
                traditional_query = f"""
                SELECT t.transaction_id,
                       t.amount,
                       t.transaction_type,
                       t.sender,
                       t.recipient,
                       1.0 as traditional_score
                FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}` t
                WHERE (
                    (t.transaction_type = $transaction_type
                     AND t.amount BETWEEN $amount_min AND $amount_max)
                    OR
                    (t.sender.country = $sender_country
                     AND t.recipient.country = $recipient_country)
                )
                LIMIT $traditional_limit
                """

                traditional_result = cluster.query(
                    traditional_query,
                    QueryOptions(named_parameters={
                        'transaction_type': transaction_type,
                        'amount_min': amount * 0.8,
                        'amount_max': amount * 1.2,
                        'sender_country': sender_country,
                        'recipient_country': recipient_country,
                        'traditional_limit': limit // 2
                    })
                )
                traditional_results = list(traditional_result.rows())

                # Combine results
                all_results = []
                seen_ids = set()

                # Add vector search results
                for row in vector_results:
                    tid = row.get('transaction_id')
                    if tid not in seen_ids:
                        seen_ids.add(tid)
                        all_results.append({
                            'transaction_id': tid,
                            'amount': row.get('amount'),
                            'transaction_type': row.get('transaction_type'),
                            'sender': row.get('sender'),
                            'recipient': row.get('recipient'),
                            'vector_score': row.get('vector_score', 0),
                            'traditional_score': 0,
                            'search_method': 'vector'
                        })

                # Add traditional search results
                for row in traditional_results:
                    tid = row.get('transaction_id')
                    if tid not in seen_ids:
                        seen_ids.add(tid)
                        all_results.append({
                            'transaction_id': tid,
                            'amount': row.get('amount'),
                            'transaction_type': row.get('transaction_type'),
                            'sender': row.get('sender'),
                            'recipient': row.get('recipient'),
                            'vector_score': 0,
                            'traditional_score': row.get('traditional_score', 1.0),
                            'search_method': 'traditional'
                        })

            else:
                # Pure traditional search
                traditional_query = f"""
                SELECT t.transaction_id,
                       t.amount,
                       t.transaction_type,
                       t.sender,
                       t.recipient
                FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}` t
                WHERE (
                    (t.transaction_type = $transaction_type
                     AND t.amount BETWEEN $amount_min AND $amount_max)
                    OR
                    (t.sender.country = $sender_country
                     AND t.recipient.country = $recipient_country)
                )
                LIMIT $limit
                """

                result = cluster.query(
                    traditional_query,
                    QueryOptions(named_parameters={
                        'transaction_type': transaction_type,
                        'amount_min': amount * 0.8,
                        'amount_max': amount * 1.2,
                        'sender_country': sender_country,
                        'recipient_country': recipient_country,
                        'limit': limit
                    })
                )

                all_results = []
                for row in result.rows():
                    all_results.append({
                        'transaction_id': row.get('transaction_id'),
                        'amount': row.get('amount'),
                        'transaction_type': row.get('transaction_type'),
                        'sender': row.get('sender'),
                        'recipient': row.get('recipient'),
                        'vector_score': 0,
                        'traditional_score': 1.0,
                        'search_method': 'traditional'
                    })

            # Calculate similarity features and combined score
            enriched_results = []
            for result_row in all_results:
                row_amount = from_decimal(result_row.get('amount', 0))

                # Amount proximity score
                if amount == 0:
                    amount_score = 0
                else:
                    amount_diff = abs(row_amount - amount) / amount
                    amount_score = max(0, 1 - min(1, amount_diff))

                # Geographic score
                geo_score = 1.0 if (
                    result_row.get('sender', {}).get('country') == sender_country and
                    result_row.get('recipient', {}).get('country') == recipient_country
                ) else 0.5

                # Type score
                type_score = 1.0 if result_row.get('transaction_type') == transaction_type else 0.3

                # Combined score with weights
                combined_score = (
                    result_row.get('vector_score', 0) * 0.4 +
                    result_row.get('traditional_score', 0) * 0.2 +
                    amount_score * 0.2 +
                    geo_score * 0.1 +
                    type_score * 0.1
                )

                result_row['similarity_features'] = {
                    'amount_score': amount_score,
                    'geo_score': geo_score,
                    'type_score': type_score
                }
                result_row['combined_score'] = combined_score
                enriched_results.append(result_row)

            # Sort by combined score
            enriched_results.sort(key=lambda x: x['combined_score'], reverse=True)
            enriched_results = enriched_results[:limit]

            # Join with decisions
            final_results = []
            for tx_row in enriched_results:
                decision_query = f"""
                SELECT d.decision,
                       d.confidence_score,
                       d.risk_score,
                       d.risk_factors
                FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.DECISIONS_COLLECTION}` d
                WHERE d.transaction_id = $transaction_id
                LIMIT 1
                """

                decision_result = cluster.query(
                    decision_query,
                    QueryOptions(named_parameters={'transaction_id': tx_row['transaction_id']})
                )
                decision_rows = list(decision_result.rows())

                if decision_rows:
                    decision = decision_rows[0]
                    final_results.append({
                        'transaction_id': tx_row['transaction_id'],
                        'amount': tx_row['amount'],
                        'transaction_type': tx_row['transaction_type'],
                        'sender': tx_row['sender'],
                        'recipient': tx_row['recipient'],
                        'decision': decision.get('decision'),
                        'confidence': decision.get('confidence_score'),
                        'risk_score': decision.get('risk_score'),
                        'risk_factors': decision.get('risk_factors'),
                        'similarity_score': tx_row['combined_score'],
                        'similarity_features': tx_row['similarity_features'],
                        'search_method': tx_row.get('search_method')
                    })

            return [serialize_doc(doc) for doc in final_results]

        except Exception as e:
            logger.error(f"Hybrid search error: {e}")
            # Fallback to simple vector search if hybrid fails
            if embedding:
                return await DecisionRepository.vector_search_similar_transactions(
                    embedding, transaction_type, limit
                )
            raise e

    @staticmethod
    async def vector_search_similar_transactions(
        embedding: List[float],
        transaction_type: str,
        limit: int = 10
    ) -> List[Dict]:
        """Fallback vector-only search for similar transactions."""
        cluster = get_cluster()

        try:
            # Couchbase FTS vector search using SEARCH() function
            query = f"""
            SELECT t.transaction_id,
                   t.amount,
                   t.transaction_type,
                   t.sender,
                   t.recipient,
                   SEARCH_SCORE() as similarity_score
            FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}` t
            WHERE t.transaction_type = $transaction_type
            AND SEARCH(t, {{
                "query": {{"match_none": {{}}}},
                "knn": [{{
                    "field": "embedding",
                    "vector": $embedding,
                    "k": $num_candidates
                }}]
            }})
            ORDER BY SEARCH_SCORE() DESC
            LIMIT $limit
            """

            result = cluster.query(
                query,
                QueryOptions(named_parameters={
                    'transaction_type': transaction_type,
                    'embedding': embedding,
                    'num_candidates': limit * 10,
                    'limit': limit
                })
            )

            transactions = list(result.rows())

            # Join with decisions
            final_results = []
            for tx_row in transactions:
                decision_query = f"""
                SELECT d.decision,
                       d.confidence_score,
                       d.risk_score,
                       d.risk_factors
                FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.DECISIONS_COLLECTION}` d
                WHERE d.transaction_id = $transaction_id
                LIMIT 1
                """

                decision_result = cluster.query(
                    decision_query,
                    QueryOptions(named_parameters={'transaction_id': tx_row['transaction_id']})
                )
                decision_rows = list(decision_result.rows())

                if decision_rows:
                    decision = decision_rows[0]
                    final_results.append({
                        'transaction_id': tx_row['transaction_id'],
                        'amount': tx_row['amount'],
                        'transaction_type': tx_row['transaction_type'],
                        'sender': tx_row['sender'],
                        'recipient': tx_row['recipient'],
                        'decision': decision.get('decision'),
                        'confidence': decision.get('confidence_score'),
                        'risk_score': decision.get('risk_score'),
                        'risk_factors': decision.get('risk_factors'),
                        'similarity_score': tx_row.get('similarity_score')
                    })

            return [serialize_doc(doc) for doc in final_results]

        except Exception as e:
            logger.error(f"Vector search error: {e}")
            raise e

    @staticmethod
    async def graph_network_analysis(
        account_id: str,
        max_depth: int = 3,
        time_window_days: int = 30
    ) -> Dict[str, Any]:
        """Analyze transaction networks for fraud detection using recursive N1QL queries."""
        cluster = get_cluster()
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=time_window_days)).isoformat()

        try:
            # Step 1: Get initial transactions involving the account
            initial_query = f"""
            SELECT t.transaction_id,
                   t.amount,
                   t.timestamp,
                   t.sender.account_number as sender_account,
                   t.recipient.account_number as recipient_account
            FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}` t
            WHERE (t.sender.account_number = $account_id
                   OR t.recipient.account_number = $account_id)
            AND t.timestamp >= $cutoff_date
            """

            result = cluster.query(
                initial_query,
                QueryOptions(named_parameters={
                    'account_id': account_id,
                    'cutoff_date': cutoff_date
                })
            )
            initial_transactions = list(result.rows())

            if not initial_transactions:
                return {
                    "account_id": account_id,
                    "analysis_period_days": time_window_days,
                    "max_depth_analyzed": max_depth,
                    "total_networks_found": 0,
                    "message": "No transaction networks found for this account"
                }

            # Step 2: Recursive traversal to find connected transactions
            all_connected_transactions = []
            visited_accounts = set([account_id])
            current_level_accounts = set([tx['recipient_account'] for tx in initial_transactions])

            for depth in range(max_depth):
                if not current_level_accounts:
                    break

                # Remove already visited accounts
                current_level_accounts = current_level_accounts - visited_accounts
                if not current_level_accounts:
                    break

                # Find transactions where the sender is in current level
                level_query = f"""
                SELECT t.transaction_id,
                       t.amount,
                       t.timestamp,
                       t.sender.account_number as sender_account,
                       t.recipient.account_number as recipient_account,
                       {depth + 1} as chain_depth
                FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.TRANSACTIONS_COLLECTION}` t
                WHERE t.sender.account_number IN $accounts
                AND t.timestamp >= $cutoff_date
                """

                result = cluster.query(
                    level_query,
                    QueryOptions(named_parameters={
                        'accounts': list(current_level_accounts),
                        'cutoff_date': cutoff_date
                    })
                )
                level_transactions = list(result.rows())

                all_connected_transactions.extend(level_transactions)
                visited_accounts.update(current_level_accounts)

                # Get next level accounts
                current_level_accounts = set([tx['recipient_account'] for tx in level_transactions])

            # Step 3: Analyze the network
            total_transactions = len(initial_transactions) + len(all_connected_transactions)
            network_size = len(all_connected_transactions)

            # Calculate total amount in network
            total_network_amount = sum(
                from_decimal(tx.get('amount', 0))
                for tx in all_connected_transactions
            )

            # Get unique accounts
            all_accounts = set()
            for tx in initial_transactions + all_connected_transactions:
                all_accounts.add(tx.get('sender_account'))
                all_accounts.add(tx.get('recipient_account'))

            # Detect suspicious patterns
            # Rapid cycling: money returns to original account
            rapid_cycling = False
            for tx in all_connected_transactions:
                if tx.get('chain_depth') == max_depth and tx.get('recipient_account') == account_id:
                    rapid_cycling = True
                    break

            # Layering: many small transactions
            small_transactions = [
                tx for tx in all_connected_transactions
                if from_decimal(tx.get('amount', 0)) < 1000
            ]
            potential_layering = len(small_transactions) >= 5

            suspicious_networks_count = 1 if (rapid_cycling or potential_layering) else 0

            return {
                "account_id": account_id,
                "analysis_period_days": time_window_days,
                "max_depth_analyzed": max_depth,
                "total_networks_found": 1 if total_transactions > 0 else 0,
                "max_network_size": network_size,
                "total_amount_in_networks": float(total_network_amount),
                "suspicious_networks_count": suspicious_networks_count,
                "unique_accounts_in_networks": len(all_accounts),
                "risk_indicators": {
                    "has_suspicious_patterns": suspicious_networks_count > 0,
                    "large_network_detected": network_size > 10,
                    "high_value_network": total_network_amount > 100000
                }
            }

        except Exception as e:
            logger.error(f"Graph network analysis error: {e}")
            raise e


class RuleRepository:
    """Repository for rule operations."""

    @staticmethod
    async def create_rule(rule: Rule) -> str:
        """Create a new rule."""
        collection = get_collection('rules')
        doc_key = f"rule::{rule.rule_id}"
        doc_data = rule.model_dump()
        collection.upsert(doc_key, doc_data)

        return rule.rule_id

    @staticmethod
    async def get_active_rules() -> List[Dict]:
        """Get all active rules."""
        cluster = get_cluster()
        query = f"""
        SELECT r.*
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.RULES_COLLECTION}` r
        WHERE r.status = $status
        ORDER BY r.priority DESC
        LIMIT 100
        """

        result = cluster.query(
            query,
            QueryOptions(named_parameters={'status': RuleStatus.ACTIVE.value})
        )

        rules = [row['r'] for row in result.rows()]
        return [serialize_doc(r) for r in rules]

    @staticmethod
    def get_active_rules_sync() -> List[Dict]:
        """Get all active rules (synchronous)."""
        cluster = get_cluster()
        query = f"""
        SELECT r.*
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.RULES_COLLECTION}` r
        WHERE r.status = $status
        ORDER BY r.priority DESC
        LIMIT 100
        """

        result = cluster.query(
            query,
            QueryOptions(named_parameters={'status': RuleStatus.ACTIVE.value})
        )

        rules = [row['r'] for row in result.rows()]
        return [serialize_doc(r) for r in rules]

    @staticmethod
    async def update_rule_metrics(rule_id: str, triggered: bool, correct: bool):
        """Update rule effectiveness metrics."""
        cluster = get_cluster()

        # Use N1QL to increment counters
        query = f"""
        UPDATE `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.RULES_COLLECTION}`
        SET metrics.triggered_count = IFMISSING(metrics.triggered_count, 0) + $triggered_inc,
            metrics.true_positives = IFMISSING(metrics.true_positives, 0) + $tp_inc,
            metrics.false_positives = IFMISSING(metrics.false_positives, 0) + $fp_inc
        WHERE rule_id = $rule_id
        """

        cluster.query(
            query,
            QueryOptions(named_parameters={
                'rule_id': rule_id,
                'triggered_inc': 1 if triggered else 0,
                'tp_inc': 1 if triggered and correct else 0,
                'fp_inc': 1 if triggered and not correct else 0
            })
        )

    @staticmethod
    def update_rule_metrics_sync(rule_id: str, triggered: bool, correct: bool):
        """Update rule effectiveness metrics (synchronous)."""
        cluster = get_cluster()

        # Use N1QL to increment counters
        query = f"""
        UPDATE `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.RULES_COLLECTION}`
        SET metrics.triggered_count = IFMISSING(metrics.triggered_count, 0) + $triggered_inc,
            metrics.true_positives = IFMISSING(metrics.true_positives, 0) + $tp_inc,
            metrics.false_positives = IFMISSING(metrics.false_positives, 0) + $fp_inc
        WHERE rule_id = $rule_id
        """

        cluster.query(
            query,
            QueryOptions(named_parameters={
                'rule_id': rule_id,
                'triggered_inc': 1 if triggered else 0,
                'tp_inc': 1 if triggered and correct else 0,
                'fp_inc': 1 if triggered and not correct else 0
            })
        )


class HumanReviewRepository:
    """Repository for human review operations."""

    @staticmethod
    async def create_review(review: HumanReview) -> str:
        """Create human review record."""
        collection = get_collection('human_reviews')
        doc_key = f"review::{review.review_id}"
        doc_data = review.model_dump()
        collection.upsert(doc_key, doc_data)

        return review.review_id

    @staticmethod
    def create_review_sync_obj(review: HumanReview) -> str:
        """Create human review record (synchronous)."""
        collection = get_collection('human_reviews')
        doc_key = f"review::{review.review_id}"
        doc_data = review.model_dump()
        collection.upsert(doc_key, doc_data)

        return review.review_id

    @staticmethod
    def create_review_sync(review_data: Dict) -> str:
        """Create human review record (synchronous)."""
        collection = get_collection('human_reviews')
        review_id = review_data.get("review_id", str(uuid.uuid4()))
        review_data["review_id"] = review_id
        doc_key = f"review::{review_id}"
        collection.upsert(doc_key, review_data)

        return review_id

    @staticmethod
    async def get_pending_reviews(limit: int = 10) -> List[Dict]:
        """Get pending human reviews."""
        cluster = get_cluster()
        query = f"""
        SELECT r.*
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.HUMAN_REVIEWS_COLLECTION}` r
        WHERE r.status = "pending"
        ORDER BY r.priority DESC, r.created_at ASC
        LIMIT $limit
        """

        result = cluster.query(query, QueryOptions(named_parameters={'limit': limit}))
        reviews = [row['r'] for row in result.rows()]
        return [serialize_doc(r) for r in reviews]

    @staticmethod
    async def update_review(
        review_id: str,
        decision: str,
        notes: str,
        reviewer: str
    ):
        """Update human review with decision."""
        cluster = get_cluster()

        query = f"""
        UPDATE `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.HUMAN_REVIEWS_COLLECTION}`
        SET status = "completed",
            completed_at = $completed_at,
            human_decision = {{
                "decision": $decision,
                "reviewer": $reviewer,
                "timestamp": $timestamp
            }},
            notes = $notes
        WHERE review_id = $review_id
        """

        now = datetime.now(timezone.utc).isoformat()

        cluster.query(
            query,
            QueryOptions(named_parameters={
                'review_id': review_id,
                'decision': decision,
                'reviewer': reviewer,
                'notes': notes,
                'completed_at': now,
                'timestamp': now
            })
        )

    @staticmethod
    def complete_review_sync(
        review_id: str,
        decision: str,
        reviewer: str,
        notes: str = None
    ):
        """Complete human review with decision (synchronous)."""
        cluster = get_cluster()

        query = f"""
        UPDATE `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.HUMAN_REVIEWS_COLLECTION}`
        SET status = "completed",
            completed_at = $completed_at,
            human_decision = {{
                "decision": $decision,
                "reviewer": $reviewer,
                "timestamp": $timestamp
            }},
            notes = $notes,
            updated_at = $updated_at
        WHERE review_id = $review_id
        """

        now = datetime.now(timezone.utc).isoformat()

        cluster.query(
            query,
            QueryOptions(named_parameters={
                'review_id': review_id,
                'decision': decision,
                'reviewer': reviewer,
                'notes': notes,
                'completed_at': now,
                'timestamp': now,
                'updated_at': now
            })
        )


class NotificationRepository:
    """Repository for notification operations."""

    @staticmethod
    async def create_notification(notification: Notification) -> str:
        """Create notification record."""
        collection = get_collection('notifications')
        doc_key = f"notification::{notification.notification_id}"
        doc_data = notification.model_dump()
        collection.upsert(doc_key, doc_data)

        return notification.notification_id

    @staticmethod
    def create_notification_sync_obj(notification: Notification) -> str:
        """Create notification record (synchronous)."""
        collection = get_collection('notifications')
        doc_key = f"notification::{notification.notification_id}"
        doc_data = notification.model_dump()
        collection.upsert(doc_key, doc_data)

        return notification.notification_id

    @staticmethod
    def create_notification_sync(notification_data: Dict) -> str:
        """Create notification record (synchronous)."""
        collection = get_collection('notifications')
        notification_id = notification_data.get("notification_id", str(uuid.uuid4()))
        notification_data["notification_id"] = notification_id
        doc_key = f"notification::{notification_id}"
        collection.upsert(doc_key, notification_data)

        return notification_id

    @staticmethod
    async def get_pending_notifications() -> List[Dict]:
        """Get pending notifications."""
        cluster = get_cluster()
        query = f"""
        SELECT n.*
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.NOTIFICATIONS_COLLECTION}` n
        WHERE n.status = $status
        ORDER BY n.created_at ASC
        LIMIT 100
        """

        result = cluster.query(
            query,
            QueryOptions(named_parameters={'status': NotificationStatus.PENDING.value})
        )

        notifications = [row['n'] for row in result.rows()]
        return [serialize_doc(n) for n in notifications]

    @staticmethod
    async def mark_as_sent(notification_id: str):
        """Mark notification as sent."""
        cluster = get_cluster()

        query = f"""
        UPDATE `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.NOTIFICATIONS_COLLECTION}`
        SET status = $status,
            sent_at = $sent_at
        WHERE notification_id = $notification_id
        """

        cluster.query(
            query,
            QueryOptions(named_parameters={
                'notification_id': notification_id,
                'status': NotificationStatus.SENT.value,
                'sent_at': datetime.now(timezone.utc).isoformat()
            })
        )

    @staticmethod
    def mark_as_sent_sync(notification_id: str):
        """Mark notification as sent (synchronous)."""
        cluster = get_cluster()

        query = f"""
        UPDATE `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.NOTIFICATIONS_COLLECTION}`
        SET status = $status,
            sent_at = $sent_at
        WHERE notification_id = $notification_id
        """

        cluster.query(
            query,
            QueryOptions(named_parameters={
                'notification_id': notification_id,
                'status': NotificationStatus.SENT.value,
                'sent_at': datetime.now(timezone.utc).isoformat()
            })
        )


class AuditRepository:
    """Repository for audit operations."""

    @staticmethod
    async def create_audit_event(event: AuditEvent) -> str:
        """Create audit event."""
        collection = get_collection('audit_events')
        doc_key = f"audit::{event.event_id}"
        doc_data = event.model_dump()
        collection.upsert(doc_key, doc_data)

        return event.event_id

    @staticmethod
    def create_audit_event_sync(event: AuditEvent) -> str:
        """Create audit event (synchronous for Temporal)."""
        collection = get_collection('audit_events')
        doc_key = f"audit::{event.event_id}"
        doc_data = event.model_dump()
        collection.upsert(doc_key, doc_data)

        return event.event_id

    @staticmethod
    async def get_recent_events(
        transaction_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get recent audit events."""
        cluster = get_cluster()

        if transaction_id:
            query = f"""
            SELECT e.*
            FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.AUDIT_EVENTS_COLLECTION}` e
            WHERE e.transaction_id = $transaction_id
            ORDER BY e.timestamp DESC
            LIMIT $limit
            """

            result = cluster.query(
                query,
                QueryOptions(named_parameters={
                    'transaction_id': transaction_id,
                    'limit': limit
                })
            )
        else:
            query = f"""
            SELECT e.*
            FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.AUDIT_EVENTS_COLLECTION}` e
            ORDER BY e.timestamp DESC
            LIMIT $limit
            """

            result = cluster.query(query, QueryOptions(named_parameters={'limit': limit}))

        events = [row['e'] for row in result.rows()]
        return [serialize_doc(e) for e in events]


class MetricsRepository:
    """Repository for metrics operations."""

    @staticmethod
    async def record_metric(metric: SystemMetric):
        """Record a system metric."""
        collection = get_collection('system_metrics')
        doc_key = f"metric::{metric.metric_id}::{uuid.uuid4()}"
        doc_data = metric.model_dump()
        collection.upsert(doc_key, doc_data)

    @staticmethod
    def record_metric_sync(metric: SystemMetric):
        """Record a system metric (synchronous)."""
        collection = get_collection('system_metrics')
        doc_key = f"metric::{metric.metric_id}::{uuid.uuid4()}"
        doc_data = metric.model_dump()
        collection.upsert(doc_key, doc_data)

    @staticmethod
    async def get_recent_metrics(metric_name: str, minutes: int = 60) -> List[Dict]:
        """Get recent metrics."""
        cluster = get_cluster()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

        query = f"""
        SELECT m.*
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.SYSTEM_METRICS_COLLECTION}` m
        WHERE m.metric_name = $metric_name
        AND m.timestamp >= $cutoff
        ORDER BY m.timestamp DESC
        LIMIT 1000
        """

        result = cluster.query(
            query,
            QueryOptions(named_parameters={
                'metric_name': metric_name,
                'cutoff': cutoff
            })
        )

        metrics = [row['m'] for row in result.rows()]
        return [serialize_doc(m) for m in metrics]

    @staticmethod
    async def get_aggregated_metrics(hours: int = 24) -> Dict:
        """Get aggregated metrics for dashboard."""
        cluster = get_cluster()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        query = f"""
        SELECT m.metric_name,
               AVG(m.value) as avg_value,
               MIN(m.value) as min_value,
               MAX(m.value) as max_value,
               COUNT(*) as count
        FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.SYSTEM_METRICS_COLLECTION}` m
        WHERE m.timestamp >= $cutoff
        GROUP BY m.metric_name
        """

        result = cluster.query(query, QueryOptions(named_parameters={'cutoff': cutoff}))

        metrics_dict = {}
        for row in result.rows():
            metrics_dict[row['metric_name']] = {
                "avg": row['avg_value'],
                "min": row['min_value'],
                "max": row['max_value'],
                "count": row['count']
            }

        return metrics_dict
