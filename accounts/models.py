from django.db import models
from django.conf import settings
from core.models import Owner
from core.models import Residential
import uuid

class UUIDModel(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True
        
class OwnerAccount(UUIDModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owner_account",
    )

    owner = models.OneToOneField(
        Owner,
        on_delete=models.CASCADE,
        related_name="account",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.owner} -> {self.user.email}"


class GuardAccount(UUIDModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="guard_account",
    )
    residential = models.ForeignKey(
        Residential,
        on_delete=models.PROTECT,
        related_name="guards",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Guard {self.user.username} -> {self.residential.name}"
