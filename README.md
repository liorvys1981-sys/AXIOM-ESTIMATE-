# AXIOM-ESTIMATE

🚘 **AXIOM-ESTIMATE** is the ultra-premium, event-driven SaaS platform for auto estimates, built for scalability, AI integration, and full US market deployment.

---

## 🚀 PREMIUM Stack & Architecture

- **Backend**: tRPC + Hono (TypeScript Fast API framework)
- **Database**: PostgreSQL (11 tables + triggers)
- **Event Bus**: Redis Streams
- **Workers**: Modular AI-ready (Vision, Mechanical, Labor, Logistics)
- **Object Storage**: MinIO/S3
- **Cloud Native**: Docker, Kubernetes, Terraform
- **Observability**: Prometheus + Grafana
- **Security**: Multi-tenant, RBAC, NetworkPolicy

---

## 📂 Repository Structure (Cloud Native, SaaS-ready)

```
axiom-estimate/
├── 📦 APP (Full-Stack)
│   ├── api/                          # Backend tRPC + Hono
│   │   ├── router.ts                 # 3 routers: auth, estimate, payment
│   │   ├── estimate-router.ts        # VIN decode, generate, CIECA export
│   │   ├── payment-router.ts         # Credits system, pricing
│   │   ├── redis-bus.js              # REDIS EVENT BUS (Event-Driven)
│   │   └── middleware.ts             # Auth, RBAC, admin roles
│   ├── workers/                      # 4 AI WORKERS SEPARATE
│   │   ├── runner.js                 # Dispatcher by WORKER_TYPE
│   │   ├── vision.js                 # YOLO damage detection
│   │   ├── mechanical.js             # Hidden damage inference + OBD-II
│   │   ├── labor.js                  # MOTOR labor + ADAS calibration
│   │   └── logistics.js              # CAPA procurement + geolocation
│   ├── db/schema.ts                  # 6-table MySQL (Drizzle ORM)
│   └── src/screens/                  # 4 functional screens (TypeScript/React)
│       ├── ScreenA.tsx               # Ingest + upload + VIN decode
│       ├── ScreenB.tsx               # Supervisor 3-column
│       ├── ScreenC.tsx               # Procurement + HITL + CIECA
│       └── PricingPage.tsx           # Payments + Credits
│
├── 🐳 DOCKER + CONTAINERS
│   ├── Dockerfile                    # Multi-stage production build
│   ├── Dockerfile.worker             # Generic worker image
│   └── docker-compose.yml            # Full stack:
│                                     #   PostgreSQL + Redis + MinIO (S3)
│                                     #   App + 4 Workers + Prometheus + Grafana
│
├── ☸️ KUBERNETES (infra/k8s/)
│   ├── api-gateway.yaml              # Deployment + LoadBalancer
│   ├── workers.yaml                  # 4 Deployments (2 replicas each)
│   ├── hpa.yaml                      # Auto-scaling 2-10 pods per worker
│   └── infra.yaml                    # PostgreSQL StatefulSet + Redis + MinIO
│
├── 🏗️ TERRAFORM (infra/terraform/)
│   ├── main.tf                       # EKS + RDS PostgreSQL + ElastiCache + S3
│   └── variables.tf                  # Region, cluster, passwords
│
├── 📊 OBSERVABILITY (infra/observability/)
│   └── prometheus.yml                # Pod scraping config
│
├── 🔒 SECURITY (infra/security/)
│   └── security.yaml                 # RBAC + NetworkPolicy + PodSecurity
│
├── 🔄 GITOPS (infra/gitops/)
│   └── argocd.yaml                   # ArgoCD app for automated CD
│
├── 📡 EVENT CONTRACTS (infra/contracts/)
│   └── events.yaml                   # Full event bus specs
│
└── 🗄️ DATABASE (infra/init.sql)
    └── 11 tables + triggers + seed data + state rules matrix
```

---

## ⚡ Quickstart (DEV, Docker)

1. `git clone https://github.com/liorvys1981-sys/AXIOM-ESTIMATE-.git`
2. `cd AXIOM-ESTIMATE-`
3. `docker-compose up --build`
4. Access API docs at `http://localhost:3333/docs`  _(after build)_

---

## 🧠 What’s Inside?
- Multi-tenant microservice architecture
- Distributed event bus (Redis Streams/MinIO)
- Multi-modal evidence ingest (media/OBD-II)
- Full audit and legal traceability
- Ready for AI: Separated, scalable AI workers
- Real-time credits and transaction engine
- 100% US-compliant standards (CIECA, GDPR, SOC2)
- Observability and auto-healing workloads

---

## 📄 LICENSE
Distributed under the BSD License. See `LICENSE` for more information.

---

## ✨ SaaS. Ready for Scale. Built for the USA.

For contributions or questions: PRs welcome!
