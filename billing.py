"""
AXIOM Billing Engine
Handles Stripe fee absorption, charge creation, subscription management,
and payment ledger writes — all scoped per multi-tenant shop.
"""

from __future__ import annotations

import logging
import os
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

import stripe
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential

from axiom.db import get_session
from axiom.models import Payment, Shop, Job
from axiom.enums import ServiceType, PaymentStatus

logger = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

# ---------------------------------------------------------------------------
# Service pricing table — single source of truth
# ---------------------------------------------------------------------------

SERVICE_BASE_PRICES: dict[ServiceType, Decimal] = {
    ServiceType.SUBSCRIPTION: Decimal("99.00"),
    ServiceType.ESTIMATE:     Decimal("5.00"),
    ServiceType.CLAIMS:       Decimal("5.00"),
    ServiceType.TOTAL_LOSS:   Decimal("5.00"),
    ServiceType.LIEN:         Decimal("99.00"),
    ServiceType.AUDIT:        Decimal("5.00"),
    ServiceType.CPO:          Decimal("5.00"),
    # gpu_resell is dynamically priced — not in this table
}

# Stripe processing constants
_STRIPE_FIXED_FEE  = Decimal("0.30")
_STRIPE_RATE       = Decimal("0.029")
_STRIPE_RATE_DENOM = Decimal("1") - _STRIPE_RATE   # 0.971


# ---------------------------------------------------------------------------
# Pure fee math — no I/O, fully testable
# ---------------------------------------------------------------------------

def calculate_absorption_amount(target_amount: Decimal) -> Decimal:
    """
    Apply the Stripe fee absorption formula so the platform nets exactly
    `target_amount` after Stripe deducts its fees.

    Formula: charged = (target + 0.30) / (1 - 0.029)

    Args:
        target_amount: The net amount the platform wants to receive.

    Returns:
        The gross amount to charge the customer, rounded to 2 decimal places.
    """
    if target_amount <= Decimal("0"):
        raise ValueError(f"target_amount must be positive, got {target_amount}")

    gross = (target_amount + _STRIPE_FIXED_FEE) / _STRIPE_RATE_DENOM
    return gross.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def dollars_to_cents(amount: Decimal) -> int:
    """Convert a dollar Decimal to Stripe integer cents."""
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_dollars(cents: int) -> Decimal:
    """Convert Stripe integer cents to a dollar Decimal."""
    return Decimal(cents) / Decimal("100")


def build_charge_payload(
    service_type: ServiceType,
    stripe_customer_id: str,
    shop_id: UUID,
    job_id: UUID | None = None,
    override_target: Decimal | None = None,
) -> dict[str, Any]:
    """
    Build the complete Stripe charge payload for a given service, including
    the fee-absorbed amount and idempotency metadata.

    Args:
        service_type:       Which office / service is being billed.
        stripe_customer_id: Stripe customer ID for the tenant shop.
        shop_id:            Internal shop UUID (embedded in metadata).
        job_id:             Optional job UUID for traceability.
        override_target:    Override the default service price (e.g. gpu_resell).

    Returns:
        Dict ready to pass to stripe.PaymentIntent.create or stripe.Charge.create.
    """
    if override_target is not None:
        target = override_target
    elif service_type in SERVICE_BASE_PRICES:
        target = SERVICE_BASE_PRICES[service_type]
    else:
        raise ValueError(
            f"No base price for service '{service_type}' and no override_target provided."
        )

    gross = calculate_absorption_amount(target)
    amount_cents = dollars_to_cents(gross)

    metadata: dict[str, str] = {
        "shop_id":      str(shop_id),
        "service_type": service_type.value,
        "target_net":   str(target),
        "gross_charged": str(gross),
    }
    if job_id:
        metadata["job_id"] = str(job_id)

    return {
        "amount":      amount_cents,
        "currency":    "usd",
        "customer":    stripe_customer_id,
        "description": f"AXIOM | {service_type.value.upper()} | Shop {shop_id}",
        "metadata":    metadata,
    }


# ---------------------------------------------------------------------------
# Stripe API wrappers (retried on transient network errors)
# ---------------------------------------------------------------------------

class BillingEngine:
    """
    Stateless billing engine. All methods are classmethods so callers do not
    need to instantiate; the DB session is injected per call for testability.
    """

    # ------------------------------------------------------------------
    # Core charge
    # ------------------------------------------------------------------

    @classmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def collect_service_fee(
        cls,
        *,
        session: AsyncSession,
        shop: Shop,
        service_type: ServiceType,
        job_id: UUID | None = None,
        override_target: Decimal | None = None,
    ) -> Payment:
        """
        Charge the shop's saved Stripe customer for one service unit.
        Persists a Payment record regardless of Stripe outcome.

        Returns the Payment ORM object (check .payment_status for result).
        """
        if not shop.stripe_customer_id:
            raise ValueError(f"Shop {shop.id} has no stripe_customer_id — cannot charge.")

        payload = build_charge_payload(
            service_type=service_type,
            stripe_customer_id=shop.stripe_customer_id,
            shop_id=shop.id,
            job_id=job_id,
            override_target=override_target,
        )

        target_net = override_target or SERVICE_BASE_PRICES.get(service_type, Decimal("0"))
        payment = Payment(
            shop_id=shop.id,
            job_id=job_id,
            service_type=service_type,
            target_amount_cents=dollars_to_cents(target_net),
            charged_amount_cents=payload["amount"],
            payment_status=PaymentStatus.PENDING,
        )
        session.add(payment)
        await session.flush()   # get payment.id before Stripe call

        try:
            intent = stripe.PaymentIntent.create(
                **payload,
                confirm=True,
                payment_method=shop.stripe_payment_method,
                off_session=True,
                idempotency_key=f"axiom-{payment.id}",
            )
            payment.stripe_payment_intent = intent["id"]
            payment.stripe_charge_id = (
                intent.get("latest_charge") or intent.get("charges", {})
                .get("data", [{}])[0].get("id")
            )
            payment.payment_status = PaymentStatus.SUCCEEDED
            logger.info(
                "charge_succeeded",
                extra={
                    "shop_id": str(shop.id),
                    "service": service_type.value,
                    "amount_cents": payload["amount"],
                    "intent_id": intent["id"],
                },
            )
        except stripe.error.CardError as exc:
            payment.payment_status = PaymentStatus.FAILED
            payment.failure_code    = exc.code
            payment.failure_message = exc.user_message
            logger.warning(
                "charge_card_error",
                extra={"shop_id": str(shop.id), "code": exc.code, "message": exc.user_message},
            )
        except stripe.error.StripeError as exc:
            payment.payment_status = PaymentStatus.FAILED
            payment.failure_message = str(exc)
            logger.error(
                "charge_stripe_error",
                extra={"shop_id": str(shop.id), "error": str(exc)},
                exc_info=True,
            )
            raise   # Triggers tenacity retry on transient errors

        await session.commit()
        return payment

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    @classmethod
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    async def create_subscription(
        cls,
        *,
        session: AsyncSession,
        shop: Shop,
        price_id: str,
    ) -> dict[str, Any]:
        """
        Create a Stripe subscription for the platform base access tier.
        The price_id should be pre-configured in the Stripe dashboard with
        the fee-absorbed amount.

        Returns the raw Stripe Subscription object.
        """
        if not shop.stripe_customer_id:
            raise ValueError(f"Shop {shop.id} has no stripe_customer_id.")

        subscription = stripe.Subscription.create(
            customer=shop.stripe_customer_id,
            items=[{"price": price_id}],
            payment_behavior="default_incomplete",
            expand=["latest_invoice.payment_intent"],
            metadata={"shop_id": str(shop.id)},
        )

        shop.subscription_stripe_id = subscription["id"]
        shop.subscription_status = subscription["status"]
        await session.commit()

        logger.info(
            "subscription_created",
            extra={"shop_id": str(shop.id), "subscription_id": subscription["id"]},
        )
        return subscription

    @classmethod
    async def cancel_subscription(
        cls,
        *,
        session: AsyncSession,
        shop: Shop,
        at_period_end: bool = True,
    ) -> dict[str, Any]:
        """
        Cancel a shop's Stripe subscription immediately or at period end.
        """
        if not shop.subscription_stripe_id:
            raise ValueError(f"Shop {shop.id} has no active subscription to cancel.")

        if at_period_end:
            result = stripe.Subscription.modify(
                shop.subscription_stripe_id,
                cancel_at_period_end=True,
            )
        else:
            result = stripe.Subscription.cancel(shop.subscription_stripe_id)

        shop.subscription_status = result["status"]
        await session.commit()

        logger.info(
            "subscription_canceled",
            extra={
                "shop_id": str(shop.id),
                "sub_id": shop.subscription_stripe_id,
                "at_period_end": at_period_end,
            },
        )
        return result

    # ------------------------------------------------------------------
    # Stripe customer provisioning
    # ------------------------------------------------------------------

    @classmethod
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    async def ensure_stripe_customer(
        cls,
        *,
        session: AsyncSession,
        shop: Shop,
    ) -> str:
        """
        Idempotently create a Stripe customer for a shop.
        If one already exists (stripe_customer_id is set), returns it.

        Returns the Stripe customer ID string.
        """
        if shop.stripe_customer_id:
            return shop.stripe_customer_id

        customer = stripe.Customer.create(
            email=shop.owner_email,
            name=shop.name,
            metadata={
                "shop_id":   str(shop.id),
                "shop_slug": shop.slug,
            },
        )
        shop.stripe_customer_id = customer["id"]
        await session.commit()

        logger.info(
            "stripe_customer_created",
            extra={"shop_id": str(shop.id), "customer_id": customer["id"]},
        )
        return customer["id"]

    # ------------------------------------------------------------------
    # Webhook processing
    # ------------------------------------------------------------------

    @classmethod
    async def process_webhook(
        cls,
        *,
        session: AsyncSession,
        payload: bytes,
        sig_header: str,
        webhook_secret: str,
    ) -> dict[str, Any]:
        """
        Verify and process a Stripe webhook event. Updates Payment and Shop
        records to stay in sync with Stripe's source of truth.

        Returns a dict with {"event_type": str, "processed": bool}.
        """
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except stripe.error.SignatureVerificationError as exc:
            logger.warning("webhook_signature_invalid", extra={"error": str(exc)})
            raise ValueError("Invalid Stripe webhook signature") from exc

        event_type: str = event["type"]
        data_object: dict[str, Any] = event["data"]["object"]

        handlers = {
            "payment_intent.succeeded":               cls._handle_payment_succeeded,
            "payment_intent.payment_failed":          cls._handle_payment_failed,
            "customer.subscription.updated":          cls._handle_subscription_updated,
            "customer.subscription.deleted":          cls._handle_subscription_deleted,
            "invoice.payment_action_required":        cls._handle_invoice_action_required,
        }

        handler = handlers.get(event_type)
        if handler:
            await handler(session=session, data=data_object)
            logger.info("webhook_processed", extra={"event_type": event_type})
            return {"event_type": event_type, "processed": True}

        logger.debug("webhook_unhandled", extra={"event_type": event_type})
        return {"event_type": event_type, "processed": False}

    # ------------------------------------------------------------------
    # Private webhook sub-handlers
    # ------------------------------------------------------------------

    @classmethod
    async def _handle_payment_succeeded(
        cls, *, session: AsyncSession, data: dict[str, Any]
    ) -> None:
        from sqlalchemy import select
        intent_id = data.get("id")
        result = await session.execute(
            select(Payment).where(Payment.stripe_payment_intent == intent_id)
        )
        payment = result.scalar_one_or_none()
        if payment:
            payment.payment_status = PaymentStatus.SUCCEEDED
            from datetime import datetime, timezone
            payment.paid_at = datetime.now(timezone.utc)
            await session.commit()

    @classmethod
    async def _handle_payment_failed(
        cls, *, session: AsyncSession, data: dict[str, Any]
    ) -> None:
        from sqlalchemy import select
        intent_id = data.get("id")
        result = await session.execute(
            select(Payment).where(Payment.stripe_payment_intent == intent_id)
        )
        payment = result.scalar_one_or_none()
        if payment:
            payment.payment_status = PaymentStatus.FAILED
            last_error = data.get("last_payment_error", {})
            payment.failure_code    = last_error.get("code")
            payment.failure_message = last_error.get("message")
            await session.commit()

    @classmethod
    async def _handle_subscription_updated(
        cls, *, session: AsyncSession, data: dict[str, Any]
    ) -> None:
        from sqlalchemy import select
        sub_id = data.get("id")
        result = await session.execute(
            select(Shop).where(Shop.subscription_stripe_id == sub_id)
        )
        shop = result.scalar_one_or_none()
        if shop:
            shop.subscription_status = data.get("status", shop.subscription_status)
            await session.commit()

    @classmethod
    async def _handle_subscription_deleted(
        cls, *, session: AsyncSession, data: dict[str, Any]
    ) -> None:
        await cls._handle_subscription_updated(session=session, data=data)

    @classmethod
    async def _handle_invoice_action_required(
        cls, *, session: AsyncSession, data: dict[str, Any]
    ) -> None:
        logger.warning(
            "invoice_action_required",
            extra={"invoice_id": data.get("id"), "customer": data.get("customer")},
        )

    # ------------------------------------------------------------------
    # VRAM brokerage — dynamic pricing
    # ------------------------------------------------------------------

    @classmethod
    async def calculate_vram_payout(
        cls,
        *,
        leased_bytes: int,
        lease_seconds: float,
        rate_per_gb_hour: Decimal,
    ) -> Decimal:
        """
        Calculate the payout owed to a shop for contributing idle VRAM.

        Args:
            leased_bytes:      Number of bytes leased to the compute market.
            lease_seconds:     Duration of the lease in seconds.
            rate_per_gb_hour:  Agreed rate in USD per GB-hour.

        Returns:
            Payout amount in USD, rounded to 6 decimal places.
        """
        gb = Decimal(leased_bytes) / Decimal(1024 ** 3)
        hours = Decimal(lease_seconds) / Decimal(3600)
        payout = gb * hours * rate_per_gb_hour
        return payout.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
