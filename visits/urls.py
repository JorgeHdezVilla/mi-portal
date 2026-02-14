from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import OwnerVisitPassViewSet, GuardScanView

router = DefaultRouter()
router.register(r"owner/visits", OwnerVisitPassViewSet, basename="owner-visits")

urlpatterns = router.urls + [
    path("guard/scan/", GuardScanView.as_view(), name="guard-scan"),
]
