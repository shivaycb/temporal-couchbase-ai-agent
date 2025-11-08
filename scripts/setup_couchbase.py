"""Complete Couchbase setup script with comprehensive test data for all scenarios."""

import logging
from datetime import datetime, timedelta, timezone
import random
import uuid
import json
from typing import List, Dict, Any
from decimal import Decimal

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions, ClusterTimeoutOptions
from couchbase.management.buckets import (
    CreateBucketSettings,
    BucketType,
    StorageBackend,
    ConflictResolutionType
)
from couchbase.management.collections import CollectionSpec
from couchbase.exceptions import (
    BucketAlreadyExistsException,
    ScopeAlreadyExistsException,
    CollectionAlreadyExistsException,
    QueryIndexAlreadyExistsException
)

from utils.config import config
from utils.decimal_utils import to_decimal
from database.schemas import (
    Customer, Rule, RuleStatus, Transaction, TransactionDecision,
    HumanReview, Notification, AuditEvent, SystemMetric,
    TransactionType, TransactionStatus, DecisionType, RiskLevel
)
from services.rule_engine import RuleEngine
from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def setup_couchbase():
    """Create Couchbase bucket, scope, collections and indexes."""
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
        cluster = Cluster(
            config.COUCHBASE_CONNECTION_STRING,
            ClusterOptions(auth, timeout_options=timeout_options)
        )

        # Wait until cluster is ready
        cluster.wait_until_ready(timedelta(seconds=10))
        logger.info("Connected to Couchbase cluster")

        # Create bucket
        create_bucket(cluster)

        # Get bucket reference
        bucket = cluster.bucket(config.COUCHBASE_BUCKET)

        # Create scope
        create_scope(bucket)

        # Create collections
        create_collections(bucket)

        # Wait for collections to be ready
        import time
        time.sleep(2)

        # Create N1QL indexes
        create_indexes(cluster)

        # Create FTS vector search index
        create_vector_search_index(cluster)

        # Insert sample data
        insert_sample_data(cluster)

        logger.info("Couchbase setup completed successfully!")

    except Exception as e:
        logger.error(f"Error setting up Couchbase: {e}")
        raise


def create_bucket(cluster: Cluster):
    """Create Couchbase bucket if it doesn't exist."""
    try:
        bucket_manager = cluster.buckets()

        # Check if bucket exists
        try:
            bucket_manager.get_bucket(config.COUCHBASE_BUCKET)
            logger.info(f"Bucket '{config.COUCHBASE_BUCKET}' already exists")
            return
        except Exception:
            pass

        # Create bucket
        bucket_settings = CreateBucketSettings(
            name=config.COUCHBASE_BUCKET,
            bucket_type=BucketType.COUCHBASE,
            ram_quota_mb=256,  # 256 MB for development
            num_replicas=0,  # No replicas for development
            flush_enabled=True,
            storage_backend=StorageBackend.COUCHSTORE,
            conflict_resolution_type=ConflictResolutionType.SEQUENCE_NUMBER
        )

        bucket_manager.create_bucket(bucket_settings)
        logger.info(f"Created bucket: {config.COUCHBASE_BUCKET}")

        # Wait for bucket to be ready
        import time
        time.sleep(3)

    except BucketAlreadyExistsException:
        logger.info(f"Bucket '{config.COUCHBASE_BUCKET}' already exists")
    except Exception as e:
        logger.error(f"Error creating bucket: {e}")
        raise


def create_scope(bucket):
    """Create scope if it doesn't exist."""
    try:
        collection_manager = bucket.collections()

        # Skip if using default scope
        if config.COUCHBASE_SCOPE == "_default":
            logger.info("Using default scope")
            return

        # Check if scope exists
        try:
            scopes = collection_manager.get_all_scopes()
            if any(scope.name == config.COUCHBASE_SCOPE for scope in scopes):
                logger.info(f"Scope '{config.COUCHBASE_SCOPE}' already exists")
                return
        except Exception:
            pass

        # Create scope
        collection_manager.create_scope(config.COUCHBASE_SCOPE)
        logger.info(f"Created scope: {config.COUCHBASE_SCOPE}")

    except ScopeAlreadyExistsException:
        logger.info(f"Scope '{config.COUCHBASE_SCOPE}' already exists")
    except Exception as e:
        logger.error(f"Error creating scope: {e}")
        raise


def create_collections(bucket):
    """Create all necessary collections."""
    collections_to_create = [
        config.CUSTOMERS_COLLECTION,
        config.TRANSACTIONS_COLLECTION,
        config.DECISIONS_COLLECTION,
        config.HUMAN_REVIEWS_COLLECTION,
        config.AUDIT_EVENTS_COLLECTION,
        config.NOTIFICATIONS_COLLECTION,
        config.SYSTEM_METRICS_COLLECTION,
        config.RULES_COLLECTION,
        config.ACCOUNTS_COLLECTION,
        config.JOURNAL_COLLECTION,
        config.BALANCE_UPDATES_COLLECTION,
        config.HOLDS_COLLECTION
    ]

    try:
        collection_manager = bucket.collections()

        # Get existing collections
        scopes = collection_manager.get_all_scopes()
        existing_collections = []
        for scope in scopes:
            if scope.name == config.COUCHBASE_SCOPE:
                existing_collections = [col.name for col in scope.collections]
                break

        # Create collections that don't exist
        for collection_name in collections_to_create:
            if collection_name in existing_collections:
                logger.info(f"Collection already exists: {collection_name}")
            else:
                try:
                    collection_spec = CollectionSpec(
                        collection_name,
                        scope_name=config.COUCHBASE_SCOPE
                    )
                    collection_manager.create_collection(collection_spec)
                    logger.info(f"Created collection: {collection_name}")
                except CollectionAlreadyExistsException:
                    logger.info(f"Collection already exists: {collection_name}")
                except Exception as e:
                    logger.error(f"Error creating collection {collection_name}: {e}")

    except Exception as e:
        logger.error(f"Error creating collections: {e}")
        raise


def create_indexes(cluster: Cluster):
    """Create all necessary N1QL indexes."""
    logger.info("Creating N1QL indexes...")

    bucket_name = config.COUCHBASE_BUCKET
    scope_name = config.COUCHBASE_SCOPE

    # Helper function to build fully qualified collection name
    def fqn(collection_name):
        return f"`{bucket_name}`.`{scope_name}`.`{collection_name}`"

    # Define all indexes
    indexes = [
        # Customer indexes
        (config.CUSTOMERS_COLLECTION, "idx_customer_id", ["customer_id"]),
        (config.CUSTOMERS_COLLECTION, "idx_customer_legal_name", ["legal_name"]),
        (config.CUSTOMERS_COLLECTION, "idx_customer_status", ["status"]),

        # Transaction indexes
        (config.TRANSACTIONS_COLLECTION, "idx_transaction_id", ["transaction_id"]),
        (config.TRANSACTIONS_COLLECTION, "idx_status_created", ["status", "created_at"]),
        (config.TRANSACTIONS_COLLECTION, "idx_transaction_type", ["transaction_type"]),
        (config.TRANSACTIONS_COLLECTION, "idx_amount", ["amount"]),
        (config.TRANSACTIONS_COLLECTION, "idx_sender_customer", ["sender.customer_id"]),
        (config.TRANSACTIONS_COLLECTION, "idx_created_at", ["created_at"]),
        (config.TRANSACTIONS_COLLECTION, "idx_sender_account", ["sender.account_number"]),
        (config.TRANSACTIONS_COLLECTION, "idx_recipient_account", ["recipient.account_number"]),

        # Composite indexes for hybrid search and graph traversal
        (config.TRANSACTIONS_COLLECTION, "idx_hybrid_search", [
            "transaction_type", "amount", "sender.country", "recipient.country"
        ]),
        (config.TRANSACTIONS_COLLECTION, "idx_graph_sender_time", [
            "sender.account_number", "created_at"
        ]),
        (config.TRANSACTIONS_COLLECTION, "idx_graph_recipient_time", [
            "recipient.account_number", "created_at"
        ]),

        # Decision indexes
        (config.DECISIONS_COLLECTION, "idx_decision_transaction", ["transaction_id"]),
        (config.DECISIONS_COLLECTION, "idx_decision_created", ["decision", "created_at"]),
        (config.DECISIONS_COLLECTION, "idx_confidence_score", ["confidence_score"]),
        (config.DECISIONS_COLLECTION, "idx_risk_score", ["risk_score"]),

        # Rule indexes
        (config.RULES_COLLECTION, "idx_rule_id", ["rule_id"]),
        (config.RULES_COLLECTION, "idx_rule_status_priority", ["status", "priority"]),
        (config.RULES_COLLECTION, "idx_rule_category", ["category"]),

        # Human review indexes
        (config.HUMAN_REVIEWS_COLLECTION, "idx_review_transaction", ["transaction_id"]),
        (config.HUMAN_REVIEWS_COLLECTION, "idx_review_status_priority", ["status", "priority"]),
        (config.HUMAN_REVIEWS_COLLECTION, "idx_review_assigned", ["assigned_to"]),
        (config.HUMAN_REVIEWS_COLLECTION, "idx_review_sla", ["sla_deadline"]),

        # Notification indexes
        (config.NOTIFICATIONS_COLLECTION, "idx_notification_id", ["notification_id"]),
        (config.NOTIFICATIONS_COLLECTION, "idx_notification_status", ["status", "created_at"]),
        (config.NOTIFICATIONS_COLLECTION, "idx_notification_transaction", ["transaction_id"]),

        # Audit indexes
        (config.AUDIT_EVENTS_COLLECTION, "idx_audit_timestamp", ["timestamp"]),
        (config.AUDIT_EVENTS_COLLECTION, "idx_audit_transaction", ["transaction_id"]),
        (config.AUDIT_EVENTS_COLLECTION, "idx_audit_type", ["event_type"]),

        # Metrics indexes
        (config.SYSTEM_METRICS_COLLECTION, "idx_metrics_timestamp", ["timestamp"]),
        (config.SYSTEM_METRICS_COLLECTION, "idx_metrics_name", ["metric_name", "timestamp"]),

        # Account indexes
        (config.ACCOUNTS_COLLECTION, "idx_account_number", ["account_number"]),
        (config.ACCOUNTS_COLLECTION, "idx_account_customer", ["customer_id"]),
        (config.ACCOUNTS_COLLECTION, "idx_account_status", ["status"]),

        # Journal indexes for ACID transactions
        (config.JOURNAL_COLLECTION, "idx_journal_id", ["journal_id"]),
        (config.JOURNAL_COLLECTION, "idx_journal_transaction", ["transaction_id"]),
        (config.JOURNAL_COLLECTION, "idx_journal_account_time", ["account_number", "timestamp"]),
        (config.JOURNAL_COLLECTION, "idx_journal_status", ["status"]),

        # Balance update indexes
        (config.BALANCE_UPDATES_COLLECTION, "idx_balance_update_id", ["update_id"]),
        (config.BALANCE_UPDATES_COLLECTION, "idx_balance_account_time", ["account_number", "timestamp"]),
        (config.BALANCE_UPDATES_COLLECTION, "idx_balance_transaction", ["transaction_id"]),

        # Hold indexes
        (config.HOLDS_COLLECTION, "idx_hold_id", ["hold_id"]),
        (config.HOLDS_COLLECTION, "idx_hold_account_status", ["account_number", "status"]),
        (config.HOLDS_COLLECTION, "idx_hold_transaction", ["transaction_id"]),
        (config.HOLDS_COLLECTION, "idx_hold_expires", ["expires_at"]),
    ]

    # Create indexes
    for collection_name, index_name, fields in indexes:
        try:
            # Build index fields string
            fields_str = ", ".join([f"`{field}`" for field in fields])

            # Create index query
            query = f"CREATE INDEX `{index_name}` ON {fqn(collection_name)}({fields_str})"

            cluster.query(query).execute()
            logger.info(f"Created index {index_name} on {collection_name}")

        except Exception as e:
            # Index might already exist
            error_msg = str(e).lower()
            if "already exists" in error_msg or "duplicate" in error_msg or "index already exists" in error_msg:
                logger.info(f"Index {index_name} already exists")
            else:
                logger.warning(f"Could not create index {index_name}: {e}")

    logger.info("All N1QL indexes created successfully")


def create_vector_search_index(cluster: Cluster):
    """Create Full-Text Search (FTS) index for vector search."""
    try:
        logger.info("Creating FTS vector search index...")

        # Note: FTS indexes with vector support must be created via Couchbase Web UI or REST API
        # The Python SDK doesn't directly support creating vector search indexes yet
        # This is a placeholder for documentation purposes

        index_definition = {
            "name": config.VECTOR_SEARCH_INDEX,
            "type": "fulltext-index",
            "sourceName": config.COUCHBASE_BUCKET,
            "sourceType": "couchbase",
            "planParams": {
                "maxPartitionsPerPIndex": 1024,
                "indexPartitions": 1
            },
            "params": {
                "doc_config": {
                    "mode": "scope.collection.type_field",
                    "type_field": "type"
                },
                "mapping": {
                    "default_analyzer": "standard",
                    "default_datetime_parser": "dateTimeOptional",
                    "default_field": "_all",
                    "default_mapping": {
                        "dynamic": False,
                        "enabled": False
                    },
                    "default_type": "_default",
                    "index_dynamic": False,
                    "store_dynamic": False,
                    "type_field": "_type",
                    "types": {
                        f"{config.COUCHBASE_SCOPE}.{config.TRANSACTIONS_COLLECTION}": {
                            "dynamic": False,
                            "enabled": True,
                            "properties": {
                                "embedding": {
                                    "enabled": True,
                                    "dynamic": False,
                                    "fields": [
                                        {
                                            "name": "embedding",
                                            "type": "vector",
                                            "dims": config.VECTOR_DIMENSION,
                                            "similarity": "dot_product",
                                            "index": True
                                        }
                                    ]
                                },
                                "transaction_type": {
                                    "enabled": True,
                                    "dynamic": False,
                                    "fields": [
                                        {
                                            "name": "transaction_type",
                                            "type": "text",
                                            "index": True
                                        }
                                    ]
                                },
                                "status": {
                                    "enabled": True,
                                    "dynamic": False,
                                    "fields": [
                                        {
                                            "name": "status",
                                            "type": "text",
                                            "index": True
                                        }
                                    ]
                                }
                            }
                        }
                    }
                },
                "store": {
                    "indexType": "scorch",
                    "segmentVersion": 16
                }
            }
        }

        logger.info("FTS Vector Search Index Configuration:")
        logger.info(json.dumps(index_definition, indent=2))
        logger.info(f"""

        MANUAL SETUP REQUIRED:
        =====================
        The FTS vector search index must be created manually via Couchbase Web UI or REST API.

        1. Open Couchbase Web UI: {config.COUCHBASE_CONNECTION_STRING.replace('couchbase://', 'http://')}:8091
        2. Navigate to Search > Add Index
        3. Use the configuration above or follow these steps:
           - Index Name: {config.VECTOR_SEARCH_INDEX}
           - Bucket: {config.COUCHBASE_BUCKET}
           - Scope: {config.COUCHBASE_SCOPE}
           - Collection: {config.TRANSACTIONS_COLLECTION}
           - Add a vector field mapping for 'embedding' with:
             * Dimensions: {config.VECTOR_DIMENSION}
             * Similarity: dot_product (or cosine)
           - Add filter fields for 'transaction_type' and 'status'
        4. Click 'Create Index'

        Alternatively, use the REST API to create the index programmatically.
        """)

    except Exception as e:
        logger.warning(f"Vector search index setup information displayed. Manual creation required: {e}")


def insert_sample_data(cluster: Cluster):
    """Insert comprehensive sample data for all scenarios."""
    try:
        bucket = cluster.bucket(config.COUCHBASE_BUCKET)
        scope = bucket.scope(config.COUCHBASE_SCOPE)

        # Check if we already have sample data
        customers_collection = scope.collection(config.CUSTOMERS_COLLECTION)

        # Try to check if data exists (using N1QL query)
        query = f"SELECT COUNT(*) as count FROM `{config.COUCHBASE_BUCKET}`.`{config.COUCHBASE_SCOPE}`.`{config.CUSTOMERS_COLLECTION}`"
        result = cluster.query(query).execute()
        count = list(result)[0]['count']

        if count > 0:
            logger.info("Sample data already exists, skipping...")
            return

        logger.info("Creating comprehensive test data for all scenarios...")

        # Create comprehensive customer profiles
        customers = create_test_customers()

        # Insert customers
        customers_coll = scope.collection(config.CUSTOMERS_COLLECTION)
        for customer in customers:
            customer_dict = customer.model_dump()
            # Convert datetime objects to ISO strings
            customer_dict = convert_datetimes_to_iso(customer_dict)
            # Convert Decimal to float for JSON serialization
            customer_dict = convert_decimals_to_float(customer_dict)
            customers_coll.insert(customer.customer_id, customer_dict)
        logger.info(f"Inserted {len(customers)} customers")

        # Create accounts for customers
        accounts = create_test_accounts(customers)
        accounts_coll = scope.collection(config.ACCOUNTS_COLLECTION)
        for account in accounts:
            account = convert_datetimes_to_iso(account)
            account = convert_decimals_to_float(account)
            accounts_coll.insert(account['account_number'], account)
        logger.info(f"Inserted {len(accounts)} accounts")

        # Insert default rules
        default_rules = RuleEngine.get_default_rules()
        rules_coll = scope.collection(config.RULES_COLLECTION)
        for rule in default_rules:
            rule_dict = rule.model_dump()
            rule_dict = convert_datetimes_to_iso(rule_dict)
            rule_dict = convert_decimals_to_float(rule_dict)
            rules_coll.insert(rule.rule_id, rule_dict)
        logger.info(f"Inserted {len(default_rules)} default rules")

        # Create comprehensive test transactions
        test_transactions = create_comprehensive_test_transactions(customers)
        test_decisions = create_test_decisions(test_transactions)
        test_reviews = create_test_human_reviews(test_transactions)
        test_notifications = create_test_notifications(test_transactions)
        test_audit_events = create_test_audit_events(test_transactions)
        test_metrics = create_test_system_metrics()

        # Insert transactions
        if test_transactions:
            txn_coll = scope.collection(config.TRANSACTIONS_COLLECTION)
            for txn in test_transactions:
                txn = convert_datetimes_to_iso(txn)
                txn = convert_decimals_to_float(txn)
                txn_coll.insert(txn['transaction_id'], txn)
            logger.info(f"Inserted {len(test_transactions)} test transactions")

        # Insert decisions
        if test_decisions:
            dec_coll = scope.collection(config.DECISIONS_COLLECTION)
            for decision in test_decisions:
                decision = convert_datetimes_to_iso(decision)
                decision = convert_decimals_to_float(decision)
                dec_coll.insert(decision['decision_id'], decision)
            logger.info(f"Inserted {len(test_decisions)} test decisions")

        # Insert human reviews
        if test_reviews:
            review_coll = scope.collection(config.HUMAN_REVIEWS_COLLECTION)
            for review in test_reviews:
                review = convert_datetimes_to_iso(review)
                review = convert_decimals_to_float(review)
                review_coll.insert(review['review_id'], review)
            logger.info(f"Inserted {len(test_reviews)} human reviews")

        # Insert notifications
        if test_notifications:
            notif_coll = scope.collection(config.NOTIFICATIONS_COLLECTION)
            for notification in test_notifications:
                notification = convert_datetimes_to_iso(notification)
                notification = convert_decimals_to_float(notification)
                notif_coll.insert(notification['notification_id'], notification)
            logger.info(f"Inserted {len(test_notifications)} notifications")

        # Insert audit events
        if test_audit_events:
            audit_coll = scope.collection(config.AUDIT_EVENTS_COLLECTION)
            for event in test_audit_events:
                event = convert_datetimes_to_iso(event)
                event = convert_decimals_to_float(event)
                audit_coll.insert(event['event_id'], event)
            logger.info(f"Inserted {len(test_audit_events)} audit events")

        # Insert system metrics
        if test_metrics:
            metrics_coll = scope.collection(config.SYSTEM_METRICS_COLLECTION)
            for metric in test_metrics:
                metric = convert_datetimes_to_iso(metric)
                metric = convert_decimals_to_float(metric)
                metrics_coll.insert(metric['metric_id'], metric)
            logger.info(f"Inserted {len(test_metrics)} system metrics")

        logger.info("Comprehensive test data creation completed")

    except Exception as e:
        logger.error(f"Error inserting sample data: {e}")
        raise


def convert_datetimes_to_iso(data):
    """Recursively convert datetime objects to ISO format strings."""
    if isinstance(data, dict):
        return {k: convert_datetimes_to_iso(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [convert_datetimes_to_iso(item) for item in data]
    elif isinstance(data, datetime):
        return data.isoformat()
    else:
        return data


def convert_decimals_to_float(data):
    """Recursively convert Decimal objects to float for JSON serialization."""
    if isinstance(data, dict):
        return {k: convert_decimals_to_float(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [convert_decimals_to_float(item) for item in data]
    elif isinstance(data, Decimal):
        return float(data)
    else:
        return data


def create_test_customers() -> List[Customer]:
    """Create diverse customer profiles for testing."""
    return [
        # Low-risk business customers
        Customer(
            customer_id="CUST_LOWRISK_001",
            legal_name="TechStartup Inc",
            display_name="TechStartup",
            customer_type="business",
            country="US",
            risk_profile={
                "risk_level": "low",
                "kyc_status": "approved",
                "last_review_date": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
                "compliance_rating": "excellent",
                "sanctions_check": "clear"
            },
            behavior_profile={
                "avg_transaction_amount": 5000,
                "transaction_frequency": "weekly",
                "common_recipients": ["Cloud Services Provider", "Software Vendor"],
                "established_relationships": True,
                "transaction_patterns": "regular"
            },
            accounts=[
                {"account_number": "ACC_TS_001", "account_type": "checking", "balance": 500000}
            ]
        ),
        Customer(
            customer_id="CUST_MEDIUMRISK_001",
            legal_name="Manufacturing Corp",
            display_name="ManufacturingCorp",
            customer_type="business",
            country="US",
            risk_profile={
                "risk_level": "medium",
                "kyc_status": "approved",
                "last_review_date": (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
                "compliance_rating": "good",
                "sanctions_check": "clear"
            },
            behavior_profile={
                "avg_transaction_amount": 75000,
                "transaction_frequency": "monthly",
                "common_recipients": ["Equipment Supplier GmbH", "Raw Materials Ltd"],
                "established_relationships": True,
                "transaction_patterns": "seasonal"
            },
            accounts=[
                {"account_number": "ACC_MC_001", "account_type": "business", "balance": 2000000}
            ]
        ),
        Customer(
            customer_id="CUST_HIGHRISK_001",
            legal_name="High Risk Trader LLC",
            display_name="HR Trader",
            customer_type="business",
            country="US",
            risk_profile={
                "risk_level": "high",
                "kyc_status": "approved",
                "last_review_date": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
                "compliance_rating": "requires_monitoring",
                "sanctions_check": "pending"
            },
            behavior_profile={
                "avg_transaction_amount": 150000,
                "transaction_frequency": "daily",
                "common_recipients": [],
                "established_relationships": False,
                "transaction_patterns": "irregular"
            },
            accounts=[
                {"account_number": "ACC_HR_001", "account_type": "business", "balance": 1000000}
            ]
        ),
        # Individual customers for different scenarios
        Customer(
            customer_id="CUST_INDIVIDUAL_001",
            legal_name="John Smith",
            display_name="John Smith",
            customer_type="individual",
            country="US",
            risk_profile={
                "risk_level": "low",
                "kyc_status": "approved",
                "last_review_date": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),
                "compliance_rating": "excellent",
                "sanctions_check": "clear"
            },
            behavior_profile={
                "avg_transaction_amount": 2500,
                "transaction_frequency": "monthly",
                "common_recipients": ["Jane Doe", "Family Trust"],
                "established_relationships": True,
                "transaction_patterns": "regular"
            },
            accounts=[
                {"account_number": "ACC_JS_001", "account_type": "checking", "balance": 50000}
            ]
        ),
        Customer(
            customer_id="CUST_SUSPICIOUS_001",
            legal_name="Suspicious Entity Inc",
            display_name="Suspicious Entity",
            customer_type="business",
            country="US",
            risk_profile={
                "risk_level": "very_high",
                "kyc_status": "approved",
                "last_review_date": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                "compliance_rating": "high_risk",
                "sanctions_check": "flagged"
            },
            behavior_profile={
                "avg_transaction_amount": 99999,
                "transaction_frequency": "irregular",
                "common_recipients": [],
                "established_relationships": False,
                "transaction_patterns": "suspicious"
            },
            accounts=[
                {"account_number": "ACC_SUSPECT_001", "account_type": "business", "balance": 200000}
            ]
        ),
        # International customers
        Customer(
            customer_id="CUST_INTERNATIONAL_001",
            legal_name="Global Supplies Ltd",
            display_name="Global Supplies",
            customer_type="business",
            country="GB",
            risk_profile={
                "risk_level": "low",
                "kyc_status": "approved",
                "last_review_date": (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(),
                "compliance_rating": "good",
                "sanctions_check": "clear"
            },
            behavior_profile={
                "avg_transaction_amount": 25000,
                "transaction_frequency": "weekly",
                "common_recipients": ["US Importers", "European Suppliers"],
                "established_relationships": True,
                "transaction_patterns": "regular"
            },
            accounts=[
                {"account_number": "ACC_GS_001", "account_type": "business", "balance": 750000}
            ]
        )
    ]


def create_test_accounts(customers: List[Customer]) -> List[Dict[str, Any]]:
    """Create account records for customers."""
    accounts = []
    for customer in customers:
        for account_info in customer.accounts:
            account = {
                "account_number": account_info["account_number"],
                "customer_id": customer.customer_id,
                "customer_name": customer.legal_name,
                "account_type": account_info["account_type"],
                "balance": to_decimal(account_info["balance"]),
                "available_balance": to_decimal(account_info["balance"]),
                "currency": "USD",
                "status": "active",
                "daily_withdrawal_limit": to_decimal(10000.0),
                "daily_transfer_limit": to_decimal(50000.0),
                "overdraft_limit": to_decimal(0.0),
                "total_deposits": to_decimal(0.0),
                "total_withdrawals": to_decimal(0.0),
                "transaction_count": 0,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "kyc_verified": True,
                "risk_score": 0.0,
                "holds": []
            }
            accounts.append(account)
    return accounts


def create_comprehensive_test_transactions(customers: List[Customer]) -> List[Dict[str, Any]]:
    """Create comprehensive test transactions covering all scenarios."""
    transactions = []
    base_time = datetime.now(timezone.utc) - timedelta(days=30)

    # Scenario 1: Normal ACH payments (should approve)
    for i in range(10):
        txn = {
            "transaction_id": f"TXN_NORMAL_ACH_{i+1:03d}",
            "transaction_type": "ach",
            "amount": to_decimal(random.uniform(1000, 10000)),
            "currency": "USD",
            "sender": {
                "name": "TechStartup Inc",
                "account_number": "ACC_TS_001",
                "customer_id": "CUST_LOWRISK_001",
                "country": "US"
            },
            "recipient": {
                "name": random.choice(["Cloud Services Provider", "Software Vendor", "Office Supplies Inc"]),
                "account_number": f"ACC_VENDOR_{i+1:03d}",
                "country": "US"
            },
            "status": "approved",
            "created_at": base_time + timedelta(days=i, hours=random.randint(9, 17)),
            "reference_number": f"INV-2024-{i+1:03d}",
            "description": "Regular business payment",
            "embedding": [random.random() for _ in range(config.VECTOR_DIMENSION)],
            "ml_features": {
                "time_of_day": "business_hours",
                "recurring": True,
                "established_recipient": True
            },
            "risk_flags": [],
            "processing_stages": [],
            "rules_applied": [],
            "regulatory": {}
        }
        transactions.append(txn)

    # Scenario 2: Large wire transfers requiring manager approval
    for i in range(5):
        txn = {
            "transaction_id": f"TXN_LARGE_WIRE_{i+1:03d}",
            "transaction_type": "wire_transfer",
            "amount": to_decimal(random.uniform(60000, 100000)),
            "currency": "USD",
            "sender": {
                "name": "Manufacturing Corp",
                "account_number": "ACC_MC_001",
                "customer_id": "CUST_MEDIUMRISK_001",
                "country": "US"
            },
            "recipient": {
                "name": "Equipment Supplier GmbH",
                "account_number": f"ACC_ES_{i+1:03d}",
                "country": "DE"
            },
            "status": "pending_manager_approval",
            "created_at": base_time + timedelta(days=i*2, hours=random.randint(9, 17)),
            "reference_number": f"PO-2024-{i+1:03d}",
            "description": "Equipment purchase",
            "embedding": [random.random() for _ in range(config.VECTOR_DIMENSION)],
            "ml_features": {
                "time_of_day": "business_hours",
                "high_value": True,
                "cross_border": True
            },
            "risk_flags": ["high_amount", "cross_border"],
            "processing_stages": [],
            "rules_applied": [],
            "regulatory": {}
        }
        transactions.append(txn)

    # Scenario 3: Suspicious transactions (should reject/escalate)
    suspicious_scenarios = [
        {
            "type": "suspicious_round_amount",
            "amount": to_decimal(99999.00),
            "recipient_country": "KY",
            "flags": ["suspicious_amount", "offshore_jurisdiction"]
        },
        {
            "type": "high_risk_country",
            "amount": to_decimal(50000.00),
            "recipient_country": "AF",
            "flags": ["high_risk_country", "unusual_destination"]
        },
        {
            "type": "after_hours_large",
            "amount": to_decimal(75000.00),
            "recipient_country": "US",
            "flags": ["unusual_time", "high_amount"]
        }
    ]

    for i, scenario in enumerate(suspicious_scenarios):
        txn = {
            "transaction_id": f"TXN_SUSPICIOUS_{i+1:03d}",
            "transaction_type": "wire_transfer",
            "amount": scenario["amount"],
            "currency": "USD",
            "sender": {
                "name": "Suspicious Entity Inc",
                "account_number": "ACC_SUSPECT_001",
                "customer_id": "CUST_SUSPICIOUS_001",
                "country": "US"
            },
            "recipient": {
                "name": "Offshore Holdings Ltd",
                "account_number": f"ACC_OFF_{i+1:03d}",
                "country": scenario["recipient_country"]
            },
            "status": "rejected" if "suspicious_amount" in scenario["flags"] else "escalated",
            "created_at": base_time + timedelta(days=i*3, hours=22 if "unusual_time" in scenario["flags"] else 14),
            "reference_number": f"URGENT-{i+1:03d}",
            "description": "Investment opportunity",
            "embedding": [random.random() for _ in range(config.VECTOR_DIMENSION)],
            "ml_features": {
                "time_of_day": "after_hours" if "unusual_time" in scenario["flags"] else "business_hours",
                "first_time_recipient": True,
                "unusual_pattern": True
            },
            "risk_flags": scenario["flags"],
            "processing_stages": [],
            "rules_applied": [],
            "regulatory": {}
        }
        transactions.append(txn)

    # Scenario 4: International transfers with enhanced due diligence
    for i in range(3):
        txn = {
            "transaction_id": f"TXN_INTERNATIONAL_{i+1:03d}",
            "transaction_type": "international",
            "amount": to_decimal(random.uniform(100000, 300000)),
            "currency": "USD",
            "sender": {
                "name": "Global Supplies Ltd",
                "account_number": "ACC_GS_001",
                "customer_id": "CUST_INTERNATIONAL_001",
                "country": "GB"
            },
            "recipient": {
                "name": random.choice(["Dubai Trading Company", "Singapore Imports Ltd", "Tokyo Electronics"]),
                "account_number": f"ACC_INTL_{i+1:03d}",
                "country": random.choice(["AE", "SG", "JP"])
            },
            "status": "pending_review",
            "created_at": base_time + timedelta(days=i*5, hours=random.randint(9, 17)),
            "reference_number": f"EXPORT-2024-{i+1:03d}",
            "description": "Trade settlement",
            "embedding": [random.random() for _ in range(config.VECTOR_DIMENSION)],
            "ml_features": {
                "time_of_day": "business_hours",
                "trade_finance": True,
                "high_value": True,
                "cross_border": True
            },
            "risk_flags": ["high_amount", "international", "enhanced_due_diligence"],
            "processing_stages": [],
            "rules_applied": [],
            "regulatory": {}
        }
        transactions.append(txn)

    # Scenario 5: Velocity/pattern detection
    velocity_base_time = datetime.now(timezone.utc) - timedelta(hours=2)
    for i in range(4):  # 4 transactions in 2 hours
        txn = {
            "transaction_id": f"TXN_VELOCITY_{i+1:03d}",
            "transaction_type": "ach",
            "amount": to_decimal(25000.00),
            "currency": "USD",
            "sender": {
                "name": "High Risk Trader LLC",
                "account_number": "ACC_HR_001",
                "customer_id": "CUST_HIGHRISK_001",
                "country": "US"
            },
            "recipient": {
                "name": f"Quick Recipient {i+1}",
                "account_number": f"ACC_QUICK_{i+1:03d}",
                "country": "US"
            },
            "status": "escalated",
            "created_at": velocity_base_time + timedelta(minutes=i*30),
            "reference_number": f"RAPID-{i+1:03d}",
            "description": "Rapid transaction",
            "embedding": [random.random() for _ in range(config.VECTOR_DIMENSION)],
            "ml_features": {
                "velocity_1h": 3 if i >= 2 else i+1,
                "total_amount_1h": to_decimal((i+1) * 25000),
                "rapid_succession": True
            },
            "risk_flags": ["velocity_pattern", "rapid_succession"],
            "processing_stages": [],
            "rules_applied": [],
            "regulatory": {}
        }
        transactions.append(txn)

    # Scenario 6: Human review queue scenarios
    review_scenarios = [
        {"priority": "high", "reason": "compliance_flag"},
        {"priority": "medium", "reason": "unusual_pattern"},
        {"priority": "low", "reason": "routine_check"}
    ]

    for i, scenario in enumerate(review_scenarios):
        txn = {
            "transaction_id": f"TXN_REVIEW_{i+1:03d}",
            "transaction_type": "wire_transfer",
            "amount": to_decimal(random.uniform(30000, 80000)),
            "currency": "USD",
            "sender": {
                "name": "Manufacturing Corp",
                "account_number": "ACC_MC_001",
                "customer_id": "CUST_MEDIUMRISK_001",
                "country": "US"
            },
            "recipient": {
                "name": f"Review Recipient {i+1}",
                "account_number": f"ACC_REV_{i+1:03d}",
                "country": "CA"
            },
            "status": "pending_review",
            "created_at": base_time + timedelta(days=i, hours=random.randint(9, 17)),
            "reference_number": f"REV-2024-{i+1:03d}",
            "description": f"Transaction requiring {scenario['priority']} priority review",
            "embedding": [random.random() for _ in range(config.VECTOR_DIMENSION)],
            "ml_features": {
                "review_priority": scenario["priority"],
                "review_reason": scenario["reason"]
            },
            "risk_flags": [scenario["reason"]],
            "processing_stages": [],
            "rules_applied": [],
            "regulatory": {}
        }
        transactions.append(txn)

    return transactions


def create_test_decisions(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create test decisions for transactions."""
    decisions = []

    decision_mapping = {
        "approved": {"decision": "approve", "confidence": (90, 99), "risk": (10, 30)},
        "rejected": {"decision": "reject", "confidence": (85, 95), "risk": (70, 95)},
        "escalated": {"decision": "escalate", "confidence": (60, 80), "risk": (40, 70)},
        "pending_review": {"decision": "escalate", "confidence": (50, 75), "risk": (45, 65)},
        "pending_manager_approval": {"decision": "approve", "confidence": (80, 90), "risk": (20, 40)}
    }

    for txn in transactions:
        status = txn["status"]
        if status in decision_mapping:
            mapping = decision_mapping[status]
            decision = {
                "decision_id": f"DEC_{txn['transaction_id'][4:]}",
                "transaction_id": txn["transaction_id"],
                "decision": mapping["decision"],
                "confidence_score": random.uniform(*mapping["confidence"]),
                "risk_score": random.uniform(*mapping["risk"]),
                "model_version": "claude-3-sonnet-bedrock",
                "processing_time_ms": random.randint(500, 3000),
                "created_at": txn["created_at"] + timedelta(seconds=random.randint(1, 30)),
                "reasoning": {
                    "primary_reasoning": f"Automated decision based on {status} status",
                    "risk_factors": txn.get("risk_flags", []),
                    "compliance_checks": ["kyc_verified", "sanctions_screening"],
                    "pattern_analysis": "normal" if status == "approved" else "flagged"
                },
                "rules_triggered": txn.get("risk_flags", []),
                "similar_cases": [
                    {"transaction_id": f"SIMILAR_{i}", "similarity_score": random.uniform(0.7, 0.9)}
                    for i in range(random.randint(1, 3))
                ],
                "risk_factors": txn.get("risk_flags", [])
            }
            decisions.append(decision)

    return decisions


def create_test_human_reviews(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create test human review records."""
    reviews = []
    reviewers = ["analyst1@company.com", "analyst2@company.com", "manager@company.com"]

    review_statuses = ["pending", "in_progress", "completed"]

    for txn in transactions:
        if txn["status"] in ["pending_review", "escalated", "pending_manager_approval"]:
            status = random.choice(review_statuses)
            review = {
                "review_id": f"REV_{txn['transaction_id'][4:]}",
                "transaction_id": txn["transaction_id"],
                "assigned_to": random.choice(reviewers),
                "priority": txn.get("ml_features", {}).get("review_priority", "medium"),
                "status": status,
                "created_at": txn["created_at"] + timedelta(minutes=random.randint(5, 30)),
                "assigned_at": txn["created_at"] + timedelta(minutes=random.randint(10, 60)),
                "sla_deadline": txn["created_at"] + timedelta(hours=24 if txn.get("ml_features", {}).get("review_priority") == "high" else 72),
                "ai_recommendation": {
                    "recommended_action": "escalate" if "suspicious" in txn["transaction_id"] else "approve",
                    "confidence": random.uniform(60, 85),
                    "key_concerns": txn.get("risk_flags", [])
                }
            }

            if status == "completed":
                review["completed_at"] = review["assigned_at"] + timedelta(hours=random.randint(1, 24))
                review["human_decision"] = {
                    "decision": random.choice(["approve", "reject", "escalate"]),
                    "reasoning": "Manual review completed",
                    "reviewer_notes": "Standard compliance review"
                }

            reviews.append(review)

    return reviews


def create_test_notifications(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create test notification records."""
    notifications = []

    notification_types = {
        "approved": "transaction_approved",
        "rejected": "transaction_rejected",
        "escalated": "transaction_escalated",
        "pending_review": "review_required"
    }

    for txn in transactions:
        if txn["status"] in notification_types:
            notification = {
                "notification_id": f"NOTIF_{txn['transaction_id'][4:]}",
                "transaction_id": txn["transaction_id"],
                "notification_type": notification_types[txn["status"]],
                "priority": "high" if "suspicious" in txn["transaction_id"] else "medium",
                "status": random.choice(["sent", "delivered", "acknowledged"]),
                "subject": f"Transaction {txn['status'].replace('_', ' ').title()}: {txn['transaction_id']}",
                "message": f"Transaction {txn['transaction_id']} has been {txn['status']}",
                "recipients": [
                    {"email": "compliance@company.com", "type": "primary"},
                    {"email": "alerts@company.com", "type": "secondary"}
                ],
                "created_at": txn["created_at"] + timedelta(minutes=random.randint(1, 10)),
                "sent_at": txn["created_at"] + timedelta(minutes=random.randint(2, 15)),
                "metadata": {
                    "amount": txn["amount"],
                    "sender": txn["sender"]["name"],
                    "recipient": txn["recipient"]["name"]
                }
            }
            notifications.append(notification)

    return notifications


def create_test_audit_events(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create test audit events."""
    events = []

    event_types = [
        "transaction_created", "transaction_processed", "decision_made",
        "rule_triggered", "review_assigned", "status_changed"
    ]

    for txn in transactions:
        # Create multiple audit events per transaction
        base_time = txn["created_at"]

        # Transaction created event
        events.append({
            "event_id": f"EVT_{txn['transaction_id'][4:]}_001",
            "transaction_id": txn["transaction_id"],
            "event_type": "transaction_created",
            "event_category": "transaction_lifecycle",
            "severity": "info",
            "timestamp": base_time,
            "event_data": {
                "amount": txn["amount"],
                "type": txn["transaction_type"],
                "sender": txn["sender"]["customer_id"]
            },
            "context": {
                "source": "api",
                "user_agent": "transaction-processor/1.0"
            }
        })

        # Decision made event
        events.append({
            "event_id": f"EVT_{txn['transaction_id'][4:]}_002",
            "transaction_id": txn["transaction_id"],
            "event_type": "decision_made",
            "event_category": "ai_decision",
            "severity": "warning" if txn["status"] in ["rejected", "escalated"] else "info",
            "timestamp": base_time + timedelta(seconds=30),
            "event_data": {
                "decision": txn["status"],
                "risk_flags": txn.get("risk_flags", []),
                "processing_time_ms": random.randint(500, 3000)
            },
            "context": {
                "model": "claude-3-sonnet-bedrock",
                "workflow_id": f"workflow-{txn['transaction_id']}"
            }
        })

    return events


def create_test_system_metrics() -> List[Dict[str, Any]]:
    """Create test system metrics."""
    metrics = []
    base_time = datetime.now(timezone.utc) - timedelta(hours=24)

    metric_types = [
        {"name": "transaction_processing_time", "unit": "ms", "range": (500, 3000)},
        {"name": "ai_model_response_time", "unit": "ms", "range": (200, 1500)},
        {"name": "database_query_time", "unit": "ms", "range": (50, 500)},
        {"name": "approval_rate", "unit": "percentage", "range": (75, 95)},
        {"name": "false_positive_rate", "unit": "percentage", "range": (2, 8)},
        {"name": "queue_depth", "unit": "count", "range": (0, 50)}
    ]

    # Generate hourly metrics for the past 24 hours
    for hour in range(24):
        timestamp = base_time + timedelta(hours=hour)

        for metric_type in metric_types:
            metric = {
                "metric_id": f"MET_{hour:02d}_{metric_type['name']}",
                "timestamp": timestamp,
                "metric_type": "performance",
                "metric_name": metric_type["name"],
                "value": random.uniform(*metric_type["range"]),
                "unit": metric_type["unit"],
                "dimensions": {
                    "environment": "development",
                    "service": "transaction-processor",
                    "hour_of_day": hour
                }
            }
            metrics.append(metric)

    return metrics


if __name__ == "__main__":
    setup_couchbase()
