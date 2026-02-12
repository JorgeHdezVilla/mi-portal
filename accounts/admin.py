from django.contrib import admin
from .models import OwnerAccount


@admin.register(OwnerAccount)
class OwnerAccountAdmin(admin.ModelAdmin):
    list_display = ("owner", "user", "created_at")
    search_fields = ("owner__first_name", "owner__last_name", "user__email")
