from django.db import models
from django.conf import settings
from core.models import Owner


class OwnerAccount(models.Model):
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
