"""
Handler for CNPG (CloudNativePG) Cluster management.

Creates and manages PostgreSQL clusters for OdooInstance:
1. On create: Create a CNPG Cluster CR with WAL archiving to S3
2. On update: Update cluster configuration if needed
3. On delete: Cluster is deleted via owner reference cascade

The cluster includes two databases:
- odoo: Main Odoo application database
- juicefs: JuiceFS metadata database

Both databases are backed up together via continuous WAL archiving,
enabling synchronized Point-in-Time Recovery (PITR).
"""

from __future__ import annotations
from kubernetes import client
from kubernetes.client.rest import ApiException
from .resource_handler import ResourceHandler, update_if_exists, create_if_missing
import base64
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .odoo_handler import OdooHandler

logger = logging.getLogger(__name__)

# CNPG API details
CNPG_GROUP = "postgresql.cnpg.io"
CNPG_VERSION = "v1"
CNPG_PLURAL = "clusters"

# Default values
DEFAULT_INSTANCES = 3
DEFAULT_STORAGE = "10Gi"
DEFAULT_WAL_RETENTION_DAYS = 30


class CNPGCluster(ResourceHandler):
    """Manages the CNPG Cluster for an OdooInstance."""

    def __init__(self, handler: OdooHandler):
        super().__init__(handler)
        self.handler = handler
        self.database_spec = self.spec.get("database", {})

    def _read_resource(self):
        """Read the CNPG Cluster resource."""
        return client.CustomObjectsApi().get_namespaced_custom_object(
            group=CNPG_GROUP,
            version=CNPG_VERSION,
            namespace=self.namespace,
            plural=CNPG_PLURAL,
            name=self._cluster_name,
        )

    @property
    def _cluster_name(self) -> str:
        """Name of the CNPG cluster."""
        return f"{self.name}-pg"

    @property
    def _rw_service_name(self) -> str:
        """Name of the read-write service for the cluster."""
        return f"{self._cluster_name}-rw"

    @property
    def _secret_name(self) -> str:
        """Name of the secret containing database credentials."""
        return f"{self._cluster_name}-app"

    @update_if_exists
    def handle_create(self):
        """Create the CNPG Cluster."""
        logger.info(f"Creating CNPG cluster {self._cluster_name}")
        cluster = self._get_resource_body()
        self._resource = client.CustomObjectsApi().create_namespaced_custom_object(
            group=CNPG_GROUP,
            version=CNPG_VERSION,
            namespace=self.namespace,
            plural=CNPG_PLURAL,
            body=cluster,
        )

    @create_if_missing
    def handle_update(self):
        """Update the CNPG Cluster configuration."""
        logger.info(f"Updating CNPG cluster {self._cluster_name}")
        cluster = self._get_resource_body()
        self._resource = client.CustomObjectsApi().patch_namespaced_custom_object(
            group=CNPG_GROUP,
            version=CNPG_VERSION,
            namespace=self.namespace,
            plural=CNPG_PLURAL,
            name=self._cluster_name,
            body=cluster,
        )

    def _get_resource_body(self) -> dict:
        """Build the CNPG Cluster specification."""
        instances = self.database_spec.get("replicas", DEFAULT_INSTANCES)
        storage = self.database_spec.get("storage", DEFAULT_STORAGE)
        wal_config = self.database_spec.get("wal", {})

        cluster = {
            "apiVersion": f"{CNPG_GROUP}/{CNPG_VERSION}",
            "kind": "Cluster",
            "metadata": {
                "name": self._cluster_name,
                "namespace": self.namespace,
                "ownerReferences": [self._owner_reference_dict()],
            },
            "spec": {
                "instances": instances,
                "postgresql": {
                    "parameters": {
                        "max_connections": "200",
                        "shared_buffers": "256MB",
                        "effective_cache_size": "768MB",
                    },
                },
                "storage": {
                    "size": storage,
                },
                "bootstrap": {
                    "initdb": {
                        "database": "odoo",
                        "owner": "odoo",
                        "postInitSQL": [
                            "CREATE DATABASE juicefs OWNER odoo;",
                        ],
                    },
                },
            },
        }

        # Add WAL archiving if configured
        if wal_config:
            backup_config = self._build_backup_config(wal_config)
            if backup_config:
                cluster["spec"]["backup"] = backup_config

        return cluster

    def _build_backup_config(self, wal_config: dict) -> dict:
        """Build the backup configuration for WAL archiving."""
        s3_bucket = wal_config.get("s3Bucket")
        s3_endpoint = wal_config.get("s3Endpoint")
        s3_secret_ref = wal_config.get("s3CredentialsSecretRef", {})
        retention_days = wal_config.get("retentionDays", DEFAULT_WAL_RETENTION_DAYS)

        if not s3_bucket or not s3_endpoint or not s3_secret_ref.get("name"):
            logger.warning("WAL config incomplete, skipping backup configuration")
            return {}

        return {
            "barmanObjectStore": {
                "destinationPath": f"s3://{s3_bucket}/",
                "endpointURL": s3_endpoint,
                "s3Credentials": {
                    "accessKeyId": {
                        "name": s3_secret_ref["name"],
                        "key": "accessKey",
                    },
                    "secretAccessKey": {
                        "name": s3_secret_ref["name"],
                        "key": "secretKey",
                    },
                },
                "wal": {
                    "compression": "gzip",
                    "maxParallel": 2,
                },
            },
            "retentionPolicy": f"{retention_days}d",
        }

    def _owner_reference_dict(self) -> dict:
        """Convert owner reference to dict for custom objects API."""
        return {
            "apiVersion": "bemade.org/v1",
            "kind": "OdooInstance",
            "name": self.name,
            "uid": self.handler.uid,
            "blockOwnerDeletion": True,
        }

    def get_connection_info(self) -> dict:
        """Get database connection info from CNPG secret."""
        try:
            secret = client.CoreV1Api().read_namespaced_secret(
                name=self._secret_name,
                namespace=self.namespace,
            )
            return {
                "host": self._rw_service_name,
                "port": "5432",
                "username": base64.b64decode(secret.data.get("username", "")).decode(),
                "password": base64.b64decode(secret.data.get("password", "")).decode(),
                "database": "odoo",
            }
        except ApiException as e:
            if e.status == 404:
                logger.warning(f"CNPG secret {self._secret_name} not found yet")
                return {}
            raise

    def is_ready(self) -> bool:
        """Check if the CNPG cluster is ready."""
        try:
            cluster = self._read_resource()
            status = cluster.get("status", {})
            return status.get("readyInstances", 0) >= 1
        except ApiException:
            return False
