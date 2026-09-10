import logging
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from users.models import User, Class, Department
from users.permissions import IsAdminRole
from users.audit import record_system_audit_event
from users.services.user_service import (
    UserService,
    get_tokens_for_user,
    parse_name_from_email,
    allocate_student_from_email,
    determine_role_from_email,
)

logger = logging.getLogger(__name__)

try:
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
except ImportError:
    id_token = None
    google_requests = None


class GoogleLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = 'login'

    def post(self, request):
        token = request.data.get("token")

        if not token:
            return Response(
                {"error": "Google token is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not settings.GOOGLE_CLIENT_ID:
            return Response(
                {"error": "GOOGLE_CLIENT_ID is not configured."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        if not id_token or not google_requests:
            return Response(
                {"error": "google-auth package is missing in server environment."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        try:
            id_info = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID
            )

            email = id_info.get("email")
            google_id = id_info.get("sub")
            full_name = id_info.get("name", "")
            picture = id_info.get("picture")

            if not email:
                return Response(
                    {"error": "Unable to retrieve email."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Strict domain check: Only permit login if email ends with @mariancollege.org
            if not email.endswith("@mariancollege.org"):
                return Response(
                    {"error": "Access denied. Only official Marian College accounts (@mariancollege.org) are permitted to log in."},
                    status=status.HTTP_403_FORBIDDEN
                )

            detected_role = determine_role_from_email(email)

            try:
                user = User.objects.get(email=email)
                if not user.is_active:
                    return Response(
                        {"error": "Account is disabled. Please contact your Administrator."},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except User.DoesNotExist:
                if detected_role == 'student':
                    names = full_name.split(" ", 1) if full_name else [email.split("@")[0], ""]
                    user = User.objects.create(
                        username=email,
                        email=email,
                        first_name=names[0],
                        last_name=names[1] if len(names) > 1 else "",
                        role='student',
                        google_id=google_id
                    )
                else:
                    return Response(
                        {"error": "Access denied. Your email is not registered in the system. Please contact your Administrator."},
                        status=status.HTTP_403_FORBIDDEN
                    )

            if not user.google_id:
                user.google_id = google_id

            if full_name:
                names = full_name.split(" ", 1)
                user.first_name = names[0]
                if len(names) > 1:
                    user.last_name = names[1]

            user.save()
            user = allocate_student_from_email(user)

            tokens = get_tokens_for_user(user)

            return Response(
                {
                    "tokens": tokens,
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "name": user.get_full_name() or user.username,
                        "role": user.role,
                        "department": user.department.name if user.department else None,
                        "department_code": user.department.code if user.department else None,
                        "class_name": user.class_name.name if user.class_name else None,
                        "picture": picture,
                    }
                },
                status=status.HTTP_200_OK
            )

        except ValueError as e:
            logger.warning(f"Google Token Verification Failed: {e}")
            return Response(
                {"error": "Invalid Google token or verification failed."},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception("Google authentication failed")
            return Response(
                {"error": "Google authentication failed. Please try again."},
                status=status.HTTP_400_BAD_REQUEST
            )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
            return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": "Invalid or expired refresh token."}, status=status.HTTP_400_BAD_REQUEST)


class CustomTokenRefreshView(TokenRefreshView):
    """
    Hardened Token Refresh View:
    Verifies that the user account exists and is active (is_active=True).
    Disabled/deactivated user accounts cannot refresh tokens.
    """
    def post(self, request, *args, **kwargs):
        refresh_token_str = request.data.get('refresh')
        if refresh_token_str:
            try:
                token = RefreshToken(refresh_token_str)
                user_id = token.payload.get('user_id')
                if user_id:
                    user = User.objects.filter(id=user_id).first()
                    if user and not user.is_active:
                        return Response(
                            {"detail": "User account is disabled. Please contact your Administrator.", "code": "user_inactive"},
                            status=status.HTTP_401_UNAUTHORIZED
                        )
            except Exception:
                pass
        return super().post(request, *args, **kwargs)


class DevBypassLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = 'login'

    def post(self, request):
        if not (settings.DEBUG and getattr(settings, 'ENABLE_DEV_BYPASS', False)):
            return Response(
                {"error": "Developer bypass login is disabled in this environment."},
                status=status.HTTP_404_NOT_FOUND
            )

        email = request.data.get("email")
        override_role = request.data.get("role")

        if not email:
            return Response(
                {"error": "Email required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not email.endswith("@mariancollege.org"):
            return Response(
                {"error": "Invalid email domain."},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            user = User.objects.get(email=email)
            if not user.is_active:
                return Response(
                    {"error": "Account is disabled. Please contact your Administrator."},
                    status=status.HTTP_403_FORBIDDEN
                )

            if override_role and override_role != user.role:
                if override_role in ('admin', 'iqac', 'faculty', 'evaluation'):
                    return Response(
                        {"error": "Selecting an arbitrary privileged role via development bypass is strictly prohibited."},
                        status=status.HTTP_403_FORBIDDEN
                    )
                user.role = override_role
        except User.DoesNotExist:
            detected_role = determine_role_from_email(email)
            if detected_role == 'student':
                derived_name = parse_name_from_email(email)
                names = derived_name.split(" ", 1)
                user = User.objects.create(
                    username=email,
                    email=email,
                    first_name=names[0],
                    last_name=names[1] if len(names) > 1 else "",
                    role='student'
                )
            else:
                return Response(
                    {"error": "User not found. Privileged accounts (admin/faculty/evaluator) must be provisioned through administrative channels."},
                    status=status.HTTP_404_NOT_FOUND
                )

        user = allocate_student_from_email(user)
        tokens = get_tokens_for_user(user)

        return Response(
            {
                "tokens": tokens,
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "name": user.get_full_name() or user.username,
                    "role": user.role,
                    "department": user.department.name if user.department else None,
                    "department_code": user.department.code if user.department else None,
                    "class_name": user.class_name.name if user.class_name else None,
                }
            }
        )


class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = allocate_student_from_email(request.user)
        return Response(
            {
                "id": user.id,
                "email": user.email,
                "name": user.get_full_name() or user.username,
                "role": user.role,
                "department": user.department.name if user.department else None,
                "department_code": user.department.code if user.department else None,
                "class_name": user.class_name.name if user.class_name else None,
            }
        )

    def put(self, request):
        user = request.user
        name = request.data.get('name')
        class_name_str = request.data.get('class_name')

        if name is not None:
            clean_name = str(name).strip()
            if len(clean_name) > 150:
                return Response(
                    {"error": "Name cannot exceed 150 characters."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if clean_name:
                parts = clean_name.split(' ', 1)
                user.first_name = parts[0]
                if len(parts) > 1:
                    user.last_name = parts[1]
                else:
                    user.last_name = ""

        if class_name_str:
            if getattr(user, 'role', None) == 'student':
                return Response(
                    {"error": "Unauthorized: Students cannot alter their class assignment."},
                    status=status.HTTP_403_FORBIDDEN
                )
            try:
                cls_obj = Class.objects.get(name__iexact=class_name_str)
                user.class_name = cls_obj
                user.department = cls_obj.department
            except Class.DoesNotExist:
                return Response(
                    {"error": f"Class '{class_name_str}' does not exist."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        user.save()

        return Response(
            {
                "id": user.id,
                "email": user.email,
                "name": user.get_full_name() or user.username,
                "role": user.role,
                "department": user.department.name if user.department else None,
                "department_code": user.department.code if user.department else None,
                "class_name": user.class_name.name if user.class_name else None,
            }
        )


class UserManagementView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        users = User.objects.select_related('department', 'class_name').all().order_by('id')
        return Response([
            {
                "id": u.id,
                "name": u.get_full_name() or u.username,
                "email": u.email,
                "role": u.role,
                "department": u.department.name if u.department else None,
                "department_code": u.department.code if u.department else None,
                "className": u.class_name.name if u.class_name else None,
                "isApproved": u.is_active
            }
            for u in users
        ])

    def post(self, request):
        email = request.data.get('email')
        role = request.data.get('role', 'student').lower()
        name = request.data.get('name', '')
        dept_code = request.data.get('department_code')
        class_name_str = request.data.get('class_name')

        if not email:
            return Response({"error": "email is required"}, status=status.HTTP_400_BAD_REQUEST)

        clean_email = str(email).strip().lower()
        import re
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', clean_email) or len(clean_email) > 254:
            return Response({"error": "Invalid email address format."}, status=status.HTTP_400_BAD_REQUEST)

        valid_roles = [c[0] for c in User.ROLE_CHOICES]
        if role not in valid_roles:
            return Response({"error": f"Invalid role '{role}'. Allowed roles: {', '.join(valid_roles)}."}, status=status.HTTP_400_BAD_REQUEST)

        clean_name = str(name).strip()
        if clean_name and len(clean_name) > 150:
            return Response({"error": "name cannot exceed 150 characters."}, status=status.HTTP_400_BAD_REQUEST)

        username = clean_email.split('@')[0]
        dept = Department.objects.filter(code=dept_code).first() if dept_code else None
        cls = Class.objects.filter(name=class_name_str).first() if class_name_str else None

        names = clean_name.split(' ', 1) if clean_name else [username, ""]
        first_name = names[0]
        last_name = names[1] if len(names) > 1 else ""

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": username,
                "role": role,
                "department": dept,
                "class_name": cls,
                "first_name": first_name,
                "last_name": last_name,
                "is_active": True
            }
        )
        old_role = None if created else user.role
        if not created:
            user.role = role
            user.department = dept
            user.class_name = cls
            user.first_name = first_name
            user.last_name = last_name
            user.save()

        record_system_audit_event(
            action='USER_ROLE_CHANGE',
            object_type='User',
            object_id=user.id,
            actor=request.user,
            object_repr=f"User {user.email} (Role: {user.role})",
            old_value={'role': old_role} if not created else None,
            new_value={'role': user.role, 'email': user.email, 'is_active': user.is_active},
            reason=f"User {'created' if created else 'updated'} via UserManagementView by {getattr(request.user, 'email', '')}",
            request=request
        )

        return Response({
            "id": user.id,
            "email": user.email,
            "name": user.get_full_name() or user.username,
            "role": user.role,
            "department": user.department.name if user.department else None,
            "className": user.class_name.name if user.class_name else None,
        })

    def delete(self, request):
        user_id = request.data.get('id')
        if not user_id:
            return Response({"error": "User id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(id=user_id)
            user.delete()
            return Response({"success": True})
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
