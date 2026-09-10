from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsAdminRole(BasePermission):
    """
    Grants permission only to users with role 'admin' or Django is_staff/is_superuser.
    """
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and
            user.is_authenticated and
            (getattr(user, 'role', None) == 'admin' or user.is_staff or user.is_superuser)
        )


class IsAdminOrIQAC(BasePermission):
    """
    Grants permission to admin or Django is_staff/is_superuser.
    """
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and
            user.is_authenticated and
            (getattr(user, 'role', None) == 'admin' or user.is_staff or user.is_superuser)
        )


class IsStaffOrAdmin(BasePermission):
    """
    Grants permission to faculty, evaluation, admin, or is_staff/is_superuser.
    """
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and
            user.is_authenticated and
            (getattr(user, 'role', None) in ('admin', 'faculty', 'evaluation') or user.is_staff or user.is_superuser)
        )


class IsAdminOrReadOnly(BasePermission):
    """
    Authenticated users can read (GET, HEAD, OPTIONS).
    Mutating methods (POST, PUT, PATCH, DELETE) require Admin role or is_staff/is_superuser.
    """
    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return bool(getattr(user, 'role', None) == 'admin' or user.is_staff or user.is_superuser)


class IsAdminOrPublicReadOnly(BasePermission):
    """
    Public and authenticated users can read (GET, HEAD, OPTIONS) public catalog/leaderboard data.
    Mutating methods require Admin role or is_staff/is_superuser.
    """
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        return bool(
            user and
            user.is_authenticated and
            (getattr(user, 'role', None) == 'admin' or user.is_staff or user.is_superuser)
        )


class IsAdminOrStaffOrReadOnly(BasePermission):
    """
    Public/authenticated read-only for classes/catalog.
    Mutating methods (POST, PUT, PATCH) allowed for Admin or Faculty/Staff.
    """
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        return bool(
            user and
            user.is_authenticated and
            (getattr(user, 'role', None) in ('admin', 'faculty', 'evaluation') or user.is_staff or user.is_superuser)
        )
