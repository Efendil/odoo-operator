"""
Database Initialization Handler

Handles the initialization of a new OdooInstance database based on the
initialization spec. This can be:
- fresh: Create an empty database (default)
- restore: Restore from another Odoo instance

When restore mode is specified, this handler patches the OdooInstance
to add a restore spec, which will then be handled by the RestoreJob handler.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .odoo_handler import OdooHandler

from .resource_handler import ResourceHandler
from kubernetes import client
import logging

logger = logging.getLogger(__name__)


class DatabaseInitializationHandler(ResourceHandler):
    """
    Handles database initialization for new OdooInstances.

    This handler checks the initialization spec and converts it to
    appropriate actions (e.g., patching with restore spec).
    """

    def __init__(self, handler: OdooHandler):
        self.handler = handler
        self.initialization_spec = handler.spec.get("initialization", {})
        self.mode = self.initialization_spec.get("mode", "fresh")

    def handle_create(self):
        """
        Handle database initialization on instance creation.

        If restore mode is specified, patch the OdooInstance with a restore spec.
        """
        if self.mode == "restore":
            self._handle_restore_initialization()
        else:
            logger.info(
                f"Database initialization mode is '{self.mode}' for {self.handler.name}, "
                "no action needed"
            )

    def _handle_restore_initialization(self):
        """
        Convert initialization restore config to a restore spec and patch the instance.
        """
        restore_config = self.initialization_spec.get("restore")
        if not restore_config:
            logger.warning(
                f"Restore mode specified but no restore config found for {self.handler.name}"
            )
            return

        logger.info(
            f"Converting initialization restore config to restore spec for {self.handler.name}"
        )

        # Build the restore spec from initialization config
        restore_spec = {
            "enabled": True,
            "url": restore_config.get("url"),
            "sourceDatabase": restore_config.get("sourceDatabase"),
            "targetDatabase": f"odoo_{self.handler.uid.replace('-', '_')}",
            "masterPassword": restore_config.get("masterPassword"),
            "withFilestore": restore_config.get("withFilestore", True),
            "neutralize": restore_config.get("neutralize", True),
        }

        # Patch the OdooInstance to add the restore spec
        try:
            logger.info(
                f"Patching {self.handler.name} with restore spec: {restore_spec}"
            )
            client.CustomObjectsApi().patch_namespaced_custom_object(
                group="bemade.org",
                version="v1",
                namespace=self.handler.namespace,
                plural="odooinstances",
                name=self.handler.name,
                body={"spec": {"restore": restore_spec}},
            )
            logger.info(
                f"Successfully patched {self.handler.name} with restore spec. "
                "RestoreJob will be triggered on update."
            )
        except Exception as e:
            logger.error(f"Failed to patch {self.handler.name} with restore spec: {e}")
            raise

    def handle_update(self):
        """No action needed on update."""
        pass

    def handle_delete(self):
        """No action needed on delete."""
        pass
