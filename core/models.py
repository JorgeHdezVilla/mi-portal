import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

def clean(self):
    if self.owner and self.residential_id:
        if self.owner.residential_id != self.residential_id:
            raise ValidationError({"owner": "El dueño seleccionado no pertenece a este residencial."})


class UUIDModel(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class Residential(UUIDModel):
    name = models.CharField(max_length=200)

    # Clave corta opcional, única cuando existe.
    code = models.CharField(max_length=50, blank=True, null=True, unique=True)

    address = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["code"]),
        ]

    def __str__(self) -> str:
        return self.name


class UnitType(models.TextChoices):
    HOUSE = "HOUSE", "Casa"
    APARTMENT = "APT", "Departamento"


class Owner(UUIDModel):
    residential = models.ForeignKey(
        "Residential",
        on_delete=models.PROTECT,
        related_name="owners",
    )

    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=120, blank=True, default="")

    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=30, blank=True, default="")
    tax_id = models.CharField(max_length=40, blank=True, default="")

    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["residential", "email"]),
            models.Index(fields=["residential", "phone"]),
            models.Index(fields=["residential", "first_name", "last_name"]),
        ]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()
    
    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

class Unit(UUIDModel):
    residential = models.ForeignKey(
        Residential,
        on_delete=models.PROTECT,
        related_name="units",
    )

    unit_type = models.CharField(max_length=10, choices=UnitType.choices, default=UnitType.HOUSE)

    reference = models.CharField(
        max_length=80,
        help_text="Ej: Casa 4A, Depto 301"
    )

    street = models.CharField(max_length=120, blank=True, default="")
    ext_number = models.CharField(max_length=30, blank=True, default="")
    int_number = models.CharField(max_length=30, blank=True, default="")

    land_m2 = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    construction_m2 = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    owner = models.OneToOneField(
        Owner,
        on_delete=models.PROTECT,
        related_name="unit",
        null=True,
        blank=True,
        help_text="Dueño actual (1 unidad por dueño)"
    )

    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("residential", "reference")]
        indexes = [
            models.Index(fields=["residential", "reference"]),
        ]

    def __str__(self) -> str:
        return f"{self.residential.name} - {self.reference}"
    

class StaffResidentialProfile(UUIDModel):
    """
    Perfil para usuarios staff que administran SOLO un Residential.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_residential_profile",
    )
    residential = models.ForeignKey(
        Residential,
        on_delete=models.PROTECT,
        related_name="staff_admins",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Staff Residential Profile"
        verbose_name_plural = "Staff Residential Profiles"

    def clean(self):
        if self.user and not self.user.is_staff:
            raise ValidationError("El usuario debe tener is_staff=True para tener StaffResidentialProfile.")
        if self.user and self.user.is_superuser:
            raise ValidationError("Un superusuario no requiere StaffResidentialProfile.")

    def __str__(self) -> str:
        return f"{self.user.username} -> {self.residential.name}"


class UnitBalanceView(Unit):
    class Meta:
        proxy = True
        verbose_name = "Saldo por unidad"
        verbose_name_plural = "Saldos por unidad"