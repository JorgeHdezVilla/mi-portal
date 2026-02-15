import uuid
from decimal import Decimal
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.text import get_valid_filename


class UUIDModel(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    class Meta:
        abstract = True


class FeeSchedule(UUIDModel):
    """
    Historial de cuota mensual por residencial.
    La cuota vigente para una fecha es la más reciente con effective_from <= fecha.
    """
    residential = models.ForeignKey(
        "core.Residential",
        on_delete=models.PROTECT,
        related_name="fee_schedules",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    effective_from = models.DateField(help_text="Fecha a partir de la cual aplica esta cuota (incluyente)")
    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["residential", "effective_from"]),
        ]
        ordering = ["-effective_from", "-created_at"]

    def __str__(self):
        return f"{self.residential} - {self.amount} desde {self.effective_from}"


class ChargeStatus(models.TextChoices):
    PENDING = "PENDING", "Pendiente"
    PARTIAL = "PARTIAL", "Parcial"
    PAID = "PAID", "Pagado"
    VOID = "VOID", "Anulado"


class MonthlyCharge(UUIDModel):
    """
    Mensualidad por unidad y mes.
    period representa el primer día del mes (ej: 2026-02-01).
    """
    residential = models.ForeignKey(
        "core.Residential",
        on_delete=models.PROTECT,
        related_name="monthly_charges",
    )
    unit = models.ForeignKey(
        "core.Unit",
        on_delete=models.PROTECT,
        related_name="monthly_charges",
    )

    period = models.DateField(help_text="Primer día del mes (ej: 2026-02-01)")
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    due_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=ChargeStatus.choices, default=ChargeStatus.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("unit", "period")]
        indexes = [
            models.Index(fields=["residential", "period"]),
            models.Index(fields=["unit", "period"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-period", "-created_at"]

    def clean(self):
        # Mantener consistencia residencial <-> unit
        if self.unit_id and self.residential_id:
            if self.unit.residential_id != self.residential_id:
                raise ValidationError("Residential no coincide con el Residential de la Unit.")

        # period debe ser primer día del mes (opcional, pero recomendado)
        if self.period and self.period.day != 1:
            raise ValidationError({"period": "period debe ser el primer día del mes (día 1)."})

    @property
    def allocated_amount(self) -> Decimal:
        agg = self.allocations.filter(payment__status=PaymentStatus.APPROVED).aggregate(s=Sum("amount_applied"))
        return agg["s"] or Decimal("0.00")

    @property
    def balance(self) -> Decimal:
        return max(self.amount - self.allocated_amount, Decimal("0.00"))

    def __str__(self):
        return f"{self.unit} {self.period} {self.amount} ({self.status})"


class PaymentStatus(models.TextChoices):
    SUBMITTED = "SUBMITTED", "En revisión"
    APPROVED = "APPROVED", "Aprobado"
    REJECTED = "REJECTED", "Rechazado"


def receipt_upload_path(instance, filename: str) -> str:
    # limpia el filename (quita rutas, backslashes, etc.)
    base = os.path.basename(filename).replace("\\", "/")
    base = os.path.basename(base)  # por si venía con carpetas

    safe_name = get_valid_filename(base)

    # ruta más corta para evitar max_length issues
    return f"receipts/{instance.uuid}/{safe_name}"

class PaymentSubmission(UUIDModel):
    """
    Un comprobante que sube el Owner (vía su user).
    Puede cubrir varias mensualidades mediante PaymentAllocation.
    """
    residential = models.ForeignKey(
        "core.Residential",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    unit = models.ForeignKey(
        "core.Unit",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    owner = models.ForeignKey(
        "core.Owner",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_payments",
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=120, blank=True, default="", help_text="Folio/Referencia de transferencia")

    receipt_image = models.ImageField(
        upload_to=receipt_upload_path,
        max_length=255,
    )

    status = models.CharField(max_length=12, choices=PaymentStatus.choices, default=PaymentStatus.SUBMITTED)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_payments",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True, default="")

    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["residential", "status", "submitted_at"]),
            models.Index(fields=["unit", "submitted_at"]),
        ]
        ordering = ["-submitted_at"]

    def clean(self):
        # Consistencia residencial/unit/owner
        if self.unit_id and self.residential_id:
            if self.unit.residential_id != self.residential_id:
                raise ValidationError("Residential no coincide con el de la Unit.")
        if self.owner_id and self.residential_id:
            if self.owner.residential_id != self.residential_id:
                raise ValidationError("Residential no coincide con el del Owner.")
        if self.unit_id and self.owner_id and self.unit.owner_id != self.owner_id:
            # Si unit.owner es OneToOne, esto es fuerte. Si permites nulls, ajusta.
            raise ValidationError("El Owner no coincide con el Owner actual de la Unit.")

    @property
    def allocated_amount(self) -> Decimal:
        agg = self.allocations.aggregate(s=Sum("amount_applied"))
        return agg["s"] or Decimal("0.00")

    @property
    def remaining_amount(self) -> Decimal:
        return max(self.amount - self.allocated_amount, Decimal("0.00"))

    def __str__(self):
        return f"{self.unit} pago {self.amount} ({self.status})"


class PaymentAllocation(UUIDModel):
    """
    Distribución del pago a una mensualidad (permite un pago para múltiples meses).
    """
    payment = models.ForeignKey(
        PaymentSubmission,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    charge = models.ForeignKey(
        MonthlyCharge,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    amount_applied = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("payment", "charge")]
        indexes = [
            models.Index(fields=["charge"]),
            models.Index(fields=["payment"]),
        ]

    def clean(self):
        # Mismos residenciales para evitar asignaciones cruzadas
        if self.payment_id and self.charge_id:
            if self.payment.residential_id != self.charge.residential_id:
                raise ValidationError("Allocation inválida: payment y charge deben ser del mismo residential.")
            if self.payment.unit_id != self.charge.unit_id:
                raise ValidationError("Allocation inválida: payment y charge deben ser de la misma unit.")

        if self.amount_applied is not None and self.amount_applied <= 0:
            raise ValidationError({"amount_applied": "Debe ser mayor a 0."})

    def __str__(self):
        return f"{self.payment} -> {self.charge.period} ({self.amount_applied})"


# ---------- Helpers de negocio (útiles para aprobar pagos) ----------

def recompute_charge_status(charge: MonthlyCharge):
    """
    Recalcula status basado en allocations de pagos APPROVED.
    """
    allocated = charge.allocated_amount
    if charge.status == ChargeStatus.VOID:
        return

    if allocated <= 0:
        charge.status = ChargeStatus.PENDING
    elif allocated < charge.amount:
        charge.status = ChargeStatus.PARTIAL
    else:
        charge.status = ChargeStatus.PAID
    charge.save(update_fields=["status"])


@transaction.atomic
def approve_payment(payment: PaymentSubmission, reviewer_user):
    """
    Aprueba el pago y actualiza status de mensualidades relacionadas.
    """
    if payment.status != PaymentStatus.SUBMITTED:
        return

    payment.status = PaymentStatus.APPROVED
    payment.reviewed_by = reviewer_user
    payment.reviewed_at = timezone.now()
    payment.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    # Recalcular las mensualidades afectadas
    for alloc in payment.allocations.select_related("charge"):
        recompute_charge_status(alloc.charge)


@transaction.atomic
def reject_payment(payment: PaymentSubmission, reviewer_user, notes: str = ""):
    """
    Rechaza el pago (no aplica allocations en status porque solo cuentan APPROVED).
    """
    if payment.status != PaymentStatus.SUBMITTED:
        return

    payment.status = PaymentStatus.REJECTED
    payment.reviewed_by = reviewer_user
    payment.reviewed_at = timezone.now()
    payment.review_notes = notes or payment.review_notes
    payment.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])