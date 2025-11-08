"""Couchbase connection management."""

from datetime import timedelta
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, ClusterTimeoutOptions, QueryOptions
from couchbase.management.collections import CollectionSpec
from couchbase.management.queries import (
    CreatePrimaryQueryIndexOptions,
    CreateQueryIndexOptions,
)
from utils.config import config
import logging

logger = logging.getLogger(__name__)


class CouchbaseDB:
    """Couchbase database connection manager."""

    cluster: Cluster = None
    bucket = None
    scope = None

    def __init__(self):
        self.collections = {}


# Global database instance
db = CouchbaseDB()


async def connect_to_couchbase():
    """Create Couchbase connection."""
    try:
        # Configure authentication
        auth = PasswordAuthenticator(
            config.COUCHBASE_USERNAME,
            config.COUCHBASE_PASSWORD
        )

        # Configure timeouts
        timeout_options = ClusterTimeoutOptions(
            kv_timeout=timedelta(seconds=10),
            query_timeout=timedelta(seconds=75),
            search_timeout=timedelta(seconds=75)
        )

        # Connect to cluster
        db.cluster = Cluster(
            config.COUCHBASE_CONNECTION_STRING,
            ClusterOptions(auth, timeout_options=timeout_options)
        )

        # Wait until cluster is ready
        db.cluster.wait_until_ready(timedelta(seconds=10))

        # Get bucket and scope
        db.bucket = db.cluster.bucket(config.COUCHBASE_BUCKET)
        db.scope = db.bucket.scope(config.COUCHBASE_SCOPE)

        # Initialize collections
        _initialize_collections()

        # Create indexes
        await create_indexes()

        logger.info("Connected to Couchbase")
    except Exception as e:
        logger.error(f"Could not connect to Couchbase: {e}")
        raise


async def close_couchbase_connection():
    """Close Couchbase connection."""
    if db.cluster:
        db.cluster.close()
        logger.info("Disconnected from Couchbase")


def _initialize_collections():
    """Initialize collection references."""
    db.collections = {
        'customers': db.scope.collection(config.CUSTOMERS_COLLECTION),
        'transactions': db.scope.collection(config.TRANSACTIONS_COLLECTION),
        'decisions': db.scope.collection(config.DECISIONS_COLLECTION),
        'human_reviews': db.scope.collection(config.HUMAN_REVIEWS_COLLECTION),
        'audit_events': db.scope.collection(config.AUDIT_EVENTS_COLLECTION),
        'notifications': db.scope.collection(config.NOTIFICATIONS_COLLECTION),
        'system_metrics': db.scope.collection(config.SYSTEM_METRICS_COLLECTION),
        'rules': db.scope.collection(config.RULES_COLLECTION),
        'accounts': db.scope.collection(config.ACCOUNTS_COLLECTION),
        'journal': db.scope.collection(config.JOURNAL_COLLECTION),
        'balance_updates': db.scope.collection(config.BALANCE_UPDATES_COLLECTION),
        'holds': db.scope.collection(config.HOLDS_COLLECTION),
    }


async def create_indexes():
    """Create necessary indexes using N1QL."""
    try:
        query_index_manager = db.cluster.query_indexes()
        bucket_name = config.COUCHBASE_BUCKET
        scope_name = config.COUCHBASE_SCOPE

        # Helper function to build fully qualified collection name
        def fqn(collection_name):
            return f"`{bucket_name}`.`{scope_name}`.`{collection_name}`"

        # Transaction indexes
        indexes = [
            # Transactions collection
            (config.TRANSACTIONS_COLLECTION, "idx_transaction_id", ["transaction_id"]),
            (config.TRANSACTIONS_COLLECTION, "idx_status_created", ["status", "created_at"]),
            (config.TRANSACTIONS_COLLECTION, "idx_transaction_type", ["transaction_type"]),
            (config.TRANSACTIONS_COLLECTION, "idx_amount", ["amount"]),
            (config.TRANSACTIONS_COLLECTION, "idx_sender_customer", ["sender.customer_id"]),
            (config.TRANSACTIONS_COLLECTION, "idx_sender_account", ["sender.account_number"]),
            (config.TRANSACTIONS_COLLECTION, "idx_recipient_account", ["recipient.account_number"]),

            # Decisions collection
            (config.DECISIONS_COLLECTION, "idx_decision_transaction", ["transaction_id"]),
            (config.DECISIONS_COLLECTION, "idx_decision_created", ["decision", "created_at"]),
            (config.DECISIONS_COLLECTION, "idx_confidence_score", ["confidence_score"]),
            (config.DECISIONS_COLLECTION, "idx_risk_score", ["risk_score"]),

            # Accounts collection
            (config.ACCOUNTS_COLLECTION, "idx_account_number", ["account_number"]),
            (config.ACCOUNTS_COLLECTION, "idx_account_customer", ["customer_id"]),
            (config.ACCOUNTS_COLLECTION, "idx_account_status", ["status"]),

            # Journal collection
            (config.JOURNAL_COLLECTION, "idx_journal_transaction", ["transaction_id"]),
            (config.JOURNAL_COLLECTION, "idx_journal_status", ["status"]),
            (config.JOURNAL_COLLECTION, "idx_journal_debit", ["debit_account", "timestamp"]),
            (config.JOURNAL_COLLECTION, "idx_journal_credit", ["credit_account", "timestamp"]),

            # Balance updates collection
            (config.BALANCE_UPDATES_COLLECTION, "idx_balance_account", ["account_number", "timestamp"]),
            (config.BALANCE_UPDATES_COLLECTION, "idx_balance_transaction", ["transaction_id"]),

            # Holds collection
            (config.HOLDS_COLLECTION, "idx_hold_account", ["account_number", "status"]),
            (config.HOLDS_COLLECTION, "idx_hold_transaction", ["transaction_id"]),
            (config.HOLDS_COLLECTION, "idx_hold_expires", ["expires_at"]),

            # Rules collection
            (config.RULES_COLLECTION, "idx_rule_status_priority", ["status", "priority"]),
            (config.RULES_COLLECTION, "idx_rule_category", ["category"]),

            # Human reviews collection
            (config.HUMAN_REVIEWS_COLLECTION, "idx_review_transaction", ["transaction_id"]),
            (config.HUMAN_REVIEWS_COLLECTION, "idx_review_status_priority", ["status", "priority"]),
            (config.HUMAN_REVIEWS_COLLECTION, "idx_review_assigned", ["assigned_to"]),
            (config.HUMAN_REVIEWS_COLLECTION, "idx_review_sla", ["sla_deadline"]),

            # Notifications collection
            (config.NOTIFICATIONS_COLLECTION, "idx_notification_status", ["status", "created_at"]),
            (config.NOTIFICATIONS_COLLECTION, "idx_notification_transaction", ["transaction_id"]),

            # Audit events collection
            (config.AUDIT_EVENTS_COLLECTION, "idx_audit_timestamp", ["timestamp"]),
            (config.AUDIT_EVENTS_COLLECTION, "idx_audit_transaction", ["transaction_id"]),
            (config.AUDIT_EVENTS_COLLECTION, "idx_audit_type", ["event_type"]),
            (config.AUDIT_EVENTS_COLLECTION, "idx_audit_customer", ["customer_id"]),

            # System metrics collection
            (config.SYSTEM_METRICS_COLLECTION, "idx_metrics_timestamp", ["timestamp"]),
            (config.SYSTEM_METRICS_COLLECTION, "idx_metrics_name", ["metric_name", "timestamp"]),
        ]

        for collection_name, index_name, fields in indexes:
            try:
                # Create index using N1QL
                fields_str = ", ".join([f"`{field}`" for field in fields])
                query = f"CREATE INDEX {index_name} ON {fqn(collection_name)}({fields_str})"

                db.cluster.query(query)
                logger.info(f"Created index {index_name} on {collection_name}")
            except Exception as e:
                # Index might already exist
                if "already exists" not in str(e).lower():
                    logger.warning(f"Could not create index {index_name}: {e}")

        logger.info("Indexes created successfully")

    except Exception as e:
        logger.error(f"Error creating indexes: {e}")


def get_cluster():
    """Get Couchbase cluster instance."""
    return db.cluster


def get_bucket():
    """Get Couchbase bucket instance."""
    return db.bucket


def get_scope():
    """Get Couchbase scope instance."""
    return db.scope


def get_collection(collection_name: str):
    """Get a specific collection instance."""
    return db.collections.get(collection_name)


def get_sync_cluster():
    """Get synchronous Couchbase cluster for Temporal activities."""
    # Couchbase Python SDK 4.x is primarily synchronous
    # with async operations available via acouchbase
    return db.cluster


def get_sync_scope():
    """Get synchronous scope for Temporal activities."""
    return db.scope
