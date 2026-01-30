from kubernetes import client
from .resource_handler import ResourceHandler, update_if_exists, create_if_missing
from typing import cast


class Ingress(ResourceHandler):
    """Handler for standard Kubernetes Ingress resources."""

    def __init__(self, handler):
        super().__init__(handler)
        self.operator_ns = handler.operator_ns
        self.tls_cert = handler.tls_cert
        # Optional defaults loaded by OdooHandler from /etc/odoo/instance-defaults.yaml
        self.defaults = getattr(handler, "defaults", {}) or {}

    def _read_resource(self):
        return client.NetworkingV1Api().read_namespaced_ingress(
            name=self.name,
            namespace=self.namespace,
        )

    def _build_ingress_spec(self):
        """Build the Ingress spec."""
        ingress_cfg = self.spec.get("ingress", {}) or {}
        if not isinstance(ingress_cfg, dict):
            ingress_cfg = {}

        # Get hostnames and optional ingress class (spec overrides defaults)
        hostnames = ingress_cfg.get("hosts", [])
        ingress_class_name = ingress_cfg.get("class")
        if ingress_class_name is None:
            ingress_class_name = cast(dict, self.defaults).get("ingressClass")
        tls_secret_name = self.tls_cert.resource.get("metadata", {}).get("name")

        # Build TLS configuration
        tls = [
            client.V1IngressTLS(
                hosts=hostnames,
                secret_name=tls_secret_name,
            )
        ]

        # Build rules for each hostname
        rules = []
        for hostname in hostnames:
            rules.append(
                client.V1IngressRule(
                    host=hostname,
                    http=client.V1HTTPIngressRuleValue(
                        paths=[
                            # WebSocket path must come first for proper matching
                            client.V1HTTPIngressPath(
                                path="/websocket",
                                path_type="Prefix",
                                backend=client.V1IngressBackend(
                                    service=client.V1IngressServiceBackend(
                                        name=self.name,
                                        port=client.V1ServiceBackendPort(number=8072),
                                    )
                                ),
                            ),
                            # Main Odoo path
                            client.V1HTTPIngressPath(
                                path="/",
                                path_type="Prefix",
                                backend=client.V1IngressBackend(
                                    service=client.V1IngressServiceBackend(
                                        name=self.name,
                                        port=client.V1ServiceBackendPort(number=8069),
                                    )
                                ),
                            ),
                        ]
                    ),
                )
            )

        ingress_spec = client.V1IngressSpec(
            tls=tls,
            rules=rules,
        )
        # Only set ingressClassName when provided (spec or defaults). Otherwise let cluster default.
        if ingress_class_name:
            ingress_spec.ingress_class_name = ingress_class_name

        return client.V1Ingress(
            api_version="networking.k8s.io/v1",
            kind="Ingress",
            metadata=client.V1ObjectMeta(
                name=self.name,
                namespace=self.namespace,
                owner_references=[self.owner_reference],
            ),
            spec=ingress_spec,
        )

    @update_if_exists
    def handle_create(self):
        body = self._build_ingress_spec()
        self._resource = client.NetworkingV1Api().create_namespaced_ingress(
            namespace=self.namespace,
            body=body,
        )

    @create_if_missing
    def handle_update(self):
        body = self._build_ingress_spec()
        self._resource = client.NetworkingV1Api().replace_namespaced_ingress(
            name=self.name,
            namespace=self.namespace,
            body=body,
        )
