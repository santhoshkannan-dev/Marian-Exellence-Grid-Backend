"""
Round 1 Verification Middleware & Permission Guard
==================================================
Enforces that only users belonging to 'Student Representatives' or
'DQC Student Rep Group' can perform Round 1 verification.
"""

from django.http import JsonResponse
from users.services.user_service import UserService


def authorizeRound1Verifier(req, res=None, next=None):
    """
    Middleware / authorization guard for Round 1 Verification:
    Grants access if req.user has 'Student Representatives' or 'DQC Student Rep Group'.

    Usage:
        is_auth, err_resp = authorizeRound1Verifier(request)
        if not is_auth:
            return JsonResponse(err_resp, status=403)
    """
    user = getattr(req, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        err = {
            "error": "Access Denied",
            "message": "You do not have Round 1 verification authority."
        }
        if res is not None and hasattr(res, 'status'):
            return res.status(403).json(err)
        return False, err

    if getattr(user, 'role', '') == 'admin' or getattr(user, 'is_superuser', False):
        if callable(next):
            return next()
        return True, None

    user_groups = getattr(user, 'user_groups', None)
    if user_groups is None:
        user_groups = UserService.get_user_group_names(user)

    is_authorized = (
        'Student Representatives' in user_groups or
        'DQC Student Rep Group' in user_groups or
        'grp-student-reps' in user_groups or
        'grp-dqc-student-rep' in user_groups or
        UserService.is_user_student_rep(user) or
        UserService.is_user_dqc_rep(user)
    )

    if not is_authorized:
        err = {
            "error": "Access Denied",
            "message": "You do not have Round 1 verification authority."
        }
        if res is not None and hasattr(res, 'status'):
            return res.status(403).json(err)
        return False, err

    if callable(next):
        return next()
    return True, None


class Round1VerificationMiddleware:
    """
    Django Middleware: Authorization check for Round 1 Verification.
    Inspects requests to Round 1 endpoints and blocks unauthorized actors.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info.rstrip('/')
        if path in ('/api/verification/dqc', '/api/verification/round1'):
            if request.method in ('POST', 'PUT', 'PATCH'):
                user = getattr(request, 'user', None)
                if user and getattr(user, 'is_authenticated', False):
                    is_auth, err = authorizeRound1Verifier(request)
                    if not is_auth:
                        return JsonResponse(err, status=403)
        return self.get_response(request)
