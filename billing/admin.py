from datetime import date

from django import forms
from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone

from core.models import Residential, Unit, Owner

from .models import (
    FeeSchedule,
    MonthlyCharge,
    PaymentSubmission,
    PaymentAllocation,
    PaymentSubmissionApproval,  # proxy
    ChargeStatus,
    PaymentStatus,
    approve_payment,
    reject_payment,
)

from billing.services import apply_available_credit_to_charge


# ---------------- Helpers ----------------

def _user_residential(request):
    """
    Devuelve el Residential del admin residencial.
    Superuser -> None (ve todo).
    """
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

    def has_add_permission(self, request):
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

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Staff: filtrar Residential a solo su residential
        if db_field.name == "residential" and not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                kwargs["queryset"] = Residential.objects.filter(pk=res.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ---------------- FeeSchedule ----------------

@admin.register(FeeSchedule)
class FeeScheduleAdmin(ResidentialScopedAdmin):
    list_display = ("effective_from", "amount", "residential", "created_at")
    list_filter = ("residential",)
    search_fields = ("residential__name", "residential__code")
    ordering = ("-effective_from",)

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        # Staff: ocultar residential (se asigna automático)
        if not request.user.is_superuser and "residential" in fields:
            fields.remove("residential")
        return fields

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                obj.residential = res
        super().save_model(request, obj, form, change)


# ---------------- MonthlyCharge + Generador ----------------

def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _iter_month_starts(start: date, end: date):
    cur = _month_start(start)
    end = _month_start(end)
    while cur <= end:
        yield cur
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)


def _fee_for_month(residential, period: date):
    fs = (
        FeeSchedule.objects
        .filter(residential=residential, effective_from__lte=period)
        .order_by("-effective_from")
        .first()
    )
    return fs.amount if fs else None


from datetime import date
from django import forms
from django.utils import timezone


MONTH_CHOICES = [
    (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"),
    (5, "Mayo"), (6, "Junio"), (7, "Julio"), (8, "Agosto"),
    (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre"),
]


class GenerateChargesForm(forms.Form):
    start_year = forms.IntegerField(min_value=2000, max_value=2100, label="Año inicio")
    start_month = forms.ChoiceField(choices=MONTH_CHOICES, label="Mes inicio")

    end_year = forms.IntegerField(min_value=2000, max_value=2100, label="Año fin")
    end_month = forms.ChoiceField(choices=MONTH_CHOICES, label="Mes fin")
    

    def clean(self):
        cleaned = super().clean()
        sy = cleaned.get("start_year")
        sm = cleaned.get("start_month")
        ey = cleaned.get("end_year")
        em = cleaned.get("end_month")

        if not (sy and sm and ey and em):
            return cleaned

        start = date(int(sy), int(sm), 1)
        end = date(int(ey), int(em), 1)

        if end < start:
            raise forms.ValidationError("El rango es inválido: fin es menor que inicio.")

        cleaned["start_date"] = start
        cleaned["end_date"] = end
        return cleaned


@admin.register(MonthlyCharge)
class MonthlyChargeAdmin(ResidentialScopedAdmin):
    list_display = ("period", "unit", "amount", "status", "residential", "created_at")
    list_filter = ("residential", "status", "period")
    search_fields = ("unit__reference", "residential__name", "residential__code")
    ordering = ("-period", "unit__reference")
    autocomplete_fields = ("unit",)

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if not request.user.is_superuser and "residential" in fields:
            fields.remove("residential")
        return fields

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                obj.residential = res
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "unit" and not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                kwargs["queryset"] = Unit.objects.filter(residential_id=res.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # ----- custom URL for generator -----
    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path("generate/", self.admin_site.admin_view(self.generate_view),
                 name="billing_monthlycharge_generate"),
        ]
        return my_urls + urls

    def generate_view(self, request):
        if not self.has_add_permission(request):
            self.message_user(request, "No tienes permisos para generar cargos.", level=messages.ERROR)
            return redirect("..")

        # Solo staff residencial (para superuser podemos mejorarlo con selector si quieres)
        if request.user.is_superuser:
            self.message_user(
                request,
                "Como superuser, genera cargos entrando al admin con un usuario staff-residencial (o dime y te agrego selector de residential).",
                level=messages.WARNING,
            )
            return redirect("..")

        res = _user_residential(request)
        if res is None:
            self.message_user(request, "Tu usuario no tiene Residential asignado.", level=messages.ERROR)
            return redirect("..")

        if request.method == "POST":
            form = GenerateChargesForm(request.POST)
            if form.is_valid():
                start = form.cleaned_data["start_date"]
                end = form.cleaned_data["end_date"]
                if end < start:
                    self.message_user(request, "Rango inválido (end < start).", level=messages.ERROR)
                    return redirect(".")

                units = Unit.objects.filter(residential_id=res.pk).only("pk")
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
                            apply_available_credit_to_charge(obj)
                            created_count += 1
                        else:
                            skipped_count += 1

                if missing_fee_months:
                    months = ", ".join([m.strftime("%Y-%m") for m in missing_fee_months[:12]])
                    extra = "" if len(missing_fee_months) <= 12 else f" (+{len(missing_fee_months)-12} más)"
                    self.message_user(
                        request,
                        f"⚠️ Meses sin cuota definida (no se generaron): {months}{extra}. "
                        f"Crea FeeSchedule con effective_from para cubrir esos meses.",
                        level=messages.WARNING,
                    )

                self.message_user(
                    request,
                    f"✅ Generación completa. Creados: {created_count}. Ya existían: {skipped_count}.",
                    level=messages.SUCCESS,
                )
                return redirect("..")
        else:
            today = timezone.now().date()
            form = GenerateChargesForm(initial={
                "start_year": today.year,
                "start_month": today.month,
                "end_year": today.year,
                "end_month": today.month,
            })

        context = dict(
            self.admin_site.each_context(request),
            title="Generar mensualidades",
            form=form,
        )
        return TemplateResponse(request, "admin/billing/monthlycharge/generate.html", context)


# ---------------- PaymentAllocation Inline ----------------

class PaymentAllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    autocomplete_fields = ("charge",)
    fields = ("charge", "amount_applied", "created_at")
    readonly_fields = ("created_at",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Staff: solo charges del residential
        if db_field.name == "charge" and not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                kwargs["queryset"] = MonthlyCharge.objects.filter(residential_id=res.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ---------------- PaymentSubmission (CRUD normal) ----------------

@admin.register(PaymentSubmission)
class PaymentSubmissionAdmin(ResidentialScopedAdmin):
    """
    CRUD normal (crear/editar).
    Aquí NO ponemos vista especial: es el CRUD.
    """
    list_display = ("submitted_at", "unit", "owner", "amount", "status", "residential")
    list_filter = ("residential", "status")
    search_fields = ("unit__reference", "owner__email", "reference")
    ordering = ("-submitted_at",)

    inlines = [PaymentAllocationInline]

    autocomplete_fields = ("unit", "owner", "submitted_by", "reviewed_by")

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))

        # ✅ ocultar campos que se asignan automáticos
        for f in ["submitted_by", "owner", "reviewed_by", "reviewed_at"]:
            if f in fields:
                fields.remove(f)

        # Staff: ocultar residential (se asigna automático)
        if not request.user.is_superuser and "residential" in fields:
            fields.remove("residential")

        return fields

    def save_model(self, request, obj, form, change):
        # Staff: forzar residential
        if not request.user.is_superuser:
            res = _user_residential(request)
            if res is not None:
                obj.residential = res

        # ✅ submitted_by automático al crear
        if not change and not obj.submitted_by_id:
            obj.submitted_by = request.user

        # ✅ owner automático basado en la unit seleccionada
        # (se guarda como historial; aunque cambie el owner futuro, este pago queda ligado al owner de ese momento)
        if obj.unit_id:
            # asegúrate de tener select_related en queryset si quieres, pero aquí funciona igual
            unit_owner_id = obj.unit.owner_id
            if not unit_owner_id:
                raise ValidationError("La unidad seleccionada no tiene dueño asignado. Asigna un Owner a la Unit primero.")
            obj.owner_id = unit_owner_id

        super().save_model(request, obj, form, change)
        
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
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


# ---------------- PaymentSubmissionApproval (vista adicional solo aprobar) ----------------

class PaymentAllocationInlineReadOnly(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    fields = ("charge", "amount_applied", "created_at")
    readonly_fields = ("charge", "amount_applied", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PaymentSubmissionApproval)
class PaymentSubmissionApprovalAdmin(ResidentialScopedAdmin):
    """
    Menú adicional: Aprobaciones de pagos.
    - lista solo SUBMITTED
    - solo lectura
    - botones Aprobar/Rechazar
    """
    change_form_template = "admin/billing/paymentsubmissionapproval/change_form.html"

    list_display = ("submitted_at", "unit", "owner", "amount", "status", "residential")
    list_filter = ("residential",)
    search_fields = ("unit__reference", "owner__email", "reference")
    ordering = ("-submitted_at",)

    inlines = [PaymentAllocationInlineReadOnly]

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("residential", "unit", "owner")
        return qs.filter(status=PaymentStatus.SUBMITTED)  # 👈 SOLO pendientes

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in PaymentSubmission._meta.fields]

    def response_change(self, request, obj):
        self.message_user(request, "Esta vista es solo para aprobar/rechazar. Usa los botones.", level=messages.WARNING)
        return redirect(".")

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path("<path:object_id>/approve/",
                 self.admin_site.admin_view(self.approve_view),
                 name="billing_paymentsubmissionapproval_approve"),
            path("<path:object_id>/reject/",
                 self.admin_site.admin_view(self.reject_view),
                 name="billing_paymentsubmissionapproval_reject"),
        ]
        return my_urls + urls

    def approve_view(self, request, object_id):
        payment = get_object_or_404(PaymentSubmission, pk=object_id)

        if not self._obj_allowed(request, payment):
            self.message_user(request, "No tienes permiso para aprobar este pago.", level=messages.ERROR)
            return redirect("../../")

        if payment.status != PaymentStatus.SUBMITTED:
            self.message_user(request, "Este pago ya fue procesado.", level=messages.WARNING)
            return redirect("../")

        approve_payment(payment, request.user, auto_allocate=True)
        self.message_user(request, "✅ Pago aprobado y auto-asignado.", level=messages.SUCCESS)
        return redirect("../../")

    def reject_view(self, request, object_id):
        payment = get_object_or_404(PaymentSubmission, pk=object_id)

        if not self._obj_allowed(request, payment):
            self.message_user(request, "No tienes permiso para rechazar este pago.", level=messages.ERROR)
            return redirect("../../")

        if payment.status != PaymentStatus.SUBMITTED:
            self.message_user(request, "Este pago ya fue procesado.", level=messages.WARNING)
            return redirect("../")

        reject_payment(payment, request.user, notes="Rechazado desde Aprobaciones")
        self.message_user(request, "❌ Pago rechazado.", level=messages.WARNING)
        return redirect("../../")