import base64

from kubernetes import client
from passlib.context import CryptContext

from .resource_handler import (ResourceHandler, create_if_missing,
                               update_if_exists)

crypt_context = CryptContext(
    schemes=["pbkdf2_sha512"],
    pbkdf2_sha512__rounds=600_000,
)


class OdooConf(ResourceHandler):
    """Manages the Odoo configuration file ConfigMap."""

    def __init__(self, handler):
        super().__init__(handler)
        self.odoo_user_secret = handler.odoo_user_secret

    def _read_resource(self):
        return client.CoreV1Api().read_namespaced_config_map(
            name=f"{self.name}-odoo-conf",
            namespace=self.namespace,
        )

    @update_if_exists
    def handle_create(self):
        configmap = self._get_resource_body()
        self._resource = client.CoreV1Api().create_namespaced_config_map(
            namespace=self.namespace,
            body=configmap,
        )

    @create_if_missing
    def handle_update(self):
        configmap = self._get_resource_body()
        self._resource = client.CoreV1Api().patch_namespaced_config_map(
            name=f"{self.name}-odoo-conf",
            namespace=self.namespace,
            body=configmap,
        )

    def _get_resource_body(self):
        metadata = client.V1ObjectMeta(
            owner_references=[self.owner_reference],
            namespace=self.namespace,
            name=f"{self.name}-odoo-conf",
        )

        # Get addons_path from addon_sync if configured
        if hasattr(self.handler, 'addon_sync') and self.handler.addon_sync.has_addons():
            addons_path = self.handler.addon_sync.get_addons_path()
        else:
            addons_path = "/mnt/extra-addons"

        # Worker configuration (default: 4 workers)
        workers = self.spec.get("workers", 4)

        config_options = {
            "data_dir": "/var/lib/odoo",
            "logfile": "",
            "log_level": "info",
            "proxy_mode": "True",
            "addons_path": addons_path,
            "db_user": base64.b64decode(
                self.odoo_user_secret.resource.data["username"]
            ).decode(),
            "list_db": "False",  # Disable database manager
            "http_interface": "0.0.0.0",
            "http_port": "8069",
            # Worker settings for production
            "workers": str(workers),
            "max_cron_threads": "2",
            # Gevent for longpolling
            "gevent_port": "8072",
        }
        admin_pw = self.spec.get("adminPassword", "")
        if admin_pw:
            admin_pw = crypt_context.hash(admin_pw)
            config_options.update(admin_passwd=admin_pw)
        config_options.update(self.spec.get("configOptions", {}))
        conf_text = "[options]\n"
        for key, value in config_options.items():
            conf_text += f"{key} = {value}\n"
        return client.V1ConfigMap(metadata=metadata, data={"odoo.conf": conf_text})
