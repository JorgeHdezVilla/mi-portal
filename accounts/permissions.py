from rest_framework.permissions import BasePermission

class IsOwnerUser(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and hasattr(request.user, "owner_account")

class IsGuardUser(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and hasattr(request.user, "guard_account") and request.user.guard_account.is_active
