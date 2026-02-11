from django.contrib import admin
from django import forms

from .models import Residential, Unit, Owner, StaffResidentialProfile


def _user_residential(request):
    """
    Superuser: None (sin restricción)
    Staff admin: residential asignado en StaffResidentialProfile
    """
    if request.user.is_superuser:
        return None
    profile = getattr(request.user, "staff_residential_profile", None)
    return getattr(profile, "residential", None)

@admin.register(StaffResidentialProfile)
class StaffResidentialProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "residential", "created_at")
    search_fields = ("user__username", "user__email", "residential__name", "residential__code")
    list_filter = ("residential",)
    autocomplete_fields = ("user", "residential")


@admin.register(Residential)
class ResidentialAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "created_at")
    search_fields = ("name", "code")
    list_filter = ("is_active",)
    ordering = ("name",)

    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        return request.user.is_staff and _user_residential(request) is not None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        res = _user_residential(request)
        if request.user.is_superuser:
            return qs
        if not request.user.is_staff or res is None:
            return qs.none()
        return qs.filter(pk=res.pk)

    def _obj_allowed(self, request, obj):
        if request.user.is_superuser:
            return True
        res = _user_residential(request)
        return request.user.is_staff and res is not None and obj.pk == res.pk

    def has_view_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        return self._obj_allowed(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        return self._obj_allowed(request, obj)

    def has_delete_permission(self, request, obj=None):
        # Normalmente: solo superadmin borra residenciales.
        return request.user.is_superuser

    def has_add_permission(self, request):
        # Normalmente: solo superadmin crea residenciales.
        return request.user.is_superuser


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "tax_id",
        "residential",
        "is_active",
        "created_at",
    )
    search_fields = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "tax_id",
        "residential__name",
        "residential__code",
    )
    list_filter = ("is_active", "residential")
    ordering = ("first_name", "last_name")

    # --- VISIBILIDAD DEL MÓDULO ---
    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        return request.user.is_staff and _user_residential(request) is not None

    # --- LISTADO ---
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("residential")
        if request.user.is_superuser:
            return qs

        res = _user_residential(request)
        if not request.user.is_staff or res is None:
            return qs.none()

        return qs.filter(residential_id=res.pk)

    # --- PERMISOS POR OBJETO ---
    def _obj_allowed(self, request, obj):
        if request.user.is_superuser:
            return True

        res = _user_residential(request)
        if not request.user.is_staff or res is None:
            return False

        # ✅ La validación correcta: Owner pertenece a ese residential
        return obj.residential_id == res.pk

    def has_view_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        return self._obj_allowed(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        return self._obj_allowed(request, obj)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj is None:
            return self.has_module_permission(request)
        return self._obj_allowed(request, obj)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return request.user.is_staff and _user_residential(request) is not None

    # --- OCULTAR CAMPO RESIDENTIAL PARA ADMIN DE RESIDENTIAL ---
    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if not request.user.is_superuser and "residential" in fields:
            fields.remove("residential")
        return fields

    # --- FORZAR RESIDENTIAL EN GUARDADO (ANTI-HACK POST) ---
    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                obj.residential = res
        super().save_model(request, obj, form, change)


class UnitAdminForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        # Sacamos request si viene (sin romper si no viene)
        request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

        if request and not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                # ✅ Setear residential ANTES de validación
                self.instance.residential = res

                # Ocultar residential y fijar valor
                if "residential" in self.fields:
                    self.fields["residential"].queryset = Residential.objects.filter(pk=res.pk)
                    self.fields["residential"].initial = res
                    self.fields["residential"].widget = forms.HiddenInput()

                # Filtrar owners por residential
                if "owner" in self.fields:
                    self.fields["owner"].queryset = Owner.objects.filter(residential_id=res.pk)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    form = UnitAdminForm

    list_display = ("reference", "unit_type", "residential", "owner", "is_active", "created_at")
    search_fields = (
        "reference",
        "street",
        "ext_number",
        "int_number",
        "residential__name",
        "residential__code",
        "owner__first_name",
        "owner__last_name",
        "owner__email",
    )
    list_filter = ("unit_type", "residential", "is_active")
    ordering = ("residential__name", "reference")
    autocomplete_fields = ("owner",)

    # 1) Pasar request al form SIN duplicar kwargs
    def get_form(self, request, obj=None, **kwargs):
        Form = super().get_form(request, obj, **kwargs)

        class RequestForm(Form):
            def __init__(self2, *args, **kw):
                kw["request"] = request
                super().__init__(*args, **kw)

        return RequestForm

    # 2) Queryset restringido por residential
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("residential", "owner")
        if request.user.is_superuser:
            return qs
        res = _user_residential(request)
        if not request.user.is_staff or res is None:
            return qs.none()
        return qs.filter(residential_id=res.pk)

    # 3) Permisos por residential
    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        return request.user.is_staff and _user_residential(request) is not None

    def _obj_allowed(self, request, obj: Unit) -> bool:
        if request.user.is_superuser:
            return True
        res = _user_residential(request)
        return request.user.is_staff and res is not None and obj.residential_id == res.pk

    def has_view_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        return self._obj_allowed(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        return self._obj_allowed(request, obj)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj is None:
            return self.has_module_permission(request)
        return self._obj_allowed(request, obj)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return request.user.is_staff and _user_residential(request) is not None

    # 4) Doble seguridad: forzar residential al guardar
    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                obj.residential = res
        super().save_model(request, obj, form, change)
