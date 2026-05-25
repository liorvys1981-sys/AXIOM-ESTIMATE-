-- ============================================================
-- AXIOM ESTIMATE — PostgreSQL Multi-Tenant Schema
-- Version: 1.1.0
-- Engine: PostgreSQL 15+
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- For fuzzy VIN/name search

-- ============================================================
-- ENUMERATIONS
-- ============================================================

CREATE TYPE subscription_status_enum AS ENUM (
    'trialing',
    'active',
    'past_due',
    'canceled',
    'unpaid',
    'paused'
);

CREATE TYPE service_type_enum AS ENUM (
    'subscription',   -- Base SaaS access
    'estimate',       -- Office 1: AI Automotive Damage Estimator
    'claims',         -- Office 2: Insurance Claims Desk
    'total_loss',     -- Office 3: Total Loss Mathematical Actuary
    'lien',           -- Office 4: Mechanic's Lien & Legal Bureau
    'audit',          -- Office 5: Repair Order Invoice Audit
    'cpo',            -- Office 6: Certified Pre-Owned Inspection
    'gpu_resell'      -- Office 7: Idle VRAM Brokerage
);

CREATE TYPE job_status_enum AS ENUM (
    'pending',
    'queued',
    'processing',
    'completed',
    'failed',
    'retrying',
    'canceled'
);

CREATE TYPE lien_status_enum AS ENUM (
    'draft',
    'filed',
    'served',
    'satisfied',
    'disputed',
    'expired',
    'withdrawn'
);

CREATE TYPE payment_status_enum AS ENUM (
    'pending',
    'succeeded',
    'failed',
    'refunded',
    'disputed'
);

CREATE TYPE cpo_badge_status_enum AS ENUM (
    'pending',
    'passed',
    'failed',
    'conditional',
    'expired'
);

-- ============================================================
-- TENANT / SHOP LAYER
-- ============================================================

CREATE TABLE IF NOT EXISTS shops (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Identity
    name                    VARCHAR(255)    NOT NULL,
    slug                    VARCHAR(100)    UNIQUE NOT NULL,   -- URL-safe shop identifier
    owner_email             VARCHAR(320)    UNIQUE NOT NULL,
    owner_name              VARCHAR(255),
    phone                   VARCHAR(30),
    -- Address (used for lien filings and DMV correspondence)
    address_line1           VARCHAR(255),
    address_line2           VARCHAR(255),
    city                    VARCHAR(100),
    state                   CHAR(2)         DEFAULT 'FL',      -- Defaults to Florida jurisdiction
    zip                     VARCHAR(10),
    -- License & legal identity
    dealer_license_number   VARCHAR(100),
    ein                     VARCHAR(20),                        -- Employer Identification Number
    dmv_agency_code         VARCHAR(50),                        -- FL-specific DMV code
    -- Stripe integration
    stripe_customer_id      VARCHAR(100)    UNIQUE,
    stripe_payment_method   VARCHAR(100),                       -- Default saved payment method
    -- Subscription state
    subscription_status     subscription_status_enum NOT NULL DEFAULT 'trialing',
    subscription_stripe_id  VARCHAR(100)    UNIQUE,
    trial_ends_at           TIMESTAMPTZ,
    subscription_ends_at    TIMESTAMPTZ,
    -- Active service offices (bitmask via array for O(1) checks)
    active_services         service_type_enum[] NOT NULL DEFAULT ARRAY['subscription']::service_type_enum[],
    -- Metadata
    is_active               BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_shops_owner_email     ON shops (owner_email);
CREATE INDEX idx_shops_stripe_cid      ON shops (stripe_customer_id);
CREATE INDEX idx_shops_slug            ON shops (slug);
CREATE INDEX idx_shops_subscription    ON shops (subscription_status);
CREATE INDEX idx_shops_state           ON shops (state);

-- ============================================================
-- SHOP USERS (multi-user per tenant)
-- ============================================================

CREATE TABLE IF NOT EXISTS shop_users (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id         UUID        NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    email           VARCHAR(320) NOT NULL,
    full_name       VARCHAR(255),
    role            VARCHAR(50) NOT NULL DEFAULT 'technician', -- owner, manager, technician, viewer
    hashed_password VARCHAR(255) NOT NULL,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_shop_users_email_shop ON shop_users (email, shop_id);
CREATE INDEX idx_shop_users_shop_id ON shop_users (shop_id);

-- ============================================================
-- VEHICLES (shared reference across offices)
-- ============================================================

CREATE TABLE IF NOT EXISTS vehicles (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id         UUID        NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    vin             CHAR(17)    NOT NULL,
    year            SMALLINT,
    make            VARCHAR(100),
    model           VARCHAR(100),
    trim            VARCHAR(100),
    color           VARCHAR(60),
    odometer        INTEGER,
    license_plate   VARCHAR(20),
    license_state   CHAR(2),
    owner_name      VARCHAR(255),
    owner_address   TEXT,
    owner_email     VARCHAR(320),
    owner_phone     VARCHAR(30),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_vehicles_vin_shop ON vehicles (vin, shop_id);
CREATE INDEX idx_vehicles_shop_id ON vehicles (shop_id);
CREATE INDEX idx_vehicles_vin     ON vehicles USING gin (vin gin_trgm_ops);

-- ============================================================
-- JOBS — Core unit-of-work across all 7 offices
-- ============================================================

CREATE TABLE IF NOT EXISTS jobs (
    id                  UUID                PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id             UUID                NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    vehicle_id          UUID                REFERENCES vehicles(id) ON DELETE SET NULL,
    submitted_by        UUID                REFERENCES shop_users(id) ON DELETE SET NULL,
    -- Routing
    service_type        service_type_enum   NOT NULL,
    -- Celery task tracking
    celery_task_id      VARCHAR(255)        UNIQUE,
    -- State machine
    status              job_status_enum     NOT NULL DEFAULT 'pending',
    retries_count       SMALLINT            NOT NULL DEFAULT 0,
    max_retries         SMALLINT            NOT NULL DEFAULT 3,
    error_message       TEXT,
    -- Input payload (raw request data, carrier info, etc.)
    input_payload       JSONB               NOT NULL DEFAULT '{}',
    -- Output payload (generated estimates, flags, verdicts, badges)
    output_payload      JSONB               DEFAULT '{}',
    -- GPU / compute telemetry
    vram_usage_bytes    BIGINT              NOT NULL DEFAULT 0,
    vram_peak_bytes     BIGINT              NOT NULL DEFAULT 0,
    execution_time_ms   INTEGER             NOT NULL DEFAULT 0,
    -- Billing linkage
    billed              BOOLEAN             NOT NULL DEFAULT FALSE,
    -- Timestamps
    queued_at           TIMESTAMPTZ,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ         NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_jobs_shop_id      ON jobs (shop_id);
CREATE INDEX idx_jobs_vehicle_id   ON jobs (vehicle_id);
CREATE INDEX idx_jobs_service_type ON jobs (service_type);
CREATE INDEX idx_jobs_status       ON jobs (status);
CREATE INDEX idx_jobs_celery       ON jobs (celery_task_id);
CREATE INDEX idx_jobs_created      ON jobs (created_at DESC);
CREATE INDEX idx_jobs_shop_service ON jobs (shop_id, service_type, status);

-- ============================================================
-- API COST LEDGER — per-job real-time compute cost tracking
-- ============================================================

CREATE TABLE IF NOT EXISTS api_costs (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              UUID        NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    shop_id             UUID        NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    -- GPU compute breakdown
    gpu_compute_cost    NUMERIC(12, 6) NOT NULL DEFAULT 0,
    gpu_seconds         NUMERIC(10, 3) NOT NULL DEFAULT 0,
    vram_gb_seconds     NUMERIC(10, 4) NOT NULL DEFAULT 0,
    -- LLM token breakdown
    llm_tokens_input    INTEGER     NOT NULL DEFAULT 0,
    llm_tokens_output   INTEGER     NOT NULL DEFAULT 0,
    llm_tokens_cost     NUMERIC(12, 6) NOT NULL DEFAULT 0,
    -- Vision / OCR pass-through
    vision_calls        INTEGER     NOT NULL DEFAULT 0,
    vision_cost         NUMERIC(12, 6) NOT NULL DEFAULT 0,
    -- Rollup
    total_platform_cost NUMERIC(12, 6) NOT NULL DEFAULT 0,
    -- Billing status
    billed_to_client    BOOLEAN     NOT NULL DEFAULT FALSE,
    invoice_id          VARCHAR(100),
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_api_costs_job_id  ON api_costs (job_id);
CREATE INDEX idx_api_costs_shop_id ON api_costs (shop_id);
CREATE INDEX idx_api_costs_billed  ON api_costs (billed_to_client, shop_id);

-- ============================================================
-- OFFICE 1 — AI Automotive Damage Estimates
-- ============================================================

CREATE TABLE IF NOT EXISTS damage_estimates (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              UUID        NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    shop_id             UUID        NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    vehicle_id          UUID        REFERENCES vehicles(id) ON DELETE SET NULL,
    -- Source images
    uploaded_image_urls TEXT[]      NOT NULL DEFAULT '{}',
    -- AI-generated findings
    damage_zones        JSONB       NOT NULL DEFAULT '[]',  -- [{zone, severity, estimated_hours, estimated_parts_cost}]
    total_parts_cost    NUMERIC(12, 2) NOT NULL DEFAULT 0,
    total_labor_cost    NUMERIC(12, 2) NOT NULL DEFAULT 0,
    total_estimate      NUMERIC(12, 2) NOT NULL DEFAULT 0,
    -- PDF output
    estimate_pdf_url    TEXT,
    -- Adjuster notes
    adjuster_notes      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_damage_estimates_shop_id ON damage_estimates (shop_id);
CREATE INDEX idx_damage_estimates_job_id  ON damage_estimates (job_id);

-- ============================================================
-- OFFICE 2 — Insurance Claims Desk
-- ============================================================

CREATE TABLE IF NOT EXISTS insurance_claims (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id                  UUID        NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    shop_id                 UUID        NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    vehicle_id              UUID        REFERENCES vehicles(id) ON DELETE SET NULL,
    -- Claim identity
    claim_number            VARCHAR(100) UNIQUE,
    policy_number           VARCHAR(100),
    carrier_name            VARCHAR(255),
    carrier_code            VARCHAR(50),                    -- Normalized carrier code (e.g. "ALLSTATE")
    -- Estimate source linkage
    damage_estimate_id      UUID        REFERENCES damage_estimates(id) ON DELETE SET NULL,
    -- CCC ONE / Mitchell compliance payload (auto-generated)
    compliance_payload      JSONB       NOT NULL DEFAULT '{}',
    carrier_guideline_flags JSONB       NOT NULL DEFAULT '[]',  -- [{code, description, resolved}]
    -- Negotiation state
    initial_offer_amount    NUMERIC(12, 2),
    final_settled_amount    NUMERIC(12, 2),
    supplement_count        SMALLINT    NOT NULL DEFAULT 0,
    -- Status
    claim_status            VARCHAR(50) NOT NULL DEFAULT 'submitted',
    stripe_invoice_id       VARCHAR(100),
    -- Timestamps
    submitted_at            TIMESTAMPTZ,
    settled_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_claims_shop_id     ON insurance_claims (shop_id);
CREATE INDEX idx_claims_carrier     ON insurance_claims (carrier_code);
CREATE INDEX idx_claims_status      ON insurance_claims (claim_status);
CREATE INDEX idx_claims_vehicle     ON insurance_claims (vehicle_id);

-- ============================================================
-- OFFICE 3 — Total Loss Mathematical Actuary
-- ============================================================

CREATE TABLE IF NOT EXISTS total_loss_assessments (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id                  UUID        NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    shop_id                 UUID        NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    vehicle_id              UUID        NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    -- Market data snapshot used at time of calculation
    salvage_auction_data    JSONB       NOT NULL DEFAULT '[]',  -- [{source, auction_id, price, date}]
    market_value_sources    JSONB       NOT NULL DEFAULT '[]',  -- [{source, value, date}]
    -- Calculated values
    actual_cash_value       NUMERIC(12, 2) NOT NULL,
    repair_cost             NUMERIC(12, 2) NOT NULL,
    salvage_value           NUMERIC(12, 2) NOT NULL DEFAULT 0,
    loss_ratio              NUMERIC(6, 4)  NOT NULL,     -- repair_cost / actual_cash_value
    total_loss_threshold    NUMERIC(6, 4)  NOT NULL DEFAULT 0.75,  -- FL default 75%
    -- Verdict
    is_total_loss           BOOLEAN     NOT NULL,
    determination_vector    TEXT,                                -- Narrative explanation of verdict
    -- Review
    reviewed_by             UUID        REFERENCES shop_users(id) ON DELETE SET NULL,
    reviewed_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tl_shop_id    ON total_loss_assessments (shop_id);
CREATE INDEX idx_tl_vehicle_id ON total_loss_assessments (vehicle_id);

-- ============================================================
-- OFFICE 4 — Mechanic's Lien & Legal Bureau (Florida Statutes §713)
-- ============================================================

CREATE TABLE IF NOT EXISTS liens (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id                 UUID            NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    job_id                  UUID            REFERENCES jobs(id) ON DELETE SET NULL,
    vehicle_id              UUID            NOT NULL REFERENCES vehicles(id) ON DELETE RESTRICT,
    -- Debtor information (owner of vehicle)
    debtor_name             VARCHAR(255)    NOT NULL,
    debtor_address          TEXT            NOT NULL,
    debtor_city             VARCHAR(100),
    debtor_state            CHAR(2),
    debtor_zip              VARCHAR(10),
    -- Lienholder (bank / finance company)
    lienholder_name         VARCHAR(255),
    lienholder_address      TEXT,
    lienholder_city         VARCHAR(100),
    lienholder_state        CHAR(2),
    lienholder_zip          VARCHAR(10),
    lienholder_account_ref  VARCHAR(100),    -- Last 4 of account for verification
    -- Lien substance
    amount_owed             NUMERIC(12, 2)  NOT NULL CHECK (amount_owed > 0),
    services_rendered       TEXT            NOT NULL,
    service_date_start      DATE            NOT NULL,
    service_date_end        DATE            NOT NULL,
    -- FL Statute §713 compliance fields
    notice_of_lien_date     DATE,
    certified_mail_tracking VARCHAR(100),    -- USPS tracking for certified mail
    dmv_filing_number       VARCHAR(100),    -- FL DMV confirmation number
    dmv_filed_at            TIMESTAMPTZ,
    -- State machine
    lien_status             lien_status_enum NOT NULL DEFAULT 'draft',
    -- Legal documents (S3 URLs)
    lien_document_url       TEXT,
    dmv_form_url            TEXT,           -- FL Form HSMV 82085 or equivalent
    -- Resolution
    paid_amount             NUMERIC(12, 2),
    paid_at                 TIMESTAMPTZ,
    satisfaction_doc_url    TEXT,
    -- Metadata
    created_by              UUID            REFERENCES shop_users(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_liens_shop_id    ON liens (shop_id);
CREATE INDEX idx_liens_vehicle_id ON liens (vehicle_id);
CREATE INDEX idx_liens_status     ON liens (lien_status);
CREATE INDEX idx_liens_vin        ON liens USING gin (
    (SELECT vin FROM vehicles WHERE vehicles.id = liens.vehicle_id) gin_trgm_ops
);

-- ============================================================
-- OFFICE 5 — Forensic Invoice & Repair Order Audit
-- ============================================================

CREATE TABLE IF NOT EXISTS invoice_audits (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id                  UUID        NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    shop_id                 UUID        NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    vehicle_id              UUID        REFERENCES vehicles(id) ON DELETE SET NULL,
    -- Source documents
    raw_invoice_url         TEXT,
    ocr_raw_text            TEXT,
    -- Line-item analysis (each part / labor line)
    line_items              JSONB       NOT NULL DEFAULT '[]',
    -- [{
    --   line_no, description, qty, unit_price, billed_price,
    --   catalog_price, markup_pct, flag_type (none|inflated|unperformed|duplicate),
    --   flag_notes
    -- }]
    -- Aggregated findings
    total_billed            NUMERIC(12, 2) NOT NULL DEFAULT 0,
    total_catalog_value     NUMERIC(12, 2) NOT NULL DEFAULT 0,
    total_overcharge        NUMERIC(12, 2) NOT NULL DEFAULT 0,
    inflation_pct           NUMERIC(6, 4)  NOT NULL DEFAULT 0,
    unperformed_items_count INTEGER        NOT NULL DEFAULT 0,
    -- Verdict
    audit_passed            BOOLEAN,
    profit_leak_amount      NUMERIC(12, 2) NOT NULL DEFAULT 0,
    auditor_notes           TEXT,
    -- Review
    reviewed_by             UUID        REFERENCES shop_users(id) ON DELETE SET NULL,
    reviewed_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audits_shop_id ON invoice_audits (shop_id);
CREATE INDEX idx_audits_job_id  ON invoice_audits (job_id);

-- ============================================================
-- OFFICE 6 — Certified Pre-Owned Inspection
-- ============================================================

CREATE TABLE IF NOT EXISTS cpo_inspections (
    id                      UUID                PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id                  UUID                NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    shop_id                 UUID                NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    vehicle_id              UUID                NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    -- OBD-II data
    obd_data_raw            JSONB               NOT NULL DEFAULT '{}',
    obd_dtc_codes           TEXT[]              NOT NULL DEFAULT '{}',  -- Diagnostic Trouble Codes
    -- Manufacturer checklist results
    manufacturer            VARCHAR(100),
    checklist_template_id   VARCHAR(100),       -- e.g. "BMW_CPO_v3", "TOYOTA_CPO_v7"
    checklist_results       JSONB               NOT NULL DEFAULT '[]',
    -- [{
    --   check_id, category, description,
    --   result (pass|fail|advisory), notes
    -- }]
    checks_total            INTEGER             NOT NULL DEFAULT 0,
    checks_passed           INTEGER             NOT NULL DEFAULT 0,
    checks_failed           INTEGER             NOT NULL DEFAULT 0,
    checks_advisory         INTEGER             NOT NULL DEFAULT 0,
    -- CPO badge
    badge_status            cpo_badge_status_enum NOT NULL DEFAULT 'pending',
    badge_issued_at         TIMESTAMPTZ,
    badge_expires_at        TIMESTAMPTZ,
    badge_token             VARCHAR(255)        UNIQUE,   -- Encrypted anchor token
    badge_pdf_url           TEXT,
    -- Inspector
    inspected_by            UUID                REFERENCES shop_users(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cpo_shop_id    ON cpo_inspections (shop_id);
CREATE INDEX idx_cpo_vehicle_id ON cpo_inspections (vehicle_id);
CREATE INDEX idx_cpo_badge      ON cpo_inspections (badge_status);

-- ============================================================
-- OFFICE 7 — Idle VRAM Brokerage
-- ============================================================

CREATE TABLE IF NOT EXISTS vram_leases (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id                 UUID        NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    -- Hardware node
    node_hostname           VARCHAR(255) NOT NULL,
    gpu_model               VARCHAR(100),
    total_vram_bytes        BIGINT      NOT NULL,
    -- Lease window
    leased_bytes            BIGINT      NOT NULL,
    lease_rate_per_gb_hour  NUMERIC(10, 6) NOT NULL,
    lease_started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_ended_at          TIMESTAMPTZ,
    -- Billing
    billed_gb_hours         NUMERIC(10, 4),
    payout_amount           NUMERIC(12, 6),
    payout_status           VARCHAR(50) NOT NULL DEFAULT 'pending',  -- pending, processing, paid, failed
    stripe_transfer_id      VARCHAR(100),
    payout_settled_at       TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_vram_shop_id ON vram_leases (shop_id);
CREATE INDEX idx_vram_node    ON vram_leases (node_hostname);
CREATE INDEX idx_vram_payout  ON vram_leases (payout_status);

-- ============================================================
-- BILLING — Stripe payment events ledger
-- ============================================================

CREATE TABLE IF NOT EXISTS payments (
    id                  UUID                PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id             UUID                NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    job_id              UUID                REFERENCES jobs(id) ON DELETE SET NULL,
    -- Stripe fields
    stripe_charge_id    VARCHAR(100)        UNIQUE,
    stripe_invoice_id   VARCHAR(100)        UNIQUE,
    stripe_payment_intent VARCHAR(100)      UNIQUE,
    -- Amounts (all in cents, matching Stripe)
    target_amount_cents INTEGER             NOT NULL,  -- Platform's desired net
    charged_amount_cents INTEGER            NOT NULL,  -- After fee absorption formula
    currency            CHAR(3)             NOT NULL DEFAULT 'usd',
    service_type        service_type_enum   NOT NULL,
    -- Status
    payment_status      payment_status_enum NOT NULL DEFAULT 'pending',
    failure_code        VARCHAR(100),
    failure_message     TEXT,
    -- Timestamps
    paid_at             TIMESTAMPTZ,
    refunded_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_payments_shop_id   ON payments (shop_id);
CREATE INDEX idx_payments_status    ON payments (payment_status);
CREATE INDEX idx_payments_job_id    ON payments (job_id);
CREATE INDEX idx_payments_created   ON payments (created_at DESC);

-- ============================================================
-- AUDIT LOG — immutable event trail for all tenant mutations
-- ============================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL   PRIMARY KEY,
    shop_id         UUID        REFERENCES shops(id) ON DELETE SET NULL,
    user_id         UUID        REFERENCES shop_users(id) ON DELETE SET NULL,
    -- Event
    event_type      VARCHAR(100) NOT NULL,   -- e.g. "lien.filed", "job.completed", "payment.succeeded"
    entity_type     VARCHAR(100),            -- e.g. "lien", "job", "payment"
    entity_id       UUID,
    -- Payload snapshot
    old_data        JSONB,
    new_data        JSONB,
    -- Request context
    ip_address      INET,
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_shop_id     ON audit_log (shop_id);
CREATE INDEX idx_audit_event_type  ON audit_log (event_type);
CREATE INDEX idx_audit_entity      ON audit_log (entity_type, entity_id);
CREATE INDEX idx_audit_created     ON audit_log (created_at DESC);

-- ============================================================
-- AUTOMATED TRIGGERS — updated_at maintenance
-- ============================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_shops_updated_at
    BEFORE UPDATE ON shops
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_liens_updated_at
    BEFORE UPDATE ON liens
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- VIEWS — Operational dashboards
-- ============================================================

-- Per-shop job summary (used by portal dashboard)
CREATE OR REPLACE VIEW v_shop_job_summary AS
SELECT
    j.shop_id,
    j.service_type,
    COUNT(*) FILTER (WHERE j.status = 'completed')  AS completed_count,
    COUNT(*) FILTER (WHERE j.status = 'failed')     AS failed_count,
    COUNT(*) FILTER (WHERE j.status IN ('pending','queued','processing')) AS active_count,
    AVG(j.execution_time_ms) FILTER (WHERE j.status = 'completed')       AS avg_exec_ms,
    SUM(ac.total_platform_cost)                                           AS total_platform_cost,
    MAX(j.created_at)                                                     AS last_job_at
FROM jobs j
LEFT JOIN api_costs ac ON ac.job_id = j.id
GROUP BY j.shop_id, j.service_type;

-- Open liens (active legal matters)
CREATE OR REPLACE VIEW v_open_liens AS
SELECT
    l.*,
    s.name  AS shop_name,
    s.state AS shop_state,
    v.vin,
    v.year,
    v.make,
    v.model
FROM liens l
JOIN shops    s ON s.id = l.shop_id
JOIN vehicles v ON v.id = l.vehicle_id
WHERE l.lien_status NOT IN ('satisfied', 'expired', 'withdrawn');

-- VRAM utilisation per node
CREATE OR REPLACE VIEW v_vram_utilisation AS
SELECT
    node_hostname,
    gpu_model,
    total_vram_bytes,
    SUM(leased_bytes) AS leased_bytes,
    ROUND(SUM(leased_bytes)::NUMERIC / NULLIF(total_vram_bytes, 0) * 100, 2) AS utilisation_pct,
    COUNT(*) AS active_leases
FROM vram_leases
WHERE lease_ended_at IS NULL
GROUP BY node_hostname, gpu_model, total_vram_bytes;
