# Odoo Operator Helm Chart

A Helm chart for deploying the Odoo Operator on Kubernetes.

## Overview

This chart deploys:
- The Odoo Operator deployment
- Custom Resource Definitions (CRDs) for OdooInstance, OdooBackupJob, OdooRestoreJob, OdooUpgradeJob, OdooInitJob
- RBAC resources (ServiceAccount, ClusterRole, ClusterRoleBinding)
- ConfigMaps for operator configuration
- Webhook server for validation

## Prerequisites

- Kubernetes 1.20+
- Helm 3.x
- cert-manager (for TLS certificates and webhook)
- S3-compatible storage for backups (Wasabi, AWS S3, MinIO)

**For CNPG mode (recommended):**
- CloudNativePG operator installed in cluster
- JuiceFS CSI driver installed in cluster

**For Legacy mode:**
- PostgreSQL database accessible from the cluster
- A Kubernetes secret containing cluster configurations

## Installation

### Quick Install (CNPG mode)

```bash
helm install odoo-operator ./odoo-operator \
  --namespace odoo-operator \
  --create-namespace
```

### With External PostgreSQL (Legacy mode)

```bash
# 1. Create clusters.yaml file
cat > clusters.yaml << EOF
main:
  host: "postgres.database.svc.cluster.local"
  port: 5432
  adminUser: "postgres"
  adminPassword: "your-password"
  default: true
EOF

# 2. Create the secret
kubectl create secret generic postgres-clusters -n odoo-operator \
  --from-file=clusters.yaml=clusters.yaml

# 3. Install with the secret reference
helm install odoo-operator ./odoo-operator \
  --namespace odoo-operator \
  --create-namespace \
  --set postgresClustersSecretRef.name=postgres-clusters
```

### With Custom Values

```bash
helm install odoo-operator ./odoo-operator \
  --namespace odoo-operator \
  --create-namespace \
  -f my-values.yaml
```

## Configuration

### Operator Settings

| Parameter                            | Description              | Default                                    |
| ------------------------------------ | ------------------------ | ------------------------------------------ |
| `operator.image`                     | Operator container image | `registry.bemade.org/bemade/odoo-operator` |
| `operator.resources.requests.cpu`    | CPU request              | `50m`                                      |
| `operator.resources.requests.memory` | Memory request           | `64Mi`                                     |
| `operator.resources.limits.cpu`      | CPU limit                | `250m`                                     |
| `operator.resources.limits.memory`   | Memory limit             | `256Mi`                                    |
| `operator.affinity`                  | Pod affinity rules       | `{}`                                       |
| `operator.tolerations`               | Pod tolerations          | `[]`                                       |
| `operator.annotations`               | Pod annotations          | `{}`                                       |

### Image Pull Secrets

The image pull secrets are used to authenticate with the container registry for pulling
Odoo instance images. Multiple secrets can be specified if needed, usually named after
the registry domain.

| Parameter                     | Description       | Default               |
| ----------------------------- | ----------------- | --------------------- |
| `imagePullSecrets[].domain`   | Registry domain   | `registry.bemade.org` |
| `imagePullSecrets[].username` | Registry username | `username`            |
| `imagePullSecrets[].password` | Registry password | `password`            |

### Default Instance Settings

These defaults apply to new OdooInstance resources:

| Parameter                            | Description             | Default     |
| ------------------------------------ | ----------------------- | ----------- |
| `defaults.odooImage`                 | Default Odoo image      | `odoo:19.0` |
| `defaults.storageClass`              | Default storage class   | `standard`  |
| `defaults.resources.requests.cpu`    | Default CPU request     | `500m`      |
| `defaults.resources.requests.memory` | Default memory request  | `1Gi`       |
| `defaults.resources.limits.cpu`      | Default CPU limit       | `4000m`     |
| `defaults.resources.limits.memory`   | Default memory limit    | `4Gi`       |
| `defaults.affinity`                  | Default pod affinity    | `{}`        |
| `defaults.tolerations`               | Default pod tolerations | `[]`        |
| `defaults.ingressClass`              | Default ingress class   | *(unset)*   |

### PostgreSQL Cluster Configuration (Legacy Mode Only)

For legacy mode with external PostgreSQL clusters, create a secret containing cluster configurations.
This is **not required** for CNPG mode.

| Parameter                        | Description                                 | Default      |
| -------------------------------- | ------------------------------------------- | ------------ |
| `postgresClustersSecretRef.name` | Name of the secret containing clusters.yaml | *(optional)* |

## Example values.yaml

```yaml
operator:
  image: registry.bemade.org/bemade/odoo-operator
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi

imagePullSecrets:
  - domain: registry.bemade.org
    username: myuser
    password: mypassword

defaults:
  odooImage: odoo:19.0
  storageClass: "longhorn"
  resources:
    requests:
      cpu: 500m
      memory: 1Gi
    limits:
      cpu: 4000m
      memory: 8Gi

# Optional: Only needed for legacy mode with external PostgreSQL
# postgresClustersSecretRef:
#   name: "postgres-clusters"
```

## Custom Resource Definitions

This chart installs five CRDs:

### OdooInstance

Defines an Odoo deployment with all its configuration. Supports two modes:

**CNPG Mode** (with `spec.database.wal`):
```yaml
apiVersion: bemade.org/v1
kind: OdooInstance
metadata:
  name: my-odoo
spec:
  image: odoo:19.0
  replicas: 1
  database:
    replicas: 3
    storage: 10Gi
    wal:
      s3Bucket: my-odoo-wal
      s3Endpoint: https://s3.wasabisys.com
      s3CredentialsSecretRef:
        name: s3-credentials
  filestore:
    storageSize: 50Gi
    s3Bucket: my-odoo-filestore
    s3Endpoint: https://s3.wasabisys.com
    s3CredentialsSecretRef:
      name: s3-credentials
  addons:
    - name: enterprise
      repo: git@gitlab.com:odoo/enterprise.git
      branch: "19.0"
      sshSecretRef:
        name: gitlab-ssh-key
  ingress:
    hosts:
      - my-odoo.example.com
    issuer: letsencrypt-prod
```

**Legacy Mode** (with `spec.database.cluster`):
```yaml
apiVersion: bemade.org/v1
kind: OdooInstance
metadata:
  name: my-odoo
spec:
  image: odoo:19.0
  replicas: 1
  adminPassword: "admin-password"
  database:
    cluster: main  # References postgres-clusters secret
  filestore:
    storageSize: 50Gi
    storageClass: longhorn
  ingress:
    hosts:
      - my-odoo.example.com
    issuer: letsencrypt-prod
```

### OdooBackupJob

Triggers a backup to S3-compatible storage.

```yaml
apiVersion: bemade.org/v1
kind: OdooBackupJob
metadata:
  name: backup-my-odoo
spec:
  odooInstanceRef:
    name: my-odoo
  format: zip
  destination:
    bucket: backups
    objectKey: my-odoo/backup.zip
    endpoint: https://s3.wasabisys.com
    s3CredentialsSecretRef:
      name: s3-credentials
```

### OdooRestoreJob

Restores a backup to an OdooInstance. Supports S3 backups, live Odoo instances, and PITR.

```yaml
apiVersion: bemade.org/v1
kind: OdooRestoreJob
metadata:
  name: restore-my-odoo
spec:
  odooInstanceRef:
    name: my-odoo
  source:
    type: pitr  # or "s3", "odoo"
    pitr:
      targetTime: "2024-01-15T10:30:00Z"
```

### OdooUpgradeJob

Triggers module upgrades on an OdooInstance.

```yaml
apiVersion: bemade.org/v1
kind: OdooUpgradeJob
metadata:
  name: upgrade-my-odoo
spec:
  odooInstanceRef:
    name: my-odoo
  modules:
    - sale
    - purchase
```

### OdooInitJob

Initializes a fresh database with specified modules.

```yaml
apiVersion: bemade.org/v1
kind: OdooInitJob
metadata:
  name: init-my-odoo
spec:
  odooInstanceRef:
    name: my-odoo
  modules:
    - base
    - sale
```

## Upgrading

```bash
helm upgrade odoo-operator ./odoo-operator \
  --namespace odoo-operator \
  -f my-values.yaml
```

**Note**: CRDs have `helm.sh/resource-policy: keep` annotation, so they won't be deleted on uninstall.

## Uninstalling

```bash
helm uninstall odoo-operator --namespace odoo-operator
```

To also remove CRDs (this will delete all OdooInstance resources!):

```bash
kubectl delete crd odooinstances.bemade.org
kubectl delete crd odoobackupjobs.bemade.org
kubectl delete crd odoorestorejobs.bemade.org
kubectl delete crd odooupgradejobs.bemade.org
kubectl delete crd odooinitjobs.bemade.org
```

## Troubleshooting

### Check Operator Logs

```bash
kubectl logs -n odoo-operator deployment/odoo-operator -f
```

### Check OdooInstance Status

```bash
kubectl get odooinstances -A
kubectl describe odooinstance my-odoo
```

### Check Job Status

```bash
kubectl get odoobackupjobs -A
kubectl get odoorestorejobs -A
kubectl get odooupgradejobs -A
kubectl get odooinitjobs -A
kubectl get jobs -n <namespace>
```

## License

LGPLv3