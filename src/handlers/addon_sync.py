"""
Handler for Git-sync addon management.

Manages git-sync sidecar containers for OdooInstance addon repositories:
1. Configures sidecar containers for each addon repository
2. Supports private repos via SSH keys
3. Mounts addons to /mnt/addons/<addon-name>/
4. Auto-generates addons_path for odoo.conf

Addons are synced live - commits trigger automatic sync within 60 seconds.
"""

from __future__ import annotations
from kubernetes import client
import logging
from typing import TYPE_CHECKING, List, Tuple

if TYPE_CHECKING:
    from .odoo_handler import OdooHandler

logger = logging.getLogger(__name__)

# Git-sync image
GIT_SYNC_IMAGE = "registry.k8s.io/git-sync/git-sync:v4.2.1"

# Default sync period
DEFAULT_SYNC_PERIOD = "60s"


class AddonSync:
    """Manages git-sync sidecars for addon repositories."""

    def __init__(self, handler: OdooHandler):
        self.handler = handler
        self.spec = handler.spec
        self.namespace = handler.namespace
        self.name = handler.name
        self.addons = self.spec.get("addons", [])

    def get_sidecar_containers(self) -> List[client.V1Container]:
        """Build git-sync sidecar containers for all configured addons."""
        containers = []
        for addon in self.addons:
            container = self._build_sidecar(addon)
            containers.append(container)
        return containers

    def get_volumes(self) -> List[client.V1Volume]:
        """Build volumes needed for addon sync."""
        volumes = []

        # Shared addons volume (emptyDir)
        volumes.append(
            client.V1Volume(
                name="addons",
                empty_dir=client.V1EmptyDirVolumeSource(),
            )
        )

        # SSH key volumes for private repos
        for addon in self.addons:
            ssh_secret_ref = addon.get("sshSecretRef")
            if ssh_secret_ref:
                addon_name = addon.get("name", "addon")
                volumes.append(
                    client.V1Volume(
                        name=f"ssh-{addon_name}",
                        secret=client.V1SecretVolumeSource(
                            secret_name=ssh_secret_ref.get("name"),
                            default_mode=0o400,
                        ),
                    )
                )

        return volumes

    def get_volume_mounts(self) -> List[client.V1VolumeMount]:
        """Build volume mounts for the main Odoo container."""
        return [
            client.V1VolumeMount(
                name="addons",
                mount_path="/mnt/addons",
            )
        ]

    def get_addons_path(self) -> str:
        """Build the addons_path for odoo.conf."""
        paths = ["/mnt/extra-addons"]

        for addon in self.addons:
            addon_name = addon.get("name", "addon")
            paths.append(f"/mnt/addons/{addon_name}")

        return ",".join(paths)

    def _build_sidecar(self, addon: dict) -> client.V1Container:
        """Build a git-sync sidecar container for an addon."""
        addon_name = addon.get("name", "addon")
        repo = addon.get("repo", "")
        branch = addon.get("branch")
        tag = addon.get("tag")
        ssh_secret_ref = addon.get("sshSecretRef")

        # Determine ref (branch or tag)
        ref = tag if tag else (branch if branch else "main")

        # Build args
        args = [
            f"--repo={repo}",
            f"--ref={ref}",
            f"--root=/mnt/addons/{addon_name}",
            f"--period={DEFAULT_SYNC_PERIOD}",
            "--link=current",
            "--one-time=false",
        ]

        # Volume mounts
        volume_mounts = [
            client.V1VolumeMount(
                name="addons",
                mount_path="/mnt/addons",
            ),
        ]

        # SSH key mount for private repos
        env = []
        if ssh_secret_ref:
            volume_mounts.append(
                client.V1VolumeMount(
                    name=f"ssh-{addon_name}",
                    mount_path="/etc/git-secret",
                    read_only=True,
                ),
            )
            args.extend([
                "--ssh",
                "--ssh-key-file=/etc/git-secret/ssh-privatekey",
                "--ssh-known-hosts=false",
            ])
            env.append(
                client.V1EnvVar(
                    name="GIT_SSH_COMMAND",
                    value="ssh -o StrictHostKeyChecking=no",
                )
            )

        return client.V1Container(
            name=f"git-sync-{addon_name}",
            image=GIT_SYNC_IMAGE,
            args=args,
            env=env if env else None,
            volume_mounts=volume_mounts,
            resources=client.V1ResourceRequirements(
                requests={"cpu": "10m", "memory": "32Mi"},
                limits={"cpu": "100m", "memory": "128Mi"},
            ),
            security_context=client.V1SecurityContext(
                run_as_user=65534,
                run_as_group=65534,
            ),
        )

    def has_addons(self) -> bool:
        """Check if any addons are configured."""
        return len(self.addons) > 0
