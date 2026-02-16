import uuid
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError

class EntryMethod(models.TextChoices):
    CAR = "CAR", "Auto"
    TAXI = "TAXI", "Taxi"
    MOTORCYCLE = "MOTORCYCLE", "Motocicleta"
    BIKE = "BIKE", "Bicicleta"
    OTHER = "OTHER", "Otro"


class UUIDModel(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True

class VisitPass(UUIDModel):
    """
    Un pase de visita creado por un Owner para su Unit.
    El QR debe representar un 'code' único + validaciones (fechas/uso).
    """
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    unit = models.ForeignKey("core.Unit", on_delete=models.CASCADE, related_name="visit_passes")
    
    residential = models.ForeignKey(
        "core.Residential",
        on_delete=models.PROTECT,
        related_name="visit_passes",
    )
    
    created_by = models.ForeignKey("auth.User", on_delete=models.PROTECT, related_name="created_visit_passes")

    visitor_name = models.CharField(max_length=160)
    arrival_at = models.DateTimeField(help_text="Fecha/hora de llegada")
    valid_days = models.PositiveSmallIntegerField(default=1, help_text="Días de validez desde arrival_at")

    one_time_use = models.BooleanField(default=False, help_text="Si es 1 solo uso (entrada+salida una vez)")
    entry_method = models.CharField(max_length=20, choices=EntryMethod.choices, default=EntryMethod.OTHER)

    notes = models.TextField(blank=True, default="")

    # Código que irá en el QR (no es imagen; es un token)
    code = models.CharField(max_length=64, unique=True, editable=False)

    # Estado de uso
    first_in_at = models.DateTimeField(null=True, blank=True)
    first_out_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.code:
            # token corto/seguro
            self.code = uuid.uuid4().hex  # 32 chars
        super().save(*args, **kwargs)

    @property
    def valid_until(self):
        return self.arrival_at + timezone.timedelta(days=self.valid_days)

    def is_active_now(self, now=None):
        now = now or timezone.now()
        return (self.revoked_at is None) and (self.arrival_at <= now <= self.valid_until)

    def __str__(self):
        return f"{self.visitor_name} - {self.unit}"


class ScanType(models.TextChoices):
    IN = "IN", "Entrada"
    OUT = "OUT", "Salida"


class VisitScan(UUIDModel):
    visit_pass = models.ForeignKey(VisitPass, on_delete=models.CASCADE, related_name="scans")
    scan_type = models.CharField(max_length=10, choices=ScanType.choices)
    scanned_at = models.DateTimeField(auto_now_add=True)
    device_id = models.CharField(max_length=100, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.visit_pass.code} - {self.scan_type}"

def clean(self):
    if self.unit_id and self.residential_id:
        if self.unit.residential_id != self.residential_id:
            raise ValidationError("Residential no coincide con el Residential de la Unit.")