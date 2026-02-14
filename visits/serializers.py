from rest_framework import serializers
from .models import VisitPass

class VisitPassCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = VisitPass
        fields = ("visitor_name", "arrival_at", "valid_days", "one_time_use", "entry_method", "notes")

    def create(self, validated_data):
        request = self.context["request"]
        owner = request.user.owner_account.owner
        unit = owner.unit  # Owner solo 1 unit

        return VisitPass.objects.create(
            unit=unit,
            residential=unit.residential,
            created_by=request.user,
            **validated_data,
        )


class VisitPassListSerializer(serializers.ModelSerializer):
    valid_until = serializers.DateTimeField(source="valid_until", read_only=True)

    class Meta:
        model = VisitPass
        fields = ("uuid", "visitor_name", "arrival_at", "valid_days", "valid_until",
                  "one_time_use", "entry_method", "notes", "code",
                  "first_in_at", "first_out_at", "revoked_at", "created_at")


class VisitScanRequestSerializer(serializers.Serializer):
    code = serializers.CharField()
    scan_type = serializers.ChoiceField(choices=["IN", "OUT"])
    device_id = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class GuardVisitPassDetailSerializer(serializers.ModelSerializer):
    unit_reference = serializers.CharField(source="unit.reference", read_only=True)
    residential_name = serializers.CharField(source="residential.name", read_only=True)

    owner_name = serializers.SerializerMethodField()
    owner_email = serializers.SerializerMethodField()

    class Meta:
        model = VisitPass
        fields = (
            "uuid", "code", "visitor_name", "arrival_at", "valid_days",
            "one_time_use", "entry_method", "notes",
            "first_in_at", "first_out_at", "revoked_at",
            "unit_reference", "residential_name",
            "owner_name", "owner_email",
        )

    def get_owner_name(self, obj):
        if obj.unit.owner:
            return str(obj.unit.owner)
        return None

    def get_owner_email(self, obj):
        if obj.unit.owner:
            return obj.unit.owner.email
        return None
