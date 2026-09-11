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


class SubcategoryAccessPermission(BasePermission):
    """
    Subcategory-granular permission enforcer for submission creation/update.

    Delegates to ``users.access_rules.validate_subcategory_access()``.

    Requires that:
      - ``request.data`` contains ``criteriaId`` (used to resolve the CriteriaItem)
      - ``request.user`` is authenticated

    Safe methods (GET, HEAD, OPTIONS) are always permitted.
    Students that fail the access check receive HTTP 403 with a descriptive message.
    """

    message = "Access denied at subcategory level."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True

        user = request.user
        if not user or not getattr(user, 'is_authenticated', False):
            return False

        # Staff / admin / superuser bypass subcategory rules entirely
        user_role = getattr(user, 'role', None)
        if (
            user_role in ('admin', 'faculty', 'evaluation')
            or getattr(user, 'is_staff', False)
            or getattr(user, 'is_superuser', False)
        ):
            return True

        # Resolve the CriteriaItem from the request body
        criteria_id_raw = request.data.get('criteriaId')
        if not criteria_id_raw:
            # No criteriaId present — let the view's own validation handle it
            return True

        try:
            criteria_id = int(criteria_id_raw)
        except (ValueError, TypeError):
            return True  # Malformed ID — let the view reject it

        from users.models import CriteriaItem
        criteria_item = CriteriaItem.objects.filter(pk=criteria_id).select_related('category').first()
        if not criteria_item:
            return True  # Non-existent item — let the view produce 404

        evidence = request.data.get('evidence')
        if isinstance(evidence, str):
            import json
            try:
                evidence = json.loads(evidence)
            except Exception:
                evidence = {}

        from users.access_rules import validate_subcategory_access
        allowed, error_msg = validate_subcategory_access(user, criteria_item, evidence)
        if not allowed:
            self.message = error_msg
            return False

        return True


class AuthorizeRound1Verifier(BasePermission):
    """
    Authorization check for Round 1 Verification.
    Grants access to users belonging to 'Student Representatives' or 'DQC Student Rep Group'.
    """
    message = "You do not have Round 1 verification authority."

    def has_permission(self, request, view):
        from rest_framework.exceptions import PermissionDenied

        user = request.user
        if not user or not getattr(user, 'is_authenticated', False):
            return False

        if getattr(user, 'role', '') == 'admin' or getattr(user, 'is_superuser', False):
            return True

        if getattr(user, 'role', '') != 'student':
            raise PermissionDenied(detail={
                "error": "Access Denied",
                "message": "You do not have Round 1 verification authority."
            })

        user_groups = getattr(user, 'user_groups', None)
        if user_groups is None:
            from users.services.user_service import UserService
            user_groups = UserService.get_user_group_names(user)

        from users.services.user_service import UserService
        is_authorized = (
            'Student Representatives' in user_groups or
            'DQC Student Rep Group' in user_groups or
            'grp-student-reps' in user_groups or
            'grp-dqc-student-rep' in user_groups or
            UserService.is_user_student_rep(user) or
            UserService.is_user_dqc_rep(user)
        )
        if not is_authorized:
            raise PermissionDenied(detail={
                "error": "Access Denied",
                "message": "You do not have Round 1 verification authority."
            })
        return True


IsRound1Verifier = AuthorizeRound1Verifier
from users.middleware import authorizeRound1Verifier

