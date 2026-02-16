from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.db.models import Sum, Q, Max

from billing.models import MonthlyCharge, PaymentAllocation, PaymentSubmission, PaymentStatus, ChargeStatus
from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce

from decimal import Decimal
from django.db.models import Sum, F, Value, DecimalField
from django.db.models.functions import Coalesce

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class UnitBalance:
    total_charged: Decimal           # suma de MonthlyCharge.amount (no VOID)
    total_applied: Decimal           # suma de allocations aprobadas
    credit_available: Decimal        # pagos aprobados - allocations aprobadas
    balance_due: Decimal             # deuda neta: total_charged - total_applied
    unpaid_months: int               # cantidad de meses con PENDING/PARTIAL
    last_payment_at: Optional[object]  # datetime o None


def get_unit_balance(unit) -> UnitBalance:
    """
    Calcula saldo por unidad basado en:
    - MonthlyCharge (cargos)
    - PaymentSubmission APPROVED (dinero recibido)
    - PaymentAllocation (dinero aplicado a cargos)
    """
    # Total cargos (excluye VOID)
    charged = (
        MonthlyCharge.objects
        .filter(unit=unit)
        .exclude(status=ChargeStatus.VOID)
        .aggregate(s=Sum("amount"))["s"]
        or ZERO
    )

    # Total aplicado (solo allocations de pagos APPROVED)
    applied = (
        PaymentAllocation.objects
        .filter(charge__unit=unit, payment__status=PaymentStatus.APPROVED)
        .aggregate(s=Sum("amount_applied"))["s"]
        or ZERO
    )

    # Total pagos aprobados (dinero recibido)
    paid = (
        PaymentSubmission.objects
        .filter(unit=unit, status=PaymentStatus.APPROVED)
        .aggregate(s=Sum("amount"))["s"]
        or ZERO
    )

    credit = paid - applied
    if credit < ZERO:
        credit = ZERO

    due = charged - applied
    if due < ZERO:
        due = ZERO

    unpaid = (
        MonthlyCharge.objects
        .filter(unit=unit)
        .exclude(status__in=[ChargeStatus.VOID, ChargeStatus.PAID])
        .count()
    )

    last_payment_at = (
        PaymentSubmission.objects
        .filter(unit=unit, status=PaymentStatus.APPROVED)
        .aggregate(m=Max("reviewed_at"))["m"]
    )

    return UnitBalance(
        total_charged=charged,
        total_applied=applied,
        credit_available=credit,
        balance_due=due,
        unpaid_months=unpaid,
        last_payment_at=last_payment_at,
    )


def get_unit_statement(unit, limit_months: int = 24):
    """
    Devuelve detalle por mensualidad con montos pagados y balance por mes.
    Útil para API/estado de cuenta.
    """
    charges = (
        MonthlyCharge.objects
        .filter(unit=unit)
        .exclude(status=ChargeStatus.VOID)
        .order_by("-period")[:limit_months]
    )

    # Map: charge_id -> sum(applied)
    allocs = (
        PaymentAllocation.objects
        .filter(charge__in=charges, payment__status=PaymentStatus.APPROVED)
        .values("charge_id")
        .annotate(applied=Sum("amount_applied"))
    )
    applied_map = {a["charge_id"]: (a["applied"] or ZERO) for a in allocs}

    rows = []
    for c in charges:
        applied = applied_map.get(c.id, ZERO)
        balance = c.amount - applied
        if balance < ZERO:
            balance = ZERO
        rows.append({
            "period": c.period,
            "amount": c.amount,
            "applied": applied,
            "balance": balance,
            "status": c.status,
        })

    return rows


@transaction.atomic
def apply_available_credit_to_charge(charge: MonthlyCharge) -> Decimal:
    """
    Aplica crédito disponible (remanente de pagos APPROVED) a un MonthlyCharge.
    Retorna cuánto se aplicó.
    """
    if charge.status in [ChargeStatus.PAID, ChargeStatus.VOID]:
        return ZERO

    # Bloquea el charge por seguridad
    charge = MonthlyCharge.objects.select_for_update().get(pk=charge.pk)

    # Calcula cuánto falta por pagar de este charge
    balance = charge.balance  # asume que tienes property balance en MonthlyCharge
    if balance <= 0:
        return ZERO

    # Trae pagos APPROVED con remanente > 0 (mismo unit/residential)

    DEC0 = Decimal("0.00")
    DECIMAL = DecimalField(max_digits=12, decimal_places=2)

    payments = (
        PaymentSubmission.objects
        .filter(
            unit=charge.unit,
            residential=charge.residential,
            status=PaymentStatus.APPROVED,
        )
        .annotate(
            allocated=Coalesce(
                Sum("allocations__amount_applied"),
                Value(DEC0, output_field=DECIMAL),
                output_field=DECIMAL,
            ),
            remaining=F("amount") - Coalesce(
                Sum("allocations__amount_applied"),
                Value(DEC0, output_field=DECIMAL),
                output_field=DECIMAL,
            ),
        )
        .filter(remaining__gt=DEC0)
        .order_by("reviewed_at", "submitted_at")
        .select_for_update()
    )

    applied_total = ZERO

    for p in payments:
        if balance <= 0:
            break

        remaining = p.remaining  # viene del annotate
        to_apply = remaining if remaining <= balance else balance

        # crea allocation hacia ESTE charge
        PaymentAllocation.objects.create(
            payment=p,
            charge=charge,
            amount_applied=to_apply,
        )

        applied_total += to_apply
        balance -= to_apply

    # Recalcular estado del charge (PAID/PARTIAL/PENDING)
    from billing.models import recompute_charge_status
    recompute_charge_status(charge)

    return applied_total