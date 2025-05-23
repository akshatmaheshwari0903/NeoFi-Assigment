from rest_framework import permissions
from .models import EventPermission

class HasEventPermission(permissions.BasePermission):
    """
    Allows access if the user has any permission for the event (read: any role, write: owner/editor).
    """
    def has_object_permission(self, request, view, obj):
        if not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

        # Check if user has any permission for the event
        try:
            permission = obj.permissions.get(user=request.user)
            
            # For read operations, any role is sufficient
            if request.method in permissions.SAFE_METHODS:
                return True

            # For write operations, only owners and editors can proceed
            return permission.role in [EventPermission.Role.OWNER, EventPermission.Role.EDITOR]
        except EventPermission.DoesNotExist:
            return False

class IsEventOwner(permissions.BasePermission):
    """
    Allows access only if the user is the owner of the event.
    """
    def has_object_permission(self, request, view, obj):
        if not request.user.is_authenticated:
            return False

        # Superusers can do anything
        if request.user.is_superuser:
            return True

        # Check if user is the owner
        try:
            permission = obj.permissions.get(user=request.user)
            return permission.role == EventPermission.Role.OWNER
        except EventPermission.DoesNotExist:
            return False

class IsEventOwnerOrEditor(permissions.BasePermission):
    """
    Allows access if the user is the owner or an editor of the event.
    """
    def has_object_permission(self, request, view, obj):
        if not request.user.is_authenticated:
            return False

        # Superusers can do anything
        if request.user.is_superuser:
            return True

        # Check if user is owner or editor
        try:
            permission = obj.permissions.get(user=request.user)
            return permission.role in [EventPermission.Role.OWNER, EventPermission.Role.EDITOR]
        except EventPermission.DoesNotExist:
            return False
