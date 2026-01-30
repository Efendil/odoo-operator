# Odoo Operator

A Kubernetes operator for managing production-grade Odoo instances at scale. This operator automates the deployment, database management, filestore provisioning, and lifecycle management of Odoo applications on Kubernetes.

## Features

### Core Features
- **Declarative Odoo Management**: Define Odoo instances as Kubernetes Custom Resources
- **Odoo 19.0 Support**: Default to latest Odoo 19.0 with 4-worker configuration
- **Standard Kubernetes Ingress**: Routes Odoo web (8069) and websocket (8072) traffic
- **Dual Database Mode**: Use managed CNPG clusters or external PostgreSQL

### CloudNativePG Integration (CNPG Mode)
- **Managed PostgreSQL Clusters**: Each Odoo instance gets its own CNPG-managed PostgreSQL cluster
- **WAL Archiving**: Continuous WAL archiving to S3/Wasabi for Point-in-Time Recovery (PITR)
- **30-Day PITR**: Recover to any point in time within 30 days
- **Synchronized Recovery**: Database and filestore metadata recovered together

### JuiceFS Filestore (CNPG Mode)
- **Distributed Filestore**: JuiceFS-backed storage with S3 data backend
- **CNPG Metadata**: JuiceFS metadata stored in the PostgreSQL cluster
- **30-Day Trash**: Deleted files retained for 30 days
- **ReadWriteMany**: Supports multiple Odoo replicas

### Git-Sync Addons
- **Multiple Repositories**: Sync addons from multiple Git repositories
- **Private Repos**: SSH key support for private repositories
- **Branch/Tag Support**: Pin to specific branches or tags
- **Auto-Sync**: Addons synced every 60 seconds

## Quick Start

### Prerequisites

- Kubernetes cluster (1.20+)
- Helm 3.x
- cert-manager with ClusterIssuer configured
- S3-compatible storage (Wasabi, AWS S3, MinIO)

**For CNPG mode (recommended):**
- CloudNativePG operator installed in cluster
- JuiceFS CSI driver installed in cluster

**For Legacy mode:**
- External PostgreSQL database accessible from the cluster

### Installation

#### Step 1: Build and Push the Operator Image

```bash
# Clone the repository
git clone https://github.com/bemade/odoo-operator.git
cd odoo-operator

# Build the operator image
docker build -t your-registry.com/odoo-operator:0.11.0 .

# Push to your registry
docker push your-registry.com/odoo-operator:0.11.0
```

#### Step 2: Install Prerequisites (CNPG Mode)

**CloudNativePG Operator:**
```bash
# Install CloudNativePG operator
kubectl apply --server-side -f \
  https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.25/releases/cnpg-1.25.1.yaml

# Verify installation
kubectl get pods -n cnpg-system
```

**JuiceFS CSI Driver:**
```bash
# Install JuiceFS CSI driver
kubectl apply -f https://raw.githubusercontent.com/juicedata/juicefs-csi-driver/master/deploy/k8s.yaml

# Verify installation
kubectl get pods -n kube-system -l app.kubernetes.io/name=juicefs-csi-driver
```

#### Step 3: Create Image Pull Secret (if using private registry)

```bash
kubectl create namespace odoo-operator

kubectl create secret docker-registry registry.bemade.org \
  --namespace odoo-operator \
  --docker-server=your-registry.com \
  --docker-username=your-username \
  --docker-password=your-password
```

#### Step 4: Install the Odoo Operator

```bash
cd charts/

# Install with default values (CNPG mode)
helm install odoo-operator ./odoo-operator \
  --namespace odoo-operator \
  --create-namespace \
  --set operator.image=your-registry.com/odoo-operator:0.11.0

# Or with custom values file
helm install odoo-operator ./odoo-operator \
  --namespace odoo-operator \
  --create-namespace \
  -f my-values.yaml
```

**Example `my-values.yaml`:**
```yaml
operator:
  image: your-registry.com/odoo-operator:0.11.0

imagePullSecrets:
  - domain: your-registry.com
    username: your-username
    password: your-password

defaults:
  odooImage: odoo:19.0
  storageClass: longhorn
```

#### Step 5: Verify Installation

```bash
# Check operator pod
kubectl get pods -n odoo-operator

# Check operator logs
kubectl logs -n odoo-operator deployment/odoo-operator -f

# Verify CRDs are installed
kubectl get crd | grep bemade.org
```

For detailed configuration options, see the [Helm chart README](charts/odoo-operator/README.md).

### Create Your First Odoo Instance

#### CNPG Mode (Managed PostgreSQL + JuiceFS)

```yaml
apiVersion: bemade.org/v1
kind: OdooInstance
metadata:
  name: my-odoo
  namespace: default
spec:
  image: odoo:19.0

  # Database (CNPG managed)
  database:
    replicas: 3
    storage: 10Gi
    wal:
      s3Bucket: my-odoo-wal
      s3Endpoint: https://s3.eu-central-1.wasabisys.com
      s3CredentialsSecretRef:
        name: s3-credentials
      retentionDays: 30

  # Filestore (JuiceFS backed)
  filestore:
    storageSize: 50Gi
    s3Bucket: my-odoo-filestore
    s3Endpoint: https://s3.eu-central-1.wasabisys.com
    s3CredentialsSecretRef:
      name: s3-credentials

  # Git-sync addons
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

#### Legacy Mode (External PostgreSQL)

```yaml
apiVersion: bemade.org/v1
kind: OdooInstance
metadata:
  name: my-odoo
  namespace: default
spec:
  image: odoo:19.0
  adminPassword: "secure-password"

  # Use external PostgreSQL cluster
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

## Custom Resource Definitions

### OdooInstance

The main resource for defining an Odoo deployment.

#### Spec Fields

| Field             | Type    | Default     | Description                    |
| ----------------- | ------- | ----------- | ------------------------------ |
| `image`           | string  | `odoo:19.0` | Docker image for Odoo          |
| `imagePullSecret` | string  | -           | Secret for private registry    |
| `replicas`        | integer | `1`         | Number of Odoo pods            |
| `workers`         | integer | `4`         | Number of Odoo workers per pod |
| `adminPassword`   | string  | -           | Odoo admin password            |
| `resources`       | object  | 500m/1Gi    | CPU/memory requests and limits |

**Database Configuration (`spec.database`):**

| Field                             | Type    | Default | Description                               |
| --------------------------------- | ------- | ------- | ----------------------------------------- |
| `cluster`                         | string  | -       | Legacy: External PostgreSQL cluster name  |
| `replicas`                        | integer | `3`     | CNPG: PostgreSQL cluster replicas         |
| `storage`                         | string  | `10Gi`  | CNPG: Storage size per replica            |
| `wal.s3Bucket`                    | string  | -       | CNPG: S3 bucket for WAL archiving         |
| `wal.s3Endpoint`                  | string  | -       | CNPG: S3 endpoint URL                     |
| `wal.s3CredentialsSecretRef.name` | string  | -       | CNPG: Secret with `accessKey`/`secretKey` |
| `wal.retentionDays`               | integer | `30`    | CNPG: PITR retention period               |

**Filestore Configuration (`spec.filestore`):**

| Field                         | Type    | Default      | Description                         |
| ----------------------------- | ------- | ------------ | ----------------------------------- |
| `storageSize`                 | string  | `50Gi`       | Storage size                        |
| `storageClass`                | string  | `juicefs-sc` | Kubernetes StorageClass             |
| `s3Bucket`                    | string  | -            | JuiceFS: S3 bucket for data         |
| `s3Endpoint`                  | string  | -            | JuiceFS: S3 endpoint URL            |
| `s3CredentialsSecretRef.name` | string  | -            | JuiceFS: Secret with S3 credentials |
| `trashDays`                   | integer | `30`         | JuiceFS: Deleted file retention     |

**Addons Configuration (`spec.addons[]`):**

| Field               | Type   | Required | Description                  |
| ------------------- | ------ | -------- | ---------------------------- |
| `name`              | string | Yes      | Addon directory name         |
| `repo`              | string | Yes      | Git repository URL           |
| `branch`            | string | No       | Git branch (default: `main`) |
| `tag`               | string | No       | Git tag (overrides branch)   |
| `sshSecretRef.name` | string | No       | Secret with `ssh-privatekey` |

### OdooRestoreJob

Restores from backups or CNPG Point-in-Time Recovery.

```yaml
apiVersion: bemade.org/v1
kind: OdooRestoreJob
metadata:
  name: restore-my-odoo
spec:
  odooInstanceRef:
    name: my-odoo
  source:
    type: pitr  # or "s3" for S3 backup, "odoo" for live instance
    pitr:
      targetTime: "2024-01-15T10:30:00Z"
```

### OdooBackupJob

Creates backups to S3-compatible storage.

```yaml
apiVersion: bemade.org/v1
kind: OdooBackupJob
metadata:
  name: backup-my-odoo
spec:
  odooInstanceRef:
    name: my-odoo
  format: zip  # zip, sql, or dump
  destination:
    bucket: backups
    objectKey: my-odoo/backup.zip
    endpoint: https://s3.wasabisys.com
    s3CredentialsSecretRef:
      name: s3-credentials
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    OdooInstance CR                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
         ┌────────────────────┬────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  CNPG Cluster   │  │ JuiceFS PVC     │  │  Deployment     │
│  (PostgreSQL)   │  │ (Filestore)     │  │  (Odoo + Sync)  │
│                 │  │                 │  │                 │
│  • 3 replicas   │  │  • S3 backend   │  │  • 4 workers    │
│  • WAL to S3    │  │  • CNPG meta    │  │  • git-sync     │
│  • 30-day PITR  │  │  • 30-day trash │  │  • gevent       │
└─────────────────┘  └─────────────────┘  └─────────────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              │
                              ▼
                     ┌─────────────────┐
                     │   S3 (Wasabi)   │
                     │                 │
                     │  • WAL archive  │
                     │  • JuiceFS data │
                     └─────────────────┘
```

## Secrets Management

S3 credentials should be managed via Kubernetes Secrets or OpenBao ExternalSecrets:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: s3-credentials
type: Opaque
stringData:
  accessKey: "your-access-key"
  secretKey: "your-secret-key"
```

## Migration from Legacy Mode

Existing instances using external PostgreSQL can continue to work. The operator detects the mode based on whether `spec.database.wal` is present:

- **With `spec.database.wal`**: CNPG mode (managed PostgreSQL + JuiceFS)
- **Without `spec.database.wal`**: Legacy mode (external DB + regular PVC)

## Development

### Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run the operator
kopf run src/operator.py --verbose
```

### Running Tests

```bash
pytest tests/ -v
```

### Linting Helm Charts

```bash
make lint
```

## License

This project is licensed under the GNU Lesser General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
