from rest_framework import viewsets, permissions
from .models import VisitPass
from accounts.permissions import IsOwnerUser
from .serializers import VisitPassCreateSerializer, GuardVisitPassDetailSerializer
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from accounts.permissions import IsGuardUser
from .serializers import VisitScanRequestSerializer, GuardVisitPassDetailSerializer
from .models import VisitPass, VisitScan

class IsOwnerUser(permissions.BasePermission):
    def has_permission(self, request, view):
        # Solo usuarios que tienen owner_account pueden usar esto
        return request.user.is_authenticated and hasattr(request.user, "owner_account")


class OwnerVisitPassViewSet(viewsets.ModelViewSet):
    permission_classes = [IsOwnerUser]

    def get_queryset(self):
        owner = self.request.user.owner_account.owner
        unit = owner.unit
        return VisitPass.objects.filter(unit=unit).select_related("unit", "residential").order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "create":
            return VisitPassCreateSerializer
        return GuardVisitPassDetailSerializer  # sirve también para owner
    

class GuardScanView(APIView):
    permission_classes = [IsGuardUser]

    def post(self, request):
        s = VisitScanRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        code = s.validated_data["code"]
        scan_type = s.validated_data["scan_type"]

        guard_res = request.user.guard_account.residential
        now = timezone.now()

        # 1) Busca el pase dentro del residencial del guardia
        try:
            vp = VisitPass.objects.select_related("unit", "residential", "unit__owner").get(code=code, residential=guard_res)
        except VisitPass.DoesNotExist:
            return Response({"detail": "Código no encontrado para este residencial."}, status=status.HTTP_404_NOT_FOUND)

        # 2) Valida vigencia
        if vp.revoked_at is not None:
            return Response({"detail": "QR revocado."}, status=status.HTTP_400_BAD_REQUEST)

        if not (vp.arrival_at <= now <= vp.valid_until):
            return Response({"detail": "QR fuera de vigencia."}, status=status.HTTP_400_BAD_REQUEST)

        # 3) Reglas de uso
        if vp.one_time_use:
            # Permite IN solo una vez, OUT solo una vez, en ese orden
            if scan_type == "IN":
                if vp.first_in_at is not None:
                    return Response({"detail": "Este QR ya fue usado para entrada."}, status=status.HTTP_400_BAD_REQUEST)
                vp.first_in_at = now
            else:  # OUT
                if vp.first_in_at is None:
                    return Response({"detail": "Primero debe registrarse la entrada."}, status=status.HTTP_400_BAD_REQUEST)
                if vp.first_out_at is not None:
                    return Response({"detail": "Este QR ya fue usado para salida."}, status=status.HTTP_400_BAD_REQUEST)
                vp.first_out_at = now
        else:
            # Multiuso: registra IN/OUT pero sin bloquear (si quieres limitar por día, lo hacemos luego)
            if scan_type == "IN" and vp.first_in_at is None:
                vp.first_in_at = now
            if scan_type == "OUT" and vp.first_out_at is None:
                vp.first_out_at = now

        vp.save(update_fields=["first_in_at", "first_out_at"])

        # 4) Auditoría
        VisitScan.objects.create(
            visit_pass=vp,
            scan_type=scan_type,
            device_id=s.validated_data.get("device_id", ""),
            notes=s.validated_data.get("notes", ""),
        )

        return Response(GuardVisitPassDetailSerializer(vp).data, status=status.HTTP_200_OK)
