"""
Handler for JuiceFS storage management.

Creates and manages JuiceFS-backed PVC for OdooInstance filestore:
1. On create: Create JuiceFS secret and PVC
2. On update: Update configuration if needed
3. On delete: Resources are deleted via owner reference cascade

JuiceFS metadata is stored in the CNPG PostgreSQL cluster (juicefs database),
enabling synchronized PITR recovery of both Odoo data and filestore metadata.
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

# Default values
DEFAULT_STORAGE_SIZE = "50Gi"
DEFAULT_TRASH_DAYS = 30
JUICEFS_STORAGE_CLASS = "juicefs-sc"


class JuiceFSStorage(ResourceHandler):
    """Manages JuiceFS-backed storage for OdooInstance filestore."""

    def __init__(self, handler: OdooHandler):
        super().__init__(handler)
        self.handler = handler
        self.filestore_spec = self.spec.get("filestore", {})
        self.cnpg_cluster = handler.cnpg_cluster

    def _read_resource(self):
        """Read the JuiceFS PVC."""
        return client.CoreV1Api().read_namespaced_persistent_volume_claim(
            name=self._pvc_name,
            namespace=self.namespace,
        )

    @property
    def _pvc_name(self) -> str:
        """Name of the JuiceFS PVC."""
        return f"{self.name}-filestore"

    @property
    def _secret_name(self) -> str:
        """Name of the JuiceFS configuration secret."""
        return f"{self.name}-juicefs"

    @update_if_exists
    def handle_create(self):
        """Create JuiceFS secret and PVC."""
        logger.info(f"Creating JuiceFS storage for {self.name}")
        self._create_or_update_secret()
        self._create_pvc()

    @create_if_missing
    def handle_update(self):
        """Update JuiceFS configuration."""
        logger.info(f"Updating JuiceFS storage for {self.name}")
        self._create_or_update_secret()

    def _create_or_update_secret(self):
        """Create or update the JuiceFS configuration secret."""
        secret_body = self._get_secret_body()

        try:
            client.CoreV1Api().read_namespaced_secret(
                name=self._secret_name,
                namespace=self.namespace,
            )
            # Secret exists, update it
            client.CoreV1Api().patch_namespaced_secret(
                name=self._secret_name,
                namespace=self.namespace,
                body=secret_body,
            )
            logger.info(f"Updated JuiceFS secret {self._secret_name}")
        except ApiException as e:
            if e.status == 404:
                # Secret doesn't exist, create it
                client.CoreV1Api().create_namespaced_secret(
                    namespace=self.namespace,
                    body=secret_body,
                )
                logger.info(f"Created JuiceFS secret {self._secret_name}")
            else:
                raise

    def _create_pvc(self):
        """Create the JuiceFS PVC."""
        pvc = self._get_pvc_body()
        self._resource = client.CoreV1Api().create_namespaced_persistent_volume_claim(
            namespace=self.namespace,
            body=pvc,
        )
        logger.info(f"Created JuiceFS PVC {self._pvc_name}")

    def _get_secret_body(self) -> client.V1Secret:
        """Build the JuiceFS secret specification."""
        s3_bucket = self.filestore_spec.get("s3Bucket", f"{self.name}-juicefs")
        s3_endpoint = self.filestore_spec.get("s3Endpoint", "")
        s3_secret_ref = self.filestore_spec.get("s3CredentialsSecretRef", {})
        trash_days = self.filestore_spec.get("trashDays", DEFAULT_TRASH_DAYS)

        # Get S3 credentials from referenced secret
        access_key, secret_key = self._get_s3_credentials(s3_secret_ref)

        # Get database connection info from CNPG cluster
        db_info = self.cnpg_cluster.get_connection_info()
        if not db_info:
            raise ValueError("CNPG cluster not ready, cannot configure JuiceFS")

        # Build metaurl for JuiceFS
        meta_url = (
            f"postgres://{db_info['username']}:{db_info['password']}"
            f"@{db_info['host']}:{db_info['port']}/juicefs"
        )

        # JuiceFS configuration
        juicefs_config = {
            "name": self.name,
            "metaurl": meta_url,
            "storage": "s3",
            "bucket": f"{s3_endpoint}/{s3_bucket}",
            "access-key": access_key,
            "secret-key": secret_key,
            "trash-days": str(trash_days),
        }

        # Encode config as JSON in secret
        string_data = {
            key: value for key, value in juicefs_config.items()
        }

        return client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=self._secret_name,
                namespace=self.namespace,
                owner_references=[self.owner_reference],
            ),
            string_data=string_data,
        )

    def _get_pvc_body(self) -> client.V1PersistentVolumeClaim:
        """Build the JuiceFS PVC specification."""
        storage_size = self.filestore_spec.get("storageSize", DEFAULT_STORAGE_SIZE)
        storage_class = self.filestore_spec.get("storageClass", JUICEFS_STORAGE_CLASS)

        return client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=self._pvc_name,
                namespace=self.namespace,
                owner_references=[self.owner_reference],
                annotations={
                    "juicefs/secret-name": self._secret_name,
                },
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteMany"],
                storage_class_name=storage_class,
                resources=client.V1VolumeResourceRequirements(
                    requests={"storage": storage_size},
                ),
            ),
        )

    def _get_s3_credentials(self, s3_secret_ref: dict) -> tuple[str, str]:
        """Fetch S3 credentials from the referenced secret."""
        secret_name = s3_secret_ref.get("name")
        if not secret_name:
            raise ValueError("filestore.s3CredentialsSecretRef.name is required")

        secret_namespace = s3_secret_ref.get("namespace", self.namespace)

        try:
            secret = client.CoreV1Api().read_namespaced_secret(
                name=secret_name,
                namespace=secret_namespace,
            )
            access_key = base64.b64decode(
                secret.data.get("accessKey", "")
            ).decode("utf-8")
            secret_key = base64.b64decode(
                secret.data.get("secretKey", "")
            ).decode("utf-8")

            if not access_key or not secret_key:
                raise ValueError(
                    f"Secret {secret_namespace}/{secret_name} missing accessKey or secretKey"
                )

            return access_key, secret_key
        except ApiException as e:
            raise ValueError(
                f"Failed to read S3 credentials secret {secret_namespace}/{secret_name}: {e}"
            )
