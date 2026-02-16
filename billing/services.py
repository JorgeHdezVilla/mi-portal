from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.db.models import Sum, Q, Max

from billing.models import MonthlyCharge, PaymentAllocation, PaymentSubmission, PaymentStatus, ChargeStatus


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