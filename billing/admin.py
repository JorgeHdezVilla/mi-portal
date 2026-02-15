from django.contrib import admin, messages
from django import forms
from django.utils import timezone

from .models import (
    FeeSchedule,
    MonthlyCharge,
    PaymentSubmission,
    PaymentAllocation,
    PaymentStatus,
    approve_payment,
    reject_payment,
)

from core.models import Residential, Unit, Owner

from datetime import date
from django.urls import path
from django.shortcuts import redirect
from django.template.response import TemplateResponse

from .models import FeeSchedule, MonthlyCharge, ChargeStatus
from core.models import Unit


def _user_residential(request):
    if request.user.is_superuser:
        return None
    profile = getattr(request.user, "staff_residential_profile", None)
    return getattr(profile, "residential", None)


class ResidentialScopedAdmin(admin.ModelAdmin):
    """
    Base: superuser ve todo.
    Staff residencial: solo ve y opera sobre su residential.
    Requiere que el modelo tenga campo residential (FK).
    """

    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        return request.user.is_staff and _user_residential(request) is not None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        res = _user_residential(request)
        if not request.user.is_staff or res is None:
            return qs.none()
        return qs.filter(residential_id=res.pk)

    def _obj_allowed(self, request, obj) -> bool:
        if request.user.is_superuser:
            return True
        res = _user_residential(request)
        return request.user.is_staff and res is not None and getattr(obj, "residential_id", None) == res.pk

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


# ---------------- FeeSchedule ----------------

@admin.register(FeeSchedule)
class FeeScheduleAdmin(ResidentialScopedAdmin):
    list_display = ("effective_from", "amount", "residential", "created_at")
    list_filter = ("residential",)
    search_fields = ("residential__name", "residential__code")
    ordering = ("-effective_from",)

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        # Staff: ocultar residential (se asigna automáticamente)
        if not request.user.is_superuser and "residential" in fields:
            fields.remove("residential")
        return fields

    def save_model(self, request, obj, form, change):
        # Staff: forzar residential
        if not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                obj.residential = res
        super().save_model(request, obj, form, change)

# ---------------- PaymentAllocation inline ----------------

class PaymentAllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    autocomplete_fields = ("charge",)
    fields = ("charge", "amount_applied", "created_at")
    readonly_fields = ("created_at",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Staff: solo permitir asignar charges de su residential
        if db_field.name == "charge" and not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                kwargs["queryset"] = MonthlyCharge.objects.filter(residential_id=res.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ---------------- PaymentSubmission ----------------

@admin.register(PaymentSubmission)
class PaymentSubmissionAdmin(ResidentialScopedAdmin):
    list_display = ("submitted_at", "unit", "owner", "amount", "status", "residential")
    list_filter = ("residential", "status")
    search_fields = ("unit__reference", "owner__email", "reference")
    ordering = ("-submitted_at",)

    inlines = [PaymentAllocationInline]

    actions = ["approve_selected", "reject_selected"]

    readonly_fields = ("submitted_at", "reviewed_at", "reviewed_by")

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        # Staff: ocultar residential (se asigna automático)
        if not request.user.is_superuser and "residential" in fields:
            fields.remove("residential")
        return fields

    def get_queryset(self, request):
        # select_related para performance + same filtering
        qs = super().get_queryset(request).select_related("residential", "unit", "owner", "submitted_by", "reviewed_by")
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Staff: filtrar unit/owner al residential del staff
        if not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                if db_field.name == "unit":
                    kwargs["queryset"] = Unit.objects.filter(residential_id=res.pk)
                if db_field.name == "owner":
                    kwargs["queryset"] = Owner.objects.filter(residential_id=res.pk)
                if db_field.name == "residential":
                    kwargs["queryset"] = Residential.objects.filter(pk=res.pk)

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        # Staff: forzar residential y marcar reviewer si cambió status manualmente
        if not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                obj.residential = res

        super().save_model(request, obj, form, change)

    @admin.action(description="Aprobar pagos seleccionados")
    def approve_selected(self, request, queryset):
        count = 0
        for p in queryset:
            if p.status == PaymentStatus.SUBMITTED and self._obj_allowed(request, p):
                approve_payment(p, request.user)
                count += 1
        self.message_user(request, f"Aprobados: {count}", level=messages.SUCCESS)

    @admin.action(description="Rechazar pagos seleccionados")
    def reject_selected(self, request, queryset):
        count = 0
        for p in queryset:
            if p.status == PaymentStatus.SUBMITTED and self._obj_allowed(request, p):
                reject_payment(p, request.user, notes="Rechazado desde acción masiva")
                count += 1
        self.message_user(request, f"Rechazados: {count}", level=messages.WARNING)


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _iter_month_starts(start: date, end: date):
    """Itera meses (primer día) desde start hasta end (incluyente)."""
    cur = _month_start(start)
    end = _month_start(end)
    while cur <= end:
        yield cur
        # sumar 1 mes
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)


def _fee_for_month(residential, period: date):
    """Obtiene la cuota vigente para ese mes."""
    fs = (
        FeeSchedule.objects
        .filter(residential=residential, effective_from__lte=period)
        .order_by("-effective_from")
        .first()
    )
    return fs.amount if fs else None


class GenerateChargesForm(forms.Form):
    start_month = forms.DateField(
        help_text="Primer día del mes (ej: 2026-01-01)",
        widget=forms.DateInput(attrs={"type": "date"})
    )
    end_month = forms.DateField(
        help_text="Primer día del mes (ej: 2026-12-01)",
        widget=forms.DateInput(attrs={"type": "date"})
    )


@admin.register(MonthlyCharge)
class MonthlyChargeAdmin(admin.ModelAdmin):
    list_display = ("period", "unit", "amount", "status", "residential", "created_at")
    list_filter = ("residential", "status", "period")
    search_fields = ("unit__reference", "residential__name", "residential__code")
    ordering = ("-period", "unit__reference")

    autocomplete_fields = ("unit",)

    # --- scope por residential (superuser ve todo; staff solo su residential) ---
    def has_module_permission(self, request):
        if request.user.is_superuser:
            return True
        return request.user.is_staff and _user_residential(request) is not None

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("unit", "residential")
        if request.user.is_superuser:
            return qs
        res = _user_residential(request)
        if not request.user.is_staff or res is None:
            return qs.none()
        return qs.filter(residential_id=res.pk)

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return request.user.is_staff and _user_residential(request) is not None

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        if request.user.is_superuser:
            return True
        res = _user_residential(request)
        return request.user.is_staff and res is not None and obj.residential_id == res.pk

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj is None:
            return self.has_module_permission(request)
        res = _user_residential(request)
        return request.user.is_staff and res is not None and obj.residential_id == res.pk

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Staff: filtrar Unit por su residential
        if db_field.name == "unit" and not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                kwargs["queryset"] = Unit.objects.filter(residential_id=res.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # --- Custom URL / vista para generar cargos ---
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path("generate/", self.admin_site.admin_view(self.generate_view), name="billing_monthlycharge_generate"),
        ]
        return my_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        # link al generador arriba en la lista
        extra_context["generate_url"] = "generate/"
        return super().changelist_view(request, extra_context=extra_context)

    def generate_view(self, request):
        # Solo staff con residential o superuser
        if not self.has_add_permission(request):
            self.message_user(request, "No tienes permisos para generar cargos.", level=messages.ERROR)
            return redirect("..")

        if request.method == "POST":
            form = GenerateChargesForm(request.POST)
            if form.is_valid():
                start = _month_start(form.cleaned_data["start_month"])
                end = _month_start(form.cleaned_data["end_month"])
                if end < start:
                    self.message_user(request, "El rango es inválido (end < start).", level=messages.ERROR)
                    return redirect(".")

                # Determinar residential
                if request.user.is_superuser:
                    # Para superuser, opcionalmente podrías pedir residential en el form.
                    # Por ahora: usa el residential del primer objeto filtrado o bloquea.
                    self.message_user(
                        request,
                        "Como superuser, genera cargos desde el admin de un residencial específico (o dime y te lo dejo con selector).",
                        level=messages.WARNING,
                    )
                    return redirect("..")

                res = _user_residential(request)
                if res is None:
                    self.message_user(request, "Tu usuario no tiene Residential asignado.", level=messages.ERROR)
                    return redirect("..")

                units = Unit.objects.filter(residential_id=res.pk).only("id")
                if not units.exists():
                    self.message_user(request, "No hay unidades para generar cargos.", level=messages.WARNING)
                    return redirect("..")

                created_count = 0
                skipped_count = 0
                missing_fee_months = []

                for period in _iter_month_starts(start, end):
                    fee = _fee_for_month(res, period)
                    if fee is None:
                        missing_fee_months.append(period)
                        continue

                    for u in units:
                        obj, created = MonthlyCharge.objects.get_or_create(
                            residential=res,
                            unit=u,
                            period=period,
                            defaults={"amount": fee, "status": ChargeStatus.PENDING},
                        )
                        if created:
                            created_count += 1
                        else:
                            skipped_count += 1

                if missing_fee_months:
                    months = ", ".join([m.strftime("%Y-%m") for m in missing_fee_months[:12]])
                    extra = "" if len(missing_fee_months) <= 12 else f" (+{len(missing_fee_months)-12} más)"
                    self.message_user(
                        request,
                        f"⚠️ No se generaron cargos en meses sin cuota definida: {months}{extra}. Crea FeeSchedule con effective_from para esos meses.",
                        level=messages.WARNING,
                    )

                self.message_user(
                    request,
                    f"✅ Generación completa. Creados: {created_count}. Ya existían: {skipped_count}.",
                    level=messages.SUCCESS,
                )
                return redirect("..")
        else:
            # default: año actual completo
            today = timezone.now().date()
            form = GenerateChargesForm(initial={
                "start_month": date(today.year, 1, 1),
                "end_month": date(today.year, 12, 1),
            })

        context = dict(
            self.admin_site.each_context(request),
            title="Generar mensualidades",
            form=form,
        )
        return TemplateResponse(request, "admin/billing/monthlycharge/generate.html", context)
