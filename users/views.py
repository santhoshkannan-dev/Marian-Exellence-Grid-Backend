
from .models import Champion, BugReport, SystemAuditLog, WorkflowAuditTrail
from .serializers import (
    ChampionSerializer, BugReportSerializer, BugReportSafeSerializer,
    SubmissionSerializer, AcademicGradeBreakdownSerializer,
    CriteriaCategorySerializer, CriteriaItemSerializer, CriteriaRuleSerializer, CriteriaVersionSerializer,
    SystemAuditLogSerializer, WorkflowAuditTrailSerializer
)
from .audit import record_system_audit_event
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
import os
import logging
import hashlib
from datetime import datetime
from django.conf import settings
from django.http import FileResponse
from .file_security import (
    validate_file_upload,
    save_private_evidence_file,
    resolve_safe_private_path,
    ALLOWED_EVIDENCE_EXTENSIONS,
    ALLOWED_IMAGE_EXTENSIONS,
    MAX_EVIDENCE_SIZE_BYTES,
    MAX_IMAGE_SIZE_BYTES,
)

logger = logging.getLogger(__name__)

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from .permissions import (
    IsAdminRole,
    IsAdminOrIQAC,
    IsStaffOrAdmin,
    IsAdminOrReadOnly,
    IsAdminOrPublicReadOnly,
    IsAdminOrStaffOrReadOnly,
)

from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

try:
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
except ImportError:
    id_token = None
    google_requests = None

from django.db import transaction
from django.db.models import Q, Sum
from .models import User, Class, Department, Course, Submission, AcademicYear, SystemSetting, UserGroupModel, CriteriaCategory, CriteriaItem, CriteriaRule, CriteriaVersion, AcademicGradeBreakdown


VALID_STATE_TRANSITIONS = {
    'Draft': ['Submitted', 'Pending Rep Verification', 'Pending Verification', 'Pending', 'Draft'],
    'Submitted': ['Student Rep Verified', 'Teacher Verified', 'Correction Requested', 'Correction', 'Rejected', 'Submitted'],
    'Pending Rep Verification': ['Student Rep Verified', 'Teacher Verified', 'Correction Requested', 'Correction', 'Rejected', 'Pending Rep Verification'],
    'Pending Verification': ['Student Rep Verified', 'Teacher Verified', 'Correction Requested', 'Correction', 'Rejected', 'Pending Verification'],
    'Pending': ['Student Rep Verified', 'Teacher Verified', 'Correction Requested', 'Correction', 'Rejected', 'Pending'],
    'Student Rep Verified': ['Teacher Verified', 'Evaluated', 'Approved', 'Verified', 'Correction Requested', 'Correction', 'Rejected', 'Student Rep Verified'],
    'Teacher Verified': ['Evaluated', 'Approved', 'Verified', 'Locked', 'Correction Requested', 'Correction', 'Rejected', 'Teacher Verified'],
    'Correction Requested': ['Submitted', 'Pending Rep Verification', 'Pending Verification', 'Draft', 'Correction Requested'],
    'Correction': ['Submitted', 'Pending Rep Verification', 'Pending Verification', 'Draft', 'Correction'],
    'Evaluated': ['Locked', 'Evaluated', 'Correction Requested', 'Correction', 'Rejected'],
    'Approved': ['Evaluated', 'Locked', 'Verified', 'Teacher Verified', 'Correction Requested', 'Correction', 'Rejected', 'Approved'],
    'Verified': ['Evaluated', 'Locked', 'Approved', 'Teacher Verified', 'Correction Requested', 'Correction', 'Rejected', 'Verified'],
    'Rejected': ['Draft', 'Rejected'],
    'Locked': [] # Locked is terminal! Cannot transition to any state.
}

UNEDITABLE_BY_STUDENT_STATES = (
    'Student Rep Verified',
    'Teacher Verified',
    'Evaluated',
    'Approved',
    'Verified',
    'Locked'
)


def get_client_ip(request):
    if not request:
        return None
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def create_audit_entry(submission, actor, stage, stage_name, prev_status, new_status, comments, request=None):
    try:
        from users.models import WorkflowAuditTrail
        ip_addr = get_client_ip(request) if request else None
        user_agent = request.META.get('HTTP_USER_AGENT') if request else None
        req_id = request.META.get('HTTP_X_REQUEST_ID') if request else None

        WorkflowAuditTrail.objects.create(
            submission=submission,
            actor=actor if actor and actor.is_authenticated else None,
            stage=stage,
            stage_name=stage_name,
            previous_status=prev_status,
            new_status=new_status,
            comments=comments or "",
            ip_address=ip_addr,
            user_agent=user_agent,
            request_id=req_id
        )
    except Exception as e:
        logger.warning(f"Failed to create audit entry for submission #{submission.id}: {e}")


def get_online_courses_item_ids():
    items = CriteriaItem.objects.filter(
        Q(category__code__iexact='cat-online-courses') |
        Q(category__category__icontains='online course')
    )
    ids = set(items.values_list('id', flat=True))
    ids.update([201, 202, 203])
    return ids


def get_upsc_psc_item_ids():
    items = CriteriaItem.objects.filter(
        Q(title__icontains='UPSC') |
        Q(title__icontains='PSC') |
        Q(title__icontains='Participation in Relevant Exam')
    )
    ids = set(items.values_list('id', flat=True))
    ids.add(404)
    return ids


def is_user_student_rep(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_student_rep', False) or getattr(user, 'is_dqc_member', False):
        return True
    if Class.objects.filter(dqc_member=user).exists():
        return True
    user_email = (getattr(user, 'email', '') or '').strip().lower()
    if user_email:
        if Class.objects.filter(dqc_member__email__iexact=user_email).exists():
            return True
        rep_group = UserGroupModel.objects.filter(
            Q(group_id='grp-student-reps') | Q(name__icontains='student rep') | Q(name__icontains='dqc')
        ).first()
        if rep_group and rep_group.members and any(isinstance(e, str) and e.strip().lower() == user_email for e in rep_group.members):
            return True
    return False


def check_duplicate_submission(user, criteria_id, academic_year, certificate_id, proof_hash, description, submission_id=None):
    if not user:
        return None

    # 1. Certificate ID / Event ID Match
    if certificate_id and str(certificate_id).strip():
        cert_clean = str(certificate_id).strip()
        qs = Submission.objects.filter(user=user, certificate_id__iexact=cert_clean).exclude(status='Rejected')
        if submission_id:
            qs = qs.exclude(id=submission_id)
        if qs.exists():
            return f"⚠️ Duplicate detected: Certificate / Identifier '{cert_clean}' has already been submitted for evaluation."

    # 2. SHA-256 Proof File Hash Match
    if proof_hash and str(proof_hash).strip():
        p_clean = str(proof_hash).strip()
        qs = Submission.objects.filter(user=user, proof_hash=p_clean).exclude(status='Rejected')
        if submission_id:
            qs = qs.exclude(id=submission_id)
        if qs.exists():
            return "⚠️ Duplicate detected: An identical proof document has already been submitted for evaluation."

    # 3. Exact Criteria + Description Activity Fingerprint Match
    if description and str(description).strip() and criteria_id:
        desc_clean = str(description).strip()
        try:
            c_id = int(criteria_id)
        except (ValueError, TypeError):
            c_id = abs(int(hashlib.md5(str(criteria_id).encode()).hexdigest(), 16)) % 1000000

        qs = Submission.objects.filter(
            user=user,
            criteria_id=c_id,
            academic_year=academic_year,
            description__iexact=desc_clean
        ).exclude(status='Rejected')
        if submission_id:
            qs = qs.exclude(id=submission_id)
        if qs.exists():
            return f"⚠️ Duplicate detected: A submission with identical activity description has already been submitted for this criteria in {academic_year}."

    return None


def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
    }


def parse_name_from_email(email):
    """
    Dynamically derives user name from email local part for any email.
    e.g. amal.thomas.25pmc114@mariancollege.org -> 'Amal Thomas'
    e.g. kochumol.abraham@mariancollege.org -> 'Kochumol Abraham'
    e.g. amal.25pmc114@mariancollege.org -> 'Amal'
    """
    if not email or '@' not in email:
        return "User"
    local_part = email.split('@')[0]
    parts = local_part.split('.')
    name_parts = []
    for part in parts:
        if any(char.isdigit() for char in part):
            break
        name_parts.append(part.capitalize())
    if name_parts:
        return " ".join(name_parts)
    return parts[0].capitalize()


def parse_email_code(email):
    """
    Parses the raw code segment from a Marian College student email.
    Returns dict with level_char, email_code, batch_year, roll_digits, roll_number, section_hint
    e.g. amal.25pmc114@mariancollege.org ->
         level_char='p', email_code='mc', batch_year=2025, roll_digits='114', roll_number=14
    """
    if not email or '@' not in email:
        return None
    local_part = email.split('@')[0]
    parts = local_part.split('.')
    if len(parts) < 2:
        return None

    # The code segment is the last part that starts with digits
    code_part = None
    for p in reversed(parts):
        if len(p) >= 5 and p[:2].isdigit():
            code_part = p
            break
    if not code_part:
        return None

    batch_year = 2000 + int(code_part[:2])
    level_char = code_part[2].lower()       # 'p' or 'u'
    email_code = code_part[3:5].lower()     # 'mc', 'bc' etc.
    roll_digits = code_part[5:]              # '114', '214'

    # Derive section hint from roll series (100-series -> A, 200-series -> B ...)
    section_hint = ''
    roll_number = None
    if roll_digits.isdigit():
        roll_num = int(roll_digits)
        roll_number = roll_num % 100       # actual roll: last two digits
        series = roll_num // 100
        section_map = {1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'E', 6: 'F'}
        section_hint = section_map.get(series, 'A')

    return {
        'level_char': level_char,
        'email_code': email_code,
        'batch_year': batch_year,
        'roll_digits': roll_digits,
        'roll_number': roll_number,
        'section_hint': section_hint,
    }


def get_active_year_start():
    """
    Returns the starting year integer of the current active AcademicYear.
    e.g. if AcademicYear.year == '2026-2027', returns 2026.
    Falls back to current calendar year if none is active.
    """
    try:
        active = AcademicYear.objects.filter(is_active=True).order_by('-year').first()
        if active and active.year:
            return int(active.year.split('-')[0])
    except (ValueError, IndexError, Exception):
        pass
    return datetime.now().year


def get_year_roman(year_number):
    mapping = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI'}
    return mapping.get(year_number, str(year_number))


def parse_student_email(email):
    """
    Parses Marian College student email format using DB Course/Department lookup.
    Returns a dict with resolved department, class name, batch_year, roll_number etc.
    Falls back to basic inference if no matching Course found in DB.
    e.g. amal.25pmc114@mariancollege.org  ->  {class_name: 'II MCA', ...}
    e.g. santhosh.25ubc214@mariancollege.org -> {class_name: 'II BCA B', ...}
    """
    parsed_code = parse_email_code(email)
    if not parsed_code:
        return None

    level_char = parsed_code['level_char']
    email_code = parsed_code['email_code']
    batch_year = parsed_code['batch_year']
    roll_number = parsed_code['roll_number']
    roll_digits = parsed_code['roll_digits']
    section_hint = parsed_code['section_hint']

    # Calculate year-in-course from active academic year
    active_year_start = get_active_year_start()
    year_number = active_year_start - batch_year + 1
    year_roman = get_year_roman(year_number)

    # --- Try DB-driven resolution first ---
    try:
        course = Course.objects.select_related('department').get(
            email_code=email_code,
            department__email_prefix=level_char
        )
        dept_obj = course.department
        section = section_hint if course.is_multi_batch else ''
        class_name = f"{year_roman} {course.abbreviation} {section}".strip() if section else f"{year_roman} {course.abbreviation}"
        return {
            'first_name': parse_name_from_email(email),
            'batch_year': batch_year,
            'year_number': year_number,
            'level': 'Postgraduate' if level_char == 'p' else 'Undergraduate',
            'course': course,
            'course_name': course.abbreviation,
            'department_name': dept_obj.name,
            'department_code': dept_obj.code,
            'department_obj': dept_obj,
            'section': section,
            'class_name': class_name,
            'roll_number': roll_number,
            'roll_digits': roll_digits,
            'db_resolved': True,
        }
    except Course.DoesNotExist:
        pass

    # --- Fallback: infer from hardcoded map (no DB course registered yet) ---
    course_map = {
        'mc': ('Master of Computer Applications', 'MCA', 'PGDCA'),
        'bc': ('Bachelor of Computer Applications', 'BCA', 'UGDCA'),
        'ba': ('Bachelor of Business Administration', 'BBA', 'UGDBA'),
        'cm': ('Commerce', 'BCom', 'UGCOM'),
        'sw': ('Social Work', 'MSW', 'PGSW'),
    }
    if email_code in course_map:
        full_name, abbr, dept_code = course_map[email_code]
        section = section_hint if level_char == 'u' else ''
        class_name = f"{year_roman} {abbr} {section}".strip() if section else f"{year_roman} {abbr}"
        return {
            'first_name': parse_name_from_email(email),
            'batch_year': batch_year,
            'year_number': year_number,
            'level': 'Postgraduate' if level_char == 'p' else 'Undergraduate',
            'course': None,
            'course_name': abbr,
            'department_name': full_name,
            'department_code': dept_code,
            'department_obj': None,
            'section': section,
            'class_name': class_name,
            'roll_number': roll_number,
            'roll_digits': roll_digits,
            'db_resolved': False,
        }

    return None


def allocate_student_from_email(user):
    """
    Allocates student user to the resolved Department and Class objects.
    Uses active AcademicYear + DB Course lookup for accurate class resolution.
    Stores roll_number and batch_year on the user.
    """
    if user.role != 'student' and determine_role_from_email(user.email) != 'student':
        # Faculty: sync class_name from class_teacher assignment
        advisor_class = Class.objects.filter(class_teacher=user).first()
        if advisor_class:
            if user.class_name != advisor_class or user.department != advisor_class.department:
                user.class_name = advisor_class
                user.department = advisor_class.department
                user.save(update_fields=['class_name', 'department'])
        elif user.class_name:
            if not Class.objects.filter(class_teacher=user).exists():
                user.class_name = None
                user.save(update_fields=['class_name'])
        return user

    parsed = parse_student_email(user.email)
    if not parsed:
        return user

    update_fields = set(['department', 'class_name', 'roll_number', 'batch_year'])

    # Store roll_number and batch_year
    user.roll_number = parsed.get('roll_number')
    user.batch_year = parsed.get('batch_year')

    if not user.first_name or user.first_name == user.username:
        derived_name = parse_name_from_email(user.email)
        name_parts = derived_name.split(" ", 1)
        user.first_name = name_parts[0]
        update_fields.add('first_name')
        if len(name_parts) > 1:
            user.last_name = name_parts[1]
            update_fields.add('last_name')

    # DB-driven path: Course was found in DB
    if parsed.get('db_resolved') and parsed.get('department_obj'):
        dept_obj = parsed['department_obj']
        course = parsed['course']
        year_number = parsed['year_number']
        section = parsed.get('section', '')
        batch_year = parsed.get('batch_year')
        class_name = parsed['class_name']

        class_obj, created = Class.objects.get_or_create(
            course=course,
            year_number=year_number,
            section=section,
            defaults={
                'name': class_name,
                'department': dept_obj,
            }
        )
        if created or class_obj.department != dept_obj:
            class_obj.department = dept_obj
            class_obj.save()

        user.department = dept_obj
        user.class_name = class_obj

    else:
        # Fallback path: no DB course found — use lookup with inferred dept/class safely
        dept_code = parsed['department_code']
        dept_name = parsed['department_name']
        class_name = parsed['class_name']

        dept_obj = Department.objects.filter(code=dept_code).first() or Department.objects.filter(name=dept_name).first()
        if not dept_obj:
            try:
                dept_obj, _ = Department.objects.get_or_create(
                    code=dept_code,
                    defaults={'name': dept_name}
                )
            except Exception:
                dept_obj = Department.objects.filter(name=dept_name).first() or Department.objects.filter(code=dept_code).first()

        class_obj = Class.objects.filter(name=class_name).first()
        if not class_obj:
            class_obj = Class.objects.create(name=class_name, department=dept_obj)
        elif dept_obj and class_obj.department != dept_obj:
            class_obj.department = dept_obj
            class_obj.save(update_fields=['department'])

        user.department = dept_obj
        user.class_name = class_obj

    user.save(update_fields=list(update_fields))
    return user


def determine_role_from_email(email):
    """
    Determines user role based on Marian College email format:
    - name.number (e.g. santhosh.25pmc152, amal.25pmc114) -> student
    - name.name (e.g. kochumol.abraham) -> faculty (staff)
    """
    username_part = email.split('@')[0]
    parts = username_part.split('.')
    if len(parts) >= 2:
        second_part = parts[1]
        if any(char.isdigit() for char in second_part):
            return "student"
        else:
            return "faculty"  # Staff ID
    return "student"


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

            # Store google_id and other details on first-time login
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
            
            # Security: A user must never be able to select an arbitrary privileged role
            if override_role and override_role != user.role:
                if override_role in ('admin', 'iqac', 'faculty', 'evaluation'):
                    return Response(
                        {"error": "Selecting an arbitrary privileged role via development bypass is strictly prohibited."},
                        status=status.HTTP_403_FORBIDDEN
                    )
                # Only non-privileged role switching (e.g. testing student role variations) without mutating DB
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


class AcademicYearListView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]

    def get(self, request):
        years = AcademicYear.objects.all().order_by('-year')
        return Response([
            {"year": y.year, "status": "Active" if y.is_active else "Inactive"}
            for y in years
        ])

    def post(self, request):
        year_str = request.data.get('year')
        is_active = request.data.get('status') == 'Active' or request.data.get('is_active') == True

        if not year_str:
            return Response({"error": "year is required"}, status=status.HTTP_400_BAD_REQUEST)

        year_str = str(year_str).strip()
        import re
        if not re.match(r'^\d{4}-\d{4}$', year_str) or len(year_str) > 20:
            return Response(
                {"error": "Academic year must be in format 'YYYY-YYYY' (e.g. '2025-2026') and cannot exceed 20 characters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            ay, created = AcademicYear.objects.get_or_create(year=year_str)
            if is_active:
                AcademicYear.objects.exclude(pk=ay.pk).update(is_active=False)
                ay.is_active = True
                ay.save()
        
        return Response({"year": ay.year, "status": "Active" if ay.is_active else "Inactive"})

    def put(self, request):
        year_str = request.data.get('year')
        is_active = request.data.get('is_active', True)
        if isinstance(is_active, str):
            is_active = is_active.lower() == 'true' or is_active.lower() == 'active'

        if not year_str:
            return Response({"error": "year is required"}, status=status.HTTP_400_BAD_REQUEST)

        year_str = str(year_str).strip()
        import re
        if not re.match(r'^\d{4}-\d{4}$', year_str) or len(year_str) > 20:
            return Response(
                {"error": "Academic year must be in format 'YYYY-YYYY' (e.g. '2025-2026') and cannot exceed 20 characters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            try:
                ay = AcademicYear.objects.select_for_update().get(year=year_str)
            except AcademicYear.DoesNotExist:
                ay = AcademicYear.objects.create(year=year_str, is_active=is_active)

            if is_active:
                AcademicYear.objects.exclude(pk=ay.pk).update(is_active=False)
                ay.is_active = True
                ay.save()
            else:
                ay.is_active = False
                ay.save()

        return Response({"year": ay.year, "status": "Active" if ay.is_active else "Inactive"})

    def delete(self, request):
        year_str = request.data.get('year') or request.query_params.get('year')
        if not year_str:
            return Response({"error": "year is required"}, status=status.HTTP_400_BAD_REQUEST)

        AcademicYear.objects.filter(year=year_str).delete()
        return Response({"success": True, "deleted_year": year_str}, status=status.HTTP_200_OK)

OFFICIAL_DEPT_ORDER = [
    'UGDCA',
    'PGDCA',
    'COMMERCE',
    'BBA_MBA',
    'SOCIAL_WORK',
    'PHYSICS',
    'ECONOMICS',
    'MATHS',
    'BACE',
    'MCMS',
    'MHTM',
    'PSYCHOLOGY',
    'IQAC',
    'ADMIN'
]

OFFICIAL_CLASS_ORDER = [
    # 1. Department of Computer Applications
    "I BCA A", "I BCA B", "II BCA A", "II BCA B", "III BCA A", "III BCA B", "I MCA", "II MCA",
    # 2. Department of Commerce
    "I BCOM A", "I BCOM B", "I BCOM C", "I BCOM (FINTECH)", "II BCOM A", "II BCOM B", "II BCOM C", "III BCOM A", "III BCOM B", "III BCOM C", "I MCOM A", "I MCOM B", "II MCOM A", "II MCOM B",
    # 3. Department of Business Administration
    "I BBA A", "I BBA B", "II BBA A", "II BBA B", "III BBA A", "III BBA B", "I MBA A", "I MBA B", "I MBA C", "II MBA A", "II MBA B", "II MBA C",
    # 4. Department of Social Work
    "I BSW A", "I BSW B", "II BSW A", "II BSW B", "III BSW A", "III BSW B", "I MSW", "II MSW",
    # 5. Department of Physics
    "I MSC PHYSICS", "II MSC PHYSICS", "III MSC PHYSICS", "IV MSC PHYSICS", "V MSC PHYSICS",
    # 6. Department of Economics
    "I ECONOMICS", "II ECONOMICS", "III ECONOMICS",
    # 7. Department of Mathematics
    "I MATHS", "II MATHS", "III MATHS",
    # 8. Department of English / Communicative English
    "I BACE", "II BACE", "III BACE",
    # 9. Department of Communication & Media Studies
    "I MCMS", "II MCMS",
    # 10. Department of Hospitality & Tourism Management
    "I MHTM", "II MHTM",
    # 11. Department of Psychology
    "I PSYCHOLOGY"
]

class DepartmentListView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]

    def get(self, request):
        depts = Department.objects.prefetch_related('courses', 'classes').all()
        from .serializers import DepartmentSerializer
        return Response(DepartmentSerializer(depts, many=True).data)

    def post(self, request):
        name = request.data.get('name')
        code = request.data.get('code')
        email_prefix = request.data.get('email_prefix', '').strip().lower()
        level = request.data.get('level', 'UG')
        if not name:
            return Response({"error": "name is required"}, status=status.HTTP_400_BAD_REQUEST)
        clean_name = str(name).strip()
        if len(clean_name) > 100:
            return Response({"error": "name cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if not code:
            code = ''.join([w[0] for w in clean_name.split()]).upper()[:5] or "DEPT"
        clean_code = str(code).strip().upper()
        if len(clean_code) > 20:
            return Response({"error": "code cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if len(email_prefix) > 5:
            return Response({"error": "email_prefix cannot exceed 5 characters."}, status=status.HTTP_400_BAD_REQUEST)
        valid_levels = [c[0] for c in Department.LEVEL_CHOICES]
        if level not in valid_levels:
            return Response({"error": f"Invalid level '{level}'. Allowed choices: {', '.join(valid_levels)}."}, status=status.HTTP_400_BAD_REQUEST)

        dept, created = Department.objects.get_or_create(
            code=clean_code,
            defaults={"name": clean_name, "email_prefix": email_prefix, "level": level}
        )
        if not created:
            dept.name = clean_name
            dept.email_prefix = email_prefix
            dept.level = level
            dept.save(update_fields=['name', 'email_prefix', 'level'])
        from .serializers import DepartmentSerializer
        return Response(DepartmentSerializer(dept).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class DepartmentDetailView(APIView):
    """GET / PUT / DELETE a single Department by its integer pk."""
    permission_classes = [IsAdminOrPublicReadOnly]

    def _get_dept(self, pk):
        try:
            return Department.objects.prefetch_related('courses', 'classes').get(pk=pk)
        except Department.DoesNotExist:
            return None

    def get(self, request, pk):
        dept = self._get_dept(pk)
        if not dept:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)
        from .serializers import DepartmentSerializer
        return Response(DepartmentSerializer(dept).data)

    def put(self, request, pk):
        dept = self._get_dept(pk)
        if not dept:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)
        name = request.data.get('name', dept.name)
        code = request.data.get('code', dept.code)
        email_prefix = request.data.get('email_prefix', dept.email_prefix).strip().lower()
        level = request.data.get('level', dept.level)

        clean_name = str(name).strip()
        if not clean_name or len(clean_name) > 100:
            return Response({"error": "name is required and cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
        clean_code = str(code).strip().upper()
        if not clean_code or len(clean_code) > 20:
            return Response({"error": "code is required and cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if len(email_prefix) > 5:
            return Response({"error": "email_prefix cannot exceed 5 characters."}, status=status.HTTP_400_BAD_REQUEST)
        valid_levels = [c[0] for c in Department.LEVEL_CHOICES]
        if level not in valid_levels:
            return Response({"error": f"Invalid level '{level}'. Allowed choices: {', '.join(valid_levels)}."}, status=status.HTTP_400_BAD_REQUEST)

        dept.name = clean_name
        dept.code = clean_code
        dept.email_prefix = email_prefix
        dept.level = level
        dept.save()
        from .serializers import DepartmentSerializer
        return Response(DepartmentSerializer(dept).data)

    def delete(self, request, pk):
        dept = self._get_dept(pk)
        if not dept:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)
        course_count = dept.courses.count()
        class_count = dept.classes.count()
        dept.delete()
        return Response({"success": True, "deleted_courses": course_count, "deleted_classes": class_count})


class CourseListView(APIView):
    """List all courses, or create a new course under a department."""
    permission_classes = [IsAdminOrPublicReadOnly]

    def get(self, request):
        dept_id = request.query_params.get('department')
        qs = Course.objects.select_related('department').all()
        if dept_id:
            qs = qs.filter(department_id=dept_id)
        from .serializers import CourseSerializer
        return Response(CourseSerializer(qs, many=True).data)

    def post(self, request):
        dept_id = request.data.get('department')
        name = request.data.get('name', '').strip()
        abbreviation = request.data.get('abbreviation', '').strip().upper()
        email_code = request.data.get('email_code', '').strip().lower()
        is_multi_batch = request.data.get('is_multi_batch', False)
        duration_years = request.data.get('duration_years', 2)

        if not dept_id or not name or not abbreviation or not email_code:
            return Response(
                {"error": "department, name, abbreviation, and email_code are required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if len(name) > 150:
            return Response({"error": "name cannot exceed 150 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if len(abbreviation) > 20:
            return Response({"error": "abbreviation cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if len(email_code) > 10:
            return Response({"error": "email_code cannot exceed 10 characters."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            dur_years = int(duration_years)
            if dur_years < 1 or dur_years > 6:
                return Response({"error": "duration_years must be between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, TypeError):
            return Response({"error": "duration_years must be an integer between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            dept = Department.objects.get(pk=dept_id)
        except Department.DoesNotExist:
            return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)

        if Course.objects.filter(department=dept, email_code=email_code).exists():
            return Response(
                {"error": f"A course with email_code '{email_code}' already exists in this department."},
                status=status.HTTP_400_BAD_REQUEST
            )

        course = Course.objects.create(
            department=dept,
            name=name,
            abbreviation=abbreviation,
            email_code=email_code,
            is_multi_batch=bool(is_multi_batch),
            duration_years=dur_years,
        )
        from .serializers import CourseSerializer
        return Response(CourseSerializer(course).data, status=status.HTTP_201_CREATED)


class CourseDetailView(APIView):
    """GET / PUT / DELETE a single Course by pk."""
    permission_classes = [IsAdminOrPublicReadOnly]

    def _get_course(self, pk):
        try:
            return Course.objects.select_related('department').get(pk=pk)
        except Course.DoesNotExist:
            return None

    def get(self, request, pk):
        course = self._get_course(pk)
        if not course:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)
        from .serializers import CourseSerializer
        return Response(CourseSerializer(course).data)

    def put(self, request, pk):
        course = self._get_course(pk)
        if not course:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)

        name = request.data.get('name', course.name)
        abbreviation = request.data.get('abbreviation', course.abbreviation)
        email_code = request.data.get('email_code', course.email_code)
        if name and len(str(name).strip()) > 150:
            return Response({"error": "name cannot exceed 150 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if abbreviation and len(str(abbreviation).strip()) > 20:
            return Response({"error": "abbreviation cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if email_code and len(str(email_code).strip()) > 10:
            return Response({"error": "email_code cannot exceed 10 characters."}, status=status.HTTP_400_BAD_REQUEST)

        if 'duration_years' in request.data:
            try:
                dur_years = int(request.data['duration_years'])
                if dur_years < 1 or dur_years > 6:
                    return Response({"error": "duration_years must be between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
                course.duration_years = dur_years
            except (ValueError, TypeError):
                return Response({"error": "duration_years must be an integer between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)

        course.name = str(name).strip()
        course.abbreviation = str(abbreviation).strip().upper()
        course.email_code = str(email_code).strip().lower()
        course.is_multi_batch = request.data.get('is_multi_batch', course.is_multi_batch)
        if 'department' in request.data:
            try:
                course.department = Department.objects.get(pk=request.data['department'])
            except Department.DoesNotExist:
                return Response({"error": "Department not found"}, status=status.HTTP_404_NOT_FOUND)
        course.save()
        from .serializers import CourseSerializer
        return Response(CourseSerializer(course).data)

    def delete(self, request, pk):
        course = self._get_course(pk)
        if not course:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)
        class_count = course.classes.count()
        course.delete()
        return Response({"success": True, "deleted_classes": class_count})


class ClassListView(APIView):
    permission_classes = [IsAdminOrStaffOrReadOnly]

    def get(self, request):
        dept_id = request.query_params.get('department')
        course_id = request.query_params.get('course')
        qs = Class.objects.select_related('department', 'course', 'class_teacher', 'dqc_member').all()
        if dept_id:
            qs = qs.filter(department_id=dept_id)
        if course_id:
            qs = qs.filter(course_id=course_id)
        classes = list(qs)

        def class_sort_key(c):
            dept_idx = 999
            if c.department and c.department.code in OFFICIAL_DEPT_ORDER:
                dept_idx = OFFICIAL_DEPT_ORDER.index(c.department.code)
            class_idx = 999
            if c.name in OFFICIAL_CLASS_ORDER:
                class_idx = OFFICIAL_CLASS_ORDER.index(c.name)
            return (dept_idx, class_idx)

        classes.sort(key=class_sort_key)
        user = request.user
        is_staff_or_admin = bool(
            user and getattr(user, 'is_authenticated', False) and (
                getattr(user, 'role', '') in ('admin', 'iqac', 'faculty') or
                getattr(user, 'is_staff', False) or
                getattr(user, 'is_superuser', False)
            )
        )
        return Response([
            {
                "id": c.id,
                "name": c.name,
                "department": c.department.name,
                "department_code": c.department.code,
                "course": c.course.id if c.course else None,
                "course_name": c.course.name if c.course else None,
                "course_abbreviation": c.course.abbreviation if c.course else None,
                "year_number": c.year_number,
                "section": c.section,
                "batch_start_year": c.batch_start_year,
                "classTeacher": (c.class_teacher.email if is_staff_or_admin else None) if c.class_teacher else None,
                "classTeacherName": c.class_teacher.get_full_name() or c.class_teacher.username if c.class_teacher else None,
                "dqcMember": (c.dqc_member.email if is_staff_or_admin else None) if c.dqc_member else None,
                "dqcMemberName": c.dqc_member.get_full_name() or c.dqc_member.username if c.dqc_member else None,
                "num_students": c.num_students,
                "negative_points": c.negative_points,
            }
            for c in classes
        ])

    def post(self, request):
        """Create a new class. Accepts either dept_code (legacy) or course_id + year_number + section."""
        user = request.user
        if not (user and user.is_authenticated and (getattr(user, 'role', None) in ('admin', 'iqac') or user.is_staff or user.is_superuser)):
            return Response(
                {"error": "Unauthorized: Only administrators and IQAC coordinators can create classes."},
                status=status.HTTP_403_FORBIDDEN
            )
        course_id = request.data.get('course_id')
        dept_code = request.data.get('department_code')
        name = request.data.get('name', '').strip()
        year_number = request.data.get('year_number')
        section = request.data.get('section', '').strip().upper()
        batch_start_year = request.data.get('batch_start_year')

        if course_id:
            try:
                course = Course.objects.select_related('department').get(pk=course_id)
            except Course.DoesNotExist:
                return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)
            dept = course.department
            if not year_number:
                return Response({"error": "year_number is required when course_id is provided"}, status=status.HTTP_400_BAD_REQUEST)
            year_number = int(year_number)
            roman_map = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI'}
            year_roman = roman_map.get(year_number, str(year_number))
            if section:
                generated_name = f"{year_roman} {course.abbreviation} {section}"
            else:
                generated_name = f"{year_roman} {course.abbreviation}"
            name = name or generated_name

            if Class.objects.filter(course=course, year_number=year_number, section=section).exists():
                return Response(
                    {"error": f"Class '{name}' already exists for this course."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            cls = Class.objects.create(
                name=name,
                department=dept,
                course=course,
                year_number=year_number,
                section=section,
                batch_start_year=int(batch_start_year) if batch_start_year else None,
            )
        else:
            # Legacy: dept_code + name only
            if not name or not dept_code:
                return Response({"error": "name and department_code are required"}, status=status.HTTP_400_BAD_REQUEST)
            try:
                dept = Department.objects.get(code=dept_code)
            except Department.DoesNotExist:
                return Response({"error": f"Department '{dept_code}' not found"}, status=status.HTTP_404_NOT_FOUND)
            cls, _ = Class.objects.get_or_create(name=name, defaults={"department": dept})

        return Response({
            "id": cls.id,
            "name": cls.name,
            "department": cls.department.name,
            "department_code": cls.department.code,
            "course": cls.course.id if cls.course else None,
            "year_number": cls.year_number,
            "section": cls.section,
            "batch_start_year": cls.batch_start_year,
        }, status=status.HTTP_201_CREATED)


    def put(self, request):
        user = request.user
        user_role = getattr(user, 'role', None)
        if user_role in ('student', 'evaluation'):
            return Response(
                {"error": "Unauthorized: You do not have permission to modify classes."},
                status=status.HTTP_403_FORBIDDEN
            )
        name = request.data.get('name')
        teacher_email = request.data.get('classTeacher')
        dqc_email = request.data.get('dqcMember')

        if not name:
            return Response({"error": "Class name is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cls = Class.objects.get(name=name)
        except Class.DoesNotExist:
            return Response({"error": f"Class '{name}' not found"}, status=status.HTTP_404_NOT_FOUND)

        if user_role == 'faculty':
            is_own_class = (cls.class_teacher_id == user.id)
            is_claiming_unassigned = (cls.class_teacher is None and teacher_email == user.email)
            if not (is_own_class or is_claiming_unassigned):
                return Response(
                    {"error": "Unauthorized: Faculty can only manage their own advised class."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if teacher_email is not None and teacher_email != (cls.class_teacher.email if cls.class_teacher else user.email):
                return Response(
                    {"error": "Unauthorized: Only administrators can reassign class advisors."},
                    status=status.HTTP_403_FORBIDDEN
                )

        if teacher_email is not None:
            if teacher_email == "":
                if cls.class_teacher:
                    old_teacher = cls.class_teacher
                    cls.class_teacher = None
                    if not Class.objects.filter(class_teacher=old_teacher).exclude(id=cls.id).exists():
                        old_teacher.class_name = None
                        old_teacher.save(update_fields=['class_name'])
            else:
                try:
                    teacher = User.objects.get(email=teacher_email)
                    # Exclusivity constraint: Cannot be assigned to another class
                    other_class = Class.objects.filter(class_teacher=teacher).exclude(id=cls.id).first()
                    if other_class:
                        return Response({
                            "error": f"Faculty '{teacher.get_full_name() or teacher_email}' is already assigned as Class Advisor to '{other_class.name}'."
                        }, status=status.HTTP_400_BAD_REQUEST)

                    if cls.class_teacher and cls.class_teacher != teacher:
                        old_teacher = cls.class_teacher
                        if not Class.objects.filter(class_teacher=old_teacher).exclude(id=cls.id).exists():
                            old_teacher.class_name = None
                            old_teacher.save(update_fields=['class_name'])

                    cls.class_teacher = teacher
                    teacher.class_name = cls
                    teacher.department = cls.department
                    teacher.save(update_fields=['class_name', 'department'])
                except User.DoesNotExist:
                    return Response({"error": f"Teacher with email '{teacher_email}' not found"}, status=status.HTTP_404_NOT_FOUND)

        if dqc_email is not None:
            if dqc_email == "":
                cls.dqc_member = None
            else:
                try:
                    student = User.objects.get(email=dqc_email)
                    # Exclusivity constraint: Cannot be assigned to another class
                    other_class = Class.objects.filter(dqc_member=student).exclude(id=cls.id).first()
                    if other_class:
                        return Response({
                            "error": f"Student '{student.get_full_name() or dqc_email}' is already assigned as DQC Representative to '{other_class.name}'."
                        }, status=status.HTTP_400_BAD_REQUEST)

                    # Class allocation & email series verification constraint
                    if student.class_name and student.class_name.name != cls.name:
                        return Response({
                            "error": f"Student '{student.get_full_name() or dqc_email}' belongs to '{student.class_name.name}' and cannot be assigned to '{cls.name}'."
                        }, status=status.HTTP_400_BAD_REQUEST)

                    cls.dqc_member = student
                except User.DoesNotExist:
                    return Response({"error": f"Student with email '{dqc_email}' not found"}, status=status.HTTP_404_NOT_FOUND)

        cls.save()

        return Response({
            "id": cls.id,
            "name": cls.name,
            "department": cls.department.name,
            "department_code": cls.department.code,
            "classTeacher": cls.class_teacher.email if cls.class_teacher else None,
            "classTeacherName": cls.class_teacher.get_full_name() or cls.class_teacher.username if cls.class_teacher else None,
            "dqcMember": cls.dqc_member.email if cls.dqc_member else None,
            "dqcMemberName": cls.dqc_member.get_full_name() or cls.dqc_member.username if cls.dqc_member else None,
        })


class ClassDetailView(APIView):
    """GET / PATCH / PUT / DELETE a single Class by primary key."""
    permission_classes = [IsAdminOrStaffOrReadOnly]

    def _get_cls(self, pk):
        try:
            return Class.objects.select_related('department', 'course', 'class_teacher', 'dqc_member').get(pk=pk)
        except Class.DoesNotExist:
            return None

    def _serialize_class(self, cls):
        return {
            "id": cls.id,
            "name": cls.name,
            "department": cls.department.name if cls.department else None,
            "department_code": cls.department.code if cls.department else None,
            "course": cls.course.id if cls.course else None,
            "course_name": cls.course.name if cls.course else None,
            "course_abbreviation": cls.course.abbreviation if cls.course else None,
            "year_number": cls.year_number,
            "section": cls.section,
            "batch_start_year": cls.batch_start_year,
            "classTeacher": cls.class_teacher.email if cls.class_teacher else None,
            "classTeacherName": cls.class_teacher.get_full_name() or cls.class_teacher.username if cls.class_teacher else None,
            "dqcMember": cls.dqc_member.email if cls.dqc_member else None,
            "dqcMemberName": cls.dqc_member.get_full_name() or cls.dqc_member.username if cls.dqc_member else None,
            "num_students": cls.num_students,
            "negative_points": cls.negative_points,
        }

    def get(self, request, pk):
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(self._serialize_class(cls))

    def delete(self, request, pk):
        if not (request.user and request.user.is_authenticated and (getattr(request.user, 'role', None) == 'admin' or request.user.is_staff or request.user.is_superuser)):
            return Response({"error": "Admin permission required to delete classes."}, status=status.HTTP_403_FORBIDDEN)
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)
        class_name = cls.name
        cls.delete()
        return Response({"success": True, "deleted": class_name})

    def patch(self, request, pk):
        user = request.user
        user_role = getattr(user, 'role', None)
        if user_role in ('student', 'evaluation'):
            return Response(
                {"error": "Unauthorized: You do not have permission to modify classes."},
                status=status.HTTP_403_FORBIDDEN
            )
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)

        if user_role == 'faculty':
            if cls.class_teacher_id != user.id:
                return Response(
                    {"error": "Unauthorized: Faculty can only manage their own advised class."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if 'classTeacher' in request.data and request.data.get('classTeacher') != (cls.class_teacher.email if cls.class_teacher else ''):
                return Response(
                    {"error": "Unauthorized: Only administrators can reassign class advisors."},
                    status=status.HTTP_403_FORBIDDEN
                )

        update_fields = []
        if 'num_students' in request.data:
            try:
                num_val = int(request.data['num_students'])
                if num_val < 0 or num_val > 1000:
                    return Response({"error": "num_students must be between 0 and 1000."}, status=status.HTTP_400_BAD_REQUEST)
                cls.num_students = num_val
                update_fields.append('num_students')
            except (ValueError, TypeError):
                return Response({"error": "num_students must be an integer between 0 and 1000."}, status=status.HTTP_400_BAD_REQUEST)
        if 'negative_points' in request.data:
            try:
                neg_val = float(request.data['negative_points'])
                if neg_val < 0 or neg_val > 10000:
                    return Response({"error": "negative_points must be between 0 and 10000."}, status=status.HTTP_400_BAD_REQUEST)
                cls.negative_points = neg_val
                update_fields.append('negative_points')
            except (ValueError, TypeError):
                return Response({"error": "negative_points must be a valid number between 0 and 10000."}, status=status.HTTP_400_BAD_REQUEST)
        if 'name' in request.data:
            clean_name = str(request.data['name']).strip()
            if not clean_name or len(clean_name) > 100:
                return Response({"error": "Class name cannot be empty or exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
            cls.name = clean_name
            update_fields.append('name')
        if 'year_number' in request.data:
            try:
                yn = int(request.data['year_number'])
                if yn < 1 or yn > 6:
                    return Response({"error": "year_number must be between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
                cls.year_number = yn
                update_fields.append('year_number')
            except (ValueError, TypeError):
                return Response({"error": "year_number must be an integer between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
        if 'section' in request.data:
            sec = str(request.data['section']).strip().upper()
            if len(sec) > 5:
                return Response({"error": "section cannot exceed 5 characters."}, status=status.HTTP_400_BAD_REQUEST)
            cls.section = sec
            update_fields.append('section')
        if 'batch_start_year' in request.data:
            bsy = request.data['batch_start_year']
            if bsy:
                try:
                    bsy_int = int(bsy)
                    if bsy_int < 2000 or bsy_int > 2100:
                        return Response({"error": "batch_start_year must be between 2000 and 2100."}, status=status.HTTP_400_BAD_REQUEST)
                    cls.batch_start_year = bsy_int
                except (ValueError, TypeError):
                    return Response({"error": "batch_start_year must be an integer between 2000 and 2100."}, status=status.HTTP_400_BAD_REQUEST)
            else:
                cls.batch_start_year = None
            update_fields.append('batch_start_year')

        if update_fields:
            cls.save(update_fields=update_fields)
        return Response(self._serialize_class(cls))

    def put(self, request, pk):
        """Full update — handles class_teacher, dqcMember, and all moderation fields."""
        user = request.user
        user_role = getattr(user, 'role', None)
        if user_role in ('student', 'evaluation'):
            return Response(
                {"error": "Unauthorized: You do not have permission to modify classes."},
                status=status.HTTP_403_FORBIDDEN
            )
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)

        if user_role == 'faculty':
            teacher_email_check = request.data.get('classTeacher')
            is_own_class = (cls.class_teacher_id == user.id)
            is_claiming_unassigned = (cls.class_teacher is None and teacher_email_check == user.email)
            if not (is_own_class or is_claiming_unassigned):
                return Response(
                    {"error": "Unauthorized: Faculty can only manage their own advised class."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if teacher_email_check is not None and teacher_email_check != (cls.class_teacher.email if cls.class_teacher else user.email):
                return Response(
                    {"error": "Unauthorized: Only administrators can reassign class advisors."},
                    status=status.HTTP_403_FORBIDDEN
                )

        teacher_email = request.data.get('classTeacher')
        dqc_email = request.data.get('dqcMember')

        if teacher_email is not None:
            if teacher_email == "":
                if cls.class_teacher:
                    old_teacher = cls.class_teacher
                    cls.class_teacher = None
                    if not Class.objects.filter(class_teacher=old_teacher).exclude(id=cls.id).exists():
                        old_teacher.class_name = None
                        old_teacher.save(update_fields=['class_name'])
            else:
                try:
                    teacher = User.objects.get(email=teacher_email)
                    other_class = Class.objects.filter(class_teacher=teacher).exclude(id=cls.id).first()
                    if other_class:
                        return Response(
                            {"error": f"Faculty '{teacher.get_full_name() or teacher_email}' is already assigned as Class Advisor to '{other_class.name}'."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    if cls.class_teacher and cls.class_teacher != teacher:
                        old_teacher = cls.class_teacher
                        if not Class.objects.filter(class_teacher=old_teacher).exclude(id=cls.id).exists():
                            old_teacher.class_name = None
                            old_teacher.save(update_fields=['class_name'])
                    cls.class_teacher = teacher
                    teacher.class_name = cls
                    teacher.department = cls.department
                    teacher.save(update_fields=['class_name', 'department'])
                except User.DoesNotExist:
                    return Response({"error": f"Teacher with email '{teacher_email}' not found"}, status=status.HTTP_404_NOT_FOUND)

        if dqc_email is not None:
            if dqc_email == "":
                cls.dqc_member = None
            else:
                try:
                    student = User.objects.get(email=dqc_email)
                    other_class = Class.objects.filter(dqc_member=student).exclude(id=cls.id).first()
                    if other_class:
                        return Response(
                            {"error": f"Student '{student.get_full_name() or dqc_email}' is already assigned as DQC Representative to '{other_class.name}'."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    if student.class_name and student.class_name.name != cls.name:
                        return Response(
                            {"error": f"Student '{student.get_full_name() or dqc_email}' belongs to '{student.class_name.name}' and cannot be assigned to '{cls.name}'."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    cls.dqc_member = student
                except User.DoesNotExist:
                    return Response({"error": f"Student with email '{dqc_email}' not found"}, status=status.HTTP_404_NOT_FOUND)

        if 'num_students' in request.data:
            try:
                num_val = int(request.data['num_students'])
                if num_val < 0 or num_val > 1000:
                    return Response({"error": "num_students must be between 0 and 1000."}, status=status.HTTP_400_BAD_REQUEST)
                cls.num_students = num_val
            except (ValueError, TypeError):
                return Response({"error": "num_students must be an integer between 0 and 1000."}, status=status.HTTP_400_BAD_REQUEST)
        if 'negative_points' in request.data:
            try:
                neg_val = float(request.data['negative_points'])
                if neg_val < 0 or neg_val > 10000:
                    return Response({"error": "negative_points must be between 0 and 10000."}, status=status.HTTP_400_BAD_REQUEST)
                cls.negative_points = neg_val
            except (ValueError, TypeError):
                return Response({"error": "negative_points must be a valid number between 0 and 10000."}, status=status.HTTP_400_BAD_REQUEST)
        if 'name' in request.data:
            clean_name = str(request.data['name']).strip()
            if not clean_name or len(clean_name) > 100:
                return Response({"error": "Class name cannot be empty or exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
            cls.name = clean_name
        if 'year_number' in request.data:
            try:
                yn = int(request.data['year_number'])
                if yn < 1 or yn > 6:
                    return Response({"error": "year_number must be between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
                cls.year_number = yn
            except (ValueError, TypeError):
                return Response({"error": "year_number must be an integer between 1 and 6."}, status=status.HTTP_400_BAD_REQUEST)
        if 'section' in request.data:
            sec = str(request.data['section']).strip().upper()
            if len(sec) > 5:
                return Response({"error": "section cannot exceed 5 characters."}, status=status.HTTP_400_BAD_REQUEST)
            cls.section = sec
        if 'batch_start_year' in request.data:
            bsy = request.data['batch_start_year']
            if bsy:
                try:
                    bsy_int = int(bsy)
                    if bsy_int < 2000 or bsy_int > 2100:
                        return Response({"error": "batch_start_year must be between 2000 and 2100."}, status=status.HTTP_400_BAD_REQUEST)
                    cls.batch_start_year = bsy_int
                except (ValueError, TypeError):
                    return Response({"error": "batch_start_year must be an integer between 2000 and 2100."}, status=status.HTTP_400_BAD_REQUEST)
            else:
                cls.batch_start_year = None
        if 'course' in request.data:
            try:
                cls.course = Course.objects.get(pk=request.data['course'])
            except Course.DoesNotExist:
                return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)

        cls.save()
        return Response(self._serialize_class(cls))



class ClassIndexView(APIView):
    """Compute and return the moderated class index M for all classes using authoritative ScoringEngine.

    Authoritative Marian Evaluation Formula:
      Step 1: Net Obtained Score = S - P
              S = sum of verified marks on Evaluated/Locked submissions
              P = Class.negative_points (penalties)
      Step 2: Moderation Mark = min(200.0, max(0.0, 2.0 * (N - n)))
              N = Class.num_students (class size)
              n = SystemSetting['smallest_class_size'] (benchmark class size)
              Moderation compensation range: 0 to 200 marks.
      Step 3: Total Score = max(0.0, (S - P) + Moderation Mark)
      Step 4: Class Index M = Total Score / N

    Query params:
      ?year=2025-2026 (optional, filters by submission academic_year)
      ?explain=true (optional, returns full 4-step explanation breakdown)
    """
    permission_classes = [IsAdminOrPublicReadOnly]

    def get(self, request):
        year = request.query_params.get('year', None)
        explain = request.query_params.get('explain', '').lower() in ('true', '1', 'yes')

        # 1. Historical Snapshot: If academic year has published locked results, serve directly from ClassIndexResult
        if year:
            from users.models import AcademicYear, ClassIndexResult
            ay = AcademicYear.objects.filter(year=year).first()
            if ay:
                locked_snaps = ClassIndexResult.objects.filter(academic_year=ay, is_locked=True).select_related('class_name', 'class_name__department')
                if locked_snaps.exists():
                    snap_ranked = []
                    snap_unranked = []
                    for snap in locked_snaps:
                        if snap.snapshot_data:
                            entry = dict(snap.snapshot_data)
                        else:
                            entry = {
                                "class_id": snap.class_name.id,
                                "class_name": snap.class_name.name,
                                "department": snap.class_name.department.name if snap.class_name.department else 'General',
                                "department_code": snap.class_name.department.code if snap.class_name.department else 'GEN',
                                "N": snap.class_name.num_students,
                                "M": snap.final_index,
                                "rank": snap.rank,
                                "academic_score": snap.academic_score,
                                "co_curricular_score": snap.co_curricular_score,
                                "extra_curricular_score": snap.extra_curricular_score,
                                "scoring_version": snap.scoring_version,
                                "is_locked": snap.is_locked,
                            }
                        if snap.rank is not None:
                            snap_ranked.append(entry)
                        else:
                            snap_unranked.append(entry)
                    snap_ranked.sort(key=lambda x: (-(x["M"] if x["M"] is not None else -1.0), x["class_name"].lower() if x.get("class_name") else ""))
                    return Response(snap_ranked + snap_unranked, status=status.HTTP_200_OK)

        # 2. Dynamic Live Calculation
        from users.scoring_engine import compute_all_rankings, explain_class_score
        if explain:
            from users.models import Class
            all_classes = Class.objects.select_related('department').all()
            explanations = [explain_class_score(cls, academic_year=year) for cls in all_classes]
            ranked_exp = [e for e in explanations if e["N"] > 0]
            unranked_exp = [e for e in explanations if e["N"] == 0]
            ranked_exp.sort(key=lambda x: (
                -(x["M"] if x["M"] is not None else -1.0),
                x["class_name"].lower() if x["class_name"] else ""
            ))
            for idx, item in enumerate(ranked_exp):
                if idx > 0:
                    prev = ranked_exp[idx - 1]
                    prev_m = prev["M"] if prev["M"] is not None else 0.0
                    curr_m = item["M"] if item["M"] is not None else 0.0
                    if abs(curr_m - prev_m) < 1e-5:
                        item["rank"] = prev["rank"]
                    else:
                        item["rank"] = idx + 1
                else:
                    item["rank"] = 1
            for item in unranked_exp:
                item["rank"] = None
            return Response(ranked_exp + unranked_exp, status=status.HTTP_200_OK)

        ranked, unranked = compute_all_rankings(academic_year=year)
        return Response(ranked + unranked, status=status.HTTP_200_OK)

    def post(self, request):
        """Allow IQAC or Admin to snapshot official class index results for an academic year."""
        user = request.user
        user_role = getattr(user, 'role', None)
        if not (user.is_superuser or user_role in ('admin', 'iqac')):
            return Response({"error": "Only IQAC and Administrators can snapshot official rankings."}, status=status.HTTP_403_FORBIDDEN)

        year = request.data.get('year')
        if not year:
            return Response({"error": "year parameter is required for snapshotting."}, status=status.HTTP_400_BAD_REQUEST)

        force = request.data.get('force', False)
        lock = request.data.get('lock', True)

        from users.scoring_engine import snapshot_academic_year_results
        try:
            results = snapshot_academic_year_results(year, force=force, mark_locked=lock)

            record_system_audit_event(
                action='RANKING_PUBLISH' if lock else 'RANKING_CALCULATE',
                object_type='AcademicYear',
                object_id=year,
                actor=user,
                object_repr=f"Official Rankings for Academic Year {year} ({len(results)} classes, locked={lock})",
                old_value=None,
                new_value={'academic_year': year, 'class_count': len(results), 'is_locked': lock},
                reason=f"Rankings {'published and locked' if lock else 'calculated and snapshotted'} by {getattr(user, 'email', '')}",
                request=request
            )

            return Response({
                "message": f"Successfully snapshotted rankings for academic year '{year}'.",
                "count": len(results),
                "is_locked": lock
            }, status=status.HTTP_201_CREATED)
        except PermissionError as pe:
            return Response({"error": str(pe)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            logger.exception("Snapshotting rankings failed")
            return Response({"error": "Failed to snapshot rankings."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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

from users.scoring_engine import (
    get_criteria_allowed_bounds,
    calculate_submission_score,
)



class SubmissionListView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        user = request.user
        email_param = request.query_params.get('email')
        
        queryset = Submission.objects.select_related('user', 'user__class_name', 'user__department').all()

        # Role-based submission visibility
        if getattr(user, 'role', None) == 'student':
            if is_user_student_rep(user):
                rep_classes = Class.objects.filter(
                    Q(dqc_member=user) | Q(dqc_member__email__iexact=user.email)
                )
                queryset = queryset.filter(Q(user=user) | Q(user__class_name__in=rep_classes))
            else:
                queryset = queryset.filter(user=user)
        elif getattr(user, 'role', None) == 'faculty':
            advised_classes = Class.objects.filter(class_teacher=user)
            dept_q = Q(user__department=user.department) if user.department else Q(pk__in=[])
            if advised_classes.exists() or user.department:
                queryset = queryset.filter(Q(user__class_name__in=advised_classes) | dept_q)
            else:
                queryset = queryset.none()
            
        academic_year = request.query_params.get('academicYear')
        if academic_year:
            queryset = queryset.filter(academic_year=academic_year)
            
        is_staff_or_eval = bool(
            user and getattr(user, 'is_authenticated', False) and (
                getattr(user, 'role', '') in ('admin', 'iqac', 'faculty', 'evaluation') or
                getattr(user, 'is_staff', False) or
                getattr(user, 'is_superuser', False)
            )
        )

        data = []
        for s in queryset:
            is_owner = bool(user and getattr(user, 'is_authenticated', False) and s.user_id == user.id)
            can_see_eval_remarks = is_owner or is_staff_or_eval

            data.append({
                "id": s.id,
                "studentId": s.user.id if s.user else 1,
                "user_email": s.user.email if s.user else None,
                "userEmail": s.user.email if s.user else None,
                "user_name": s.user.name if s.user and hasattr(s.user, 'name') else s.user.email if s.user else None,
                "className": s.user.class_name.name if s.user and s.user.class_name else None,
                "class_name": s.user.class_name.name if s.user and s.user.class_name else None,
                "criteriaId": s.criteria_id,
                "academicYear": s.academic_year,
                "description": s.description,
                "status": s.status,
                "remarks": s.remarks,
                "marks": s.marks,
                "proof": s.proof,
                "eventId": s.event_id,
                "startDate": s.start_date,
                "start_date": s.start_date,
                "endDate": s.end_date,
                "end_date": s.end_date,
                "evaluatorVerified": s.evaluator_verified,
                "evidence": s.evidence,
                "verifiedByName": s.verified_by_name,
                "repVerifiedByName": s.rep_verified_by_name,
                "repRemarks": s.rep_remarks,
                "teacherVerifiedByName": s.teacher_verified_by_name,
                "teacherRemarks": s.teacher_remarks,
                "evaluatorVerifiedByName": s.evaluator_verified_by_name if can_see_eval_remarks else None,
                "evaluatorRemarks": s.evaluator_remarks if can_see_eval_remarks else None
            })
        return Response(data)

    def post(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return Response({"error": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)

        criteria_id = request.data.get('criteriaId')
        if not criteria_id:
            return Response({"error": "criteriaId is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            criteria_id_int = int(criteria_id)
        except (ValueError, TypeError):
            return Response({"error": "criteriaId must be a valid integer ID."}, status=status.HTTP_400_BAD_REQUEST)

        criteria_item = CriteriaItem.objects.filter(pk=criteria_id_int).first()
        if not criteria_item:
            return Response({"error": f"Criteria item with id '{criteria_id_int}' does not exist."}, status=status.HTTP_404_NOT_FOUND)

        academic_year = request.data.get('academicYear', '2025-2026')
        clean_ay = str(academic_year).strip()
        import re
        if not re.match(r'^\d{4}-\d{4}$', clean_ay) or len(clean_ay) > 20:
            return Response({"error": "academicYear must be in format 'YYYY-YYYY' (e.g. '2025-2026') and cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if not (AcademicYear.objects.filter(year=clean_ay).exists() or CriteriaVersion.objects.filter(academic_year=clean_ay).exists()):
            return Response({"error": f"Academic year '{clean_ay}' does not exist in the system."}, status=status.HTTP_400_BAD_REQUEST)
        academic_year = clean_ay

        description = request.data.get('description', '')
        clean_desc = str(description).strip()
        if not clean_desc:
            return Response({"error": "description is required and cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)
        if len(clean_desc) > 5000:
            return Response({"error": "description cannot exceed 5000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        description = clean_desc

        raw_status = request.data.get('status')
        user_role = getattr(user, 'role', None)

        # Validate workflow status on creation: client cannot set privileged or terminal states
        if user_role == 'student':
            # Students are strictly restricted to non-privileged initial statuses
            if raw_status is not None and raw_status not in ('Draft', 'Submitted', 'Pending Verification', 'Pending Rep Verification'):
                return Response(
                    {"error": "Unauthorized: Students cannot create submissions in verified, evaluated, or locked status."},
                    status=status.HTTP_403_FORBIDDEN
                )
            status_val = raw_status if raw_status else 'Draft'
        else:
            status_val = raw_status if raw_status else 'Pending Verification'
            if status_val not in dict(Submission.STATUS_CHOICES):
                return Response({"error": f"Invalid status: '{status_val}'."}, status=status.HTTP_400_BAD_REQUEST)

        remarks = request.data.get('remarks', '')
        if remarks and len(str(remarks)) > 2000:
            return Response({"error": "remarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)

        proof = request.data.get('proof', '')
        if proof:
            raw_proof = str(proof).strip()
            if '..' in raw_proof or raw_proof.startswith('/') or raw_proof.startswith('\\'):
                return Response({"error": "Invalid proof path specification."}, status=status.HTTP_400_BAD_REQUEST)
            if len(raw_proof) > 255:
                return Response({"error": "proof reference cannot exceed 255 characters."}, status=status.HTTP_400_BAD_REQUEST)
            proof = raw_proof

        proof_file = request.FILES.get('proof_file') or request.FILES.get('file') or request.FILES.get('evidence_file')
        uploaded_proof_hash = None
        if proof_file:
            try:
                rel_p, f_h, _, _ = save_private_evidence_file(proof_file, academic_year=academic_year)
                proof = rel_p
                uploaded_proof_hash = f_h
            except ValueError as ve:
                return Response({"error": str(ve)}, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                logger.exception("Failed to save uploaded evidence file")
                return Response({"error": f"Failed to save evidence file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        event_id = request.data.get('eventId', '')
        if event_id and len(str(event_id)) > 100:
            return Response({"error": "eventId cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)

        evidence = request.data.get('evidence')
        start_date = request.data.get('start_date') or request.data.get('startDate')
        if not start_date and isinstance(evidence, dict):
            start_date = evidence.get('startDate') or evidence.get('examDate')
        if start_date and len(str(start_date)) > 50:
            return Response({"error": "start_date cannot exceed 50 characters."}, status=status.HTTP_400_BAD_REQUEST)

        end_date = request.data.get('end_date') or request.data.get('endDate')
        if not end_date and isinstance(evidence, dict):
            end_date = evidence.get('endDate')
        if end_date and len(str(end_date)) > 50:
            return Response({"error": "end_date cannot exceed 50 characters."}, status=status.HTTP_400_BAD_REQUEST)

        marks = request.data.get('marks')
        if marks is not None and user and user_role == 'student':
            marks = None
        elif marks is not None:
            try:
                req_marks = float(marks)
                allowed_min, allowed_max, details = get_criteria_allowed_bounds(criteria_item, evidence)
                is_negative = (criteria_item.type in ('negative', 'academic_grades')) or (allowed_min < 0)
                if req_marks < 0 and not is_negative:
                    return Response(
                        {"error": f"Score ({req_marks}) cannot be negative for non-penalty criteria."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if req_marks > (allowed_max + 1e-5):
                    return Response(
                        {"error": f"Requested score ({req_marks}) exceeds the maximum allowed limit ({allowed_max}) for criteria '{criteria_item.title}'{details}."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if allowed_min is not None and req_marks < (allowed_min - 1e-5):
                    return Response(
                        {"error": f"Requested score ({req_marks}) is below the minimum allowed limit ({allowed_min}) for criteria '{criteria_item.title}'."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (ValueError, TypeError):
                return Response({"error": "Invalid marks value provided."}, status=status.HTTP_400_BAD_REQUEST)
            
        # Check submission limits for Online Courses and UPSC/PSC Exams
        try:
            criteria_id_int = int(criteria_id)
            online_item_ids = get_online_courses_item_ids()
            if criteria_id_int in online_item_ids:
                existing_count = Submission.objects.filter(
                    user=user,
                    criteria_id__in=online_item_ids
                ).exclude(status='Rejected').count()
                if existing_count >= 3:
                    return Response(
                        {"error": "Maximum 3 online courses can be submitted per student. Limit of 3 reached."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            upsc_item_ids = get_upsc_psc_item_ids()
            if criteria_id_int in upsc_item_ids:
                existing_count = Submission.objects.filter(
                    user=user,
                    criteria_id__in=upsc_item_ids
                ).exclude(status='Rejected').count()
                if existing_count >= 3:
                    return Response(
                        {"error": "Maximum 3 submissions allowed for UPSC/PSC Exam Participation. Limit of 3 reached."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
        except (ValueError, TypeError):
            pass

        # Academic Grade Breakdown Validation & Auto-Calculation
        if isinstance(evidence, dict) and "grades" in evidence:
            grades_data = evidence.get("grades") or {}
            s_cnt = int(grades_data.get("S", 0))
            ap_cnt = int(grades_data.get("APlus", 0))
            a_cnt = int(grades_data.get("A", 0))
            fail_cnt = int(grades_data.get("Fail", 0))
            t_students = int(evidence.get("totalStudents", 0))

            if s_cnt < 0 or ap_cnt < 0 or a_cnt < 0 or fail_cnt < 0 or t_students < 0:
                return Response({"error": "Grade counts and total students cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

            g_sum = s_cnt + ap_cnt + a_cnt + fail_cnt
            if t_students <= 0:
                t_students = max(1, g_sum)
                evidence["totalStudents"] = t_students

            if g_sum > t_students:
                return Response({"error": f"Sum of student grades ({g_sum}) exceeds total class students ({t_students})."}, status=status.HTTP_400_BAD_REQUEST)

            passed = max(0, t_students - fail_cnt)
            pass_pct = round((passed / float(t_students)) * 100.0, 2)
            evidence["classPassPercentage"] = pass_pct
            evidence["passCount"] = passed

        # Extract certificate ID and proof hash for duplicate detection
        cert_id = request.data.get('certificateId') or request.data.get('eventId')
        if not cert_id and isinstance(evidence, dict):
            cert_id = evidence.get('certificateId') or evidence.get('certId') or evidence.get('startupGovtId') or evidence.get('eventId')
        if cert_id and len(str(cert_id)) > 100:
            return Response({"error": "certificateId/identifier cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
        
        proof_h = uploaded_proof_hash or request.data.get('proofHash')
        if not proof_h and isinstance(evidence, dict):
            proof_h = evidence.get('proofHash')
        if not proof_h and proof:
            proof_h = hashlib.sha256(str(proof).encode('utf-8')).hexdigest()

        # Check duplicate submission
        dup_err = check_duplicate_submission(
            user=user,
            criteria_id=criteria_id,
            academic_year=academic_year,
            certificate_id=cert_id,
            proof_hash=proof_h,
            description=description
        )
        if dup_err:
            return Response({"error": dup_err}, status=status.HTTP_400_BAD_REQUEST)

        # Resolve active CriteriaVersion for submission's academic_year
        active_cv = CriteriaVersion.objects.filter(academic_year=academic_year, is_locked=False).order_by('-version').first()
        if not active_cv:
            active_cv = CriteriaVersion.objects.filter(academic_year=academic_year).order_by('-version').first()
        if not active_cv:
            active_cv, _ = CriteriaVersion.objects.get_or_create(
                academic_year=academic_year or '2025-2026',
                version=1,
                defaults={'name': f'{academic_year} v1', 'is_locked': False}
            )

        # Academic Grade Breakdown: AcademicGradeBreakdown is the authoritative relational source
        gb_data = request.data.get('grade_breakdown')
        if not gb_data and isinstance(evidence, dict) and "grades" in evidence:
            ev_g = evidence.get('grades') or {}
            gb_data = {
                "s_grade_count": ev_g.get("S", 0),
                "a_plus_grade_count": ev_g.get("APlus", 0),
                "a_grade_count": ev_g.get("A", 0),
                "other_pass_count": ev_g.get("OtherPass", 0) or ev_g.get("B", 0),
                "failed_count": ev_g.get("Fail", 0),
                "total_students": evidence.get("totalStudents", 0)
            }

        # Clean evidence to avoid duplicating grade data in JSON
        clean_evidence = dict(evidence or {}) if isinstance(evidence, dict) else {}
        clean_evidence.pop("grades", None)
        clean_evidence.pop("markBreakdown", None)
        clean_evidence.pop("classPassPercentage", None)
        clean_evidence.pop("totalStudents", None)
        clean_evidence.pop("passCount", None)

        if marks is None:
            c_item = CriteriaItem.objects.filter(pk=criteria_id_int).first()
            if c_item:
                marks = calculate_submission_score(c_item, evidence)

        from django.db import IntegrityError
        try:
            with transaction.atomic():
                submission = Submission.objects.create(
                    user=user,
                    criteria_id=criteria_id_int,
                    criteria_version=active_cv,
                    academic_year=academic_year,
                    description=description,
                    status=status_val,
                    remarks=remarks,
                    marks=marks,
                    proof=proof,
                    proof_hash=proof_h,
                    certificate_id=cert_id,
                    event_id=event_id,
                    evidence=clean_evidence,
                    start_date=start_date,
                    end_date=end_date
                )

                sub_type = clean_evidence.get("submissionType")
                if sub_type:
                    submission.submission_type = sub_type
                    submission.save(update_fields=["submission_type"])

                if isinstance(gb_data, dict):
                    s_c = int(gb_data.get("s_grade_count", 0) or 0)
                    ap_c = int(gb_data.get("a_plus_grade_count", 0) or 0)
                    a_c = int(gb_data.get("a_grade_count", 0) or 0)
                    other_c = int(gb_data.get("other_pass_count", 0) or 0)
                    fail_c = int(gb_data.get("failed_count", 0) or 0)
                    total_c = int(gb_data.get("total_students", 0) or 0)
                    AcademicGradeBreakdown.objects.update_or_create(
                        submission=submission,
                        defaults={
                            "s_grade_count": s_c,
                            "a_plus_grade_count": ap_c,
                            "a_grade_count": a_c,
                            "other_pass_count": other_c,
                            "failed_count": fail_c,
                            "total_students": total_c
                        }
                    )

                create_audit_entry(
                    submission=submission,
                    actor=user,
                    stage=1,
                    stage_name="Student Claims",
                    prev_status="Initial",
                    new_status=submission.status,
                    comments=remarks or "",
                    request=request
                )

                record_system_audit_event(
                    action='SUBMISSION_CREATE',
                    object_type='Submission',
                    object_id=submission.id,
                    actor=user,
                    object_repr=f"Submission #{submission.id} (Criteria: {submission.criteria_id}) -> {submission.status}",
                    old_value=None,
                    new_value={'status': submission.status, 'marks': submission.marks, 'criteria_id': submission.criteria_id},
                    reason=remarks or "New submission created",
                    request=request
                )
        except IntegrityError as e:
            logger.warning(f"IntegrityError creating submission: {e}")
            return Response(
                {"error": "A duplicate submission with this certificate or proof document was already recorded in the system."},
                status=status.HTTP_400_BAD_REQUEST
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Failed to create submission")
            return Response({"error": "Failed to create submission. Please verify your input data."}, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            "id": submission.id,
            "studentId": submission.user.id if submission.user else 1,
            "user_email": submission.user.email if submission.user else None,
            "userEmail": submission.user.email if submission.user else None,
            "user_name": submission.user.name if submission.user and hasattr(submission.user, 'name') else submission.user.email if submission.user else None,
            "className": submission.user.class_name.name if submission.user and submission.user.class_name else None,
            "class_name": submission.user.class_name.name if submission.user and submission.user.class_name else None,
            "criteriaId": submission.criteria_id,
            "academicYear": submission.academic_year,
            "description": submission.description,
            "status": submission.status,
            "remarks": submission.remarks,
            "marks": submission.marks,
            "proof": submission.proof,
            "eventId": submission.event_id,
            "startDate": submission.start_date,
            "start_date": submission.start_date,
            "endDate": submission.end_date,
            "end_date": submission.end_date,
            "evaluatorVerified": submission.evaluator_verified,
            "evidence": submission.evidence,
            "verifiedByName": submission.verified_by_name,
            "repVerifiedByName": submission.rep_verified_by_name,
            "repRemarks": submission.rep_remarks,
            "teacherVerifiedByName": submission.teacher_verified_by_name,
            "teacherRemarks": submission.teacher_remarks,
            "evaluatorVerifiedByName": submission.evaluator_verified_by_name,
            "evaluatorRemarks": submission.evaluator_remarks
        }, status=status.HTTP_201_CREATED)

class SubmissionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            submission = Submission.objects.select_related('user', 'user__class_name', 'user__department').get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found"}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        user_role = getattr(user, 'role', None)

        # Object-level authorization for reading submissions (Area 3: IDOR prevention)
        if user.is_superuser or user_role in ('admin', 'iqac', 'evaluation'):
            pass
        elif user_role == 'faculty':
            advised_classes = Class.objects.filter(class_teacher=user)
            is_class_teacher = bool(submission.user and submission.user.class_name in advised_classes)
            is_same_dept = bool(submission.user and user.department_id and (submission.user.department_id == user.department_id))
            if not (is_class_teacher or is_same_dept):
                return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)
        elif user_role == 'student':
            if submission.user_id != user.id:
                if not is_user_student_rep(user):
                    return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)
                rep_classes = Class.objects.filter(
                    Q(dqc_member=user) | Q(dqc_member__email__iexact=user.email)
                )
                if not (submission.user and submission.user.class_name in rep_classes):
                    return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)
        else:
            return Response({"error": "You do not have permission to view this submission."}, status=status.HTTP_403_FORBIDDEN)

        serializer = SubmissionSerializer(submission)
        resp_data = dict(serializer.data)
        is_owner = bool(user and getattr(user, 'is_authenticated', False) and submission.user_id == user.id)
        is_staff_or_eval = bool(
            user and getattr(user, 'is_authenticated', False) and (
                getattr(user, 'role', '') in ('admin', 'iqac', 'faculty', 'evaluation') or
                getattr(user, 'is_staff', False) or
                getattr(user, 'is_superuser', False)
            )
        )
        if not (is_owner or is_staff_or_eval):
            resp_data['evaluator_remarks'] = None
            resp_data['evaluator_verified_by_name'] = None
        return Response(resp_data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        user = request.user
        if not user or not getattr(user, 'is_authenticated', False):
            return Response({"error": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            submission = Submission.objects.get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found"}, status=status.HTTP_404_NOT_FOUND)

        is_owner = (submission.user_id == user.id)
        user_role = getattr(user, 'role', None)
        is_rep = is_user_student_rep(user)

        if user_role == 'student':
            if is_owner:
                if submission.status in UNEDITABLE_BY_STUDENT_STATES:
                    return Response(
                        {"error": f"Submission cannot be edited in '{submission.status}' status."},
                        status=status.HTTP_403_FORBIDDEN
                    )
                req_status = request.data.get('status')
                if req_status and req_status != submission.status:
                    if req_status in ('Approved', 'Verified', 'Teacher Verified', 'Student Rep Verified', 'Evaluated', 'Locked'):
                        return Response(
                            {"error": "Unauthorized: Students cannot alter verification or evaluation status."},
                            status=status.HTTP_403_FORBIDDEN
                        )
                if request.data.get('marks') is not None and request.data.get('marks') != submission.marks:
                    return Response(
                        {"error": "Unauthorized: Students cannot assign evaluation marks."},
                        status=status.HTTP_403_FORBIDDEN
                    )
            elif is_rep:
                rep_classes = Class.objects.filter(
                    Q(dqc_member=user) | Q(dqc_member__email__iexact=user.email)
                )
                if not (submission.user and submission.user.class_name in rep_classes):
                    return Response(
                        {"error": "Student representative is not assigned to this student's class."},
                        status=status.HTTP_403_FORBIDDEN
                    )
                allowed_rep_statuses = {'Student Rep Verified', 'Correction Requested', 'Rejected', 'Pending Rep Verification', 'Pending', 'Submitted'}
                req_status = request.data.get('status')
                if req_status and req_status not in allowed_rep_statuses:
                    return Response(
                        {"error": f"Student representatives cannot transition submission to '{req_status}'."},
                        status=status.HTTP_403_FORBIDDEN
                    )
            else:
                return Response(
                    {"error": "You do not have permission to modify this submission."},
                    status=status.HTTP_403_FORBIDDEN
                )
        elif user_role == 'faculty':
            advised_classes = Class.objects.filter(class_teacher=user)
            is_class_teacher = bool(submission.user and submission.user.class_name in advised_classes)
            is_same_dept = bool(submission.user and user.department_id and (submission.user.department_id == user.department_id))
            if not (is_class_teacher or is_same_dept):
                return Response(
                    {"error": "Faculty cannot modify submissions outside their advised class or department."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if 'marks' in request.data and request.data.get('marks') is not None and request.data.get('marks') != submission.marks:
                return Response(
                    {"error": "Unauthorized: Faculty cannot assign evaluation marks."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if 'evaluatorRemarks' in request.data and request.data.get('evaluatorRemarks') != submission.evaluator_remarks:
                return Response(
                    {"error": "Unauthorized: Faculty cannot assign evaluator remarks."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if 'evaluatorVerified' in request.data and request.data.get('evaluatorVerified') != submission.evaluator_verified:
                return Response(
                    {"error": "Unauthorized: Faculty cannot alter evaluator verification status."},
                    status=status.HTTP_403_FORBIDDEN
                )
        elif user_role == 'evaluation':
            req_c_val = request.data.get('criteriaId', submission.criteria_id)
            try:
                req_criteria_id = int(req_c_val)
            except (ValueError, TypeError):
                return Response({"error": "criteriaId must be a valid integer ID."}, status=status.HTTP_400_BAD_REQUEST)
            criteria_item = CriteriaItem.objects.filter(pk=req_criteria_id).select_related('category').first()
            if not criteria_item:
                return Response({"error": f"Criteria item with id '{req_criteria_id}' does not exist."}, status=status.HTTP_404_NOT_FOUND)
            if criteria_item and criteria_item.category and criteria_item.category.evaluators:
                cat_evaluators = [str(e).strip().lower() for e in criteria_item.category.evaluators if e]
                user_email = (user.email or '').strip().lower()
                if cat_evaluators and user_email not in cat_evaluators:
                    return Response(
                        {"error": "Unauthorized: Evaluator is not assigned to evaluate this criteria category."},
                        status=status.HTTP_403_FORBIDDEN
                    )
        elif user.is_superuser or user_role in ('admin', 'iqac'):
            pass
        else:
            return Response(
                {"error": "You do not have permission to modify this submission."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Validate criteriaId existence on update
        if 'criteriaId' in request.data and request.data.get('criteriaId') is not None:
            try:
                target_criteria_id = int(request.data['criteriaId'])
            except (ValueError, TypeError):
                return Response({"error": "criteriaId must be a valid integer ID."}, status=status.HTTP_400_BAD_REQUEST)
            c_check = CriteriaItem.objects.filter(pk=target_criteria_id).first()
            if not c_check:
                return Response({"error": f"Criteria item with id '{target_criteria_id}' does not exist."}, status=status.HTTP_404_NOT_FOUND)
        else:
            target_criteria_id = int(submission.criteria_id)

        # Validate academicYear on update
        if 'academicYear' in request.data and request.data.get('academicYear') is not None:
            upd_ay = str(request.data['academicYear']).strip()
            import re
            if not re.match(r'^\d{4}-\d{4}$', upd_ay) or len(upd_ay) > 20:
                return Response({"error": "academicYear must be in format 'YYYY-YYYY' (e.g. '2025-2026') and cannot exceed 20 characters."}, status=status.HTTP_400_BAD_REQUEST)
            if not (AcademicYear.objects.filter(year=upd_ay).exists() or CriteriaVersion.objects.filter(academic_year=upd_ay).exists()):
                return Response({"error": f"Academic year '{upd_ay}' does not exist in the system."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate string lengths on update
        if 'description' in request.data:
            clean_d = str(request.data['description']).strip()
            if not clean_d:
                return Response({"error": "description cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)
            if len(clean_d) > 5000:
                return Response({"error": "description cannot exceed 5000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'remarks' in request.data and request.data.get('remarks') and len(str(request.data['remarks'])) > 2000:
            return Response({"error": "remarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'proof' in request.data and request.data.get('proof'):
            raw_proof = str(request.data.get('proof')).strip()
            if '..' in raw_proof or raw_proof.startswith('/') or raw_proof.startswith('\\'):
                return Response({"error": "Invalid proof path specification."}, status=status.HTTP_400_BAD_REQUEST)
            if len(raw_proof) > 255:
                return Response({"error": "proof cannot exceed 255 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'eventId' in request.data and request.data.get('eventId') and len(str(request.data['eventId'])) > 100:
            return Response({"error": "eventId cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'certificateId' in request.data and request.data.get('certificateId') and len(str(request.data['certificateId'])) > 100:
            return Response({"error": "certificateId cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'repRemarks' in request.data and request.data.get('repRemarks') and len(str(request.data['repRemarks'])) > 2000:
            return Response({"error": "repRemarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'teacherRemarks' in request.data and request.data.get('teacherRemarks') and len(str(request.data['teacherRemarks'])) > 2000:
            return Response({"error": "teacherRemarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if 'evaluatorRemarks' in request.data and request.data.get('evaluatorRemarks') and len(str(request.data['evaluatorRemarks'])) > 2000:
            return Response({"error": "evaluatorRemarks cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)

        # Check online courses & UPSC/PSC limits on update if changing criteriaId or status
        target_status = request.data.get('status', submission.status)
        online_item_ids = get_online_courses_item_ids()
        if target_criteria_id in online_item_ids and target_status != 'Rejected':
            existing_count = Submission.objects.filter(
                user=submission.user,
                criteria_id__in=online_item_ids
            ).exclude(id=submission.id).exclude(status='Rejected').count()
            if existing_count >= 3:
                return Response(
                    {"error": "Maximum 3 online courses can be submitted per student. Limit of 3 reached."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        upsc_item_ids = get_upsc_psc_item_ids()
        if target_criteria_id in upsc_item_ids and target_status != 'Rejected':
            existing_count = Submission.objects.filter(
                user=submission.user,
                criteria_id__in=upsc_item_ids
            ).exclude(id=submission.id).exclude(status='Rejected').count()
            if existing_count >= 3:
                return Response(
                    {"error": "Maximum 3 submissions allowed for UPSC/PSC Exam Participation. Limit of 3 reached."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Duplicate detection check on update
        target_cert_id = request.data.get('certificateId') or request.data.get('eventId', submission.certificate_id or submission.event_id)
        if not target_cert_id and isinstance(request.data.get('evidence'), dict):
            ev = request.data.get('evidence')
            target_cert_id = ev.get('certificateId') or ev.get('certId') or ev.get('startupGovtId') or ev.get('eventId')
        
        target_proof_h = request.data.get('proofHash')
        if not target_proof_h and isinstance(request.data.get('evidence'), dict):
            target_proof_h = request.data.get('evidence').get('proofHash')
        if not target_proof_h and 'proof' in request.data and request.data.get('proof'):
            target_proof_h = hashlib.sha256(str(request.data.get('proof')).encode('utf-8')).hexdigest()
        if not target_proof_h:
            target_proof_h = submission.proof_hash

        dup_err = check_duplicate_submission(
            user=submission.user,
            criteria_id=target_criteria_id,
            academic_year=request.data.get('academicYear', submission.academic_year),
            certificate_id=target_cert_id,
            proof_hash=target_proof_h,
            description=request.data.get('description', submission.description),
            submission_id=submission.id
        )
        # Academic Grade Breakdown: AcademicGradeBreakdown is the authoritative relational source
        gb_data = request.data.get('grade_breakdown')
        if not gb_data and isinstance(request.data.get('evidence'), dict) and "grades" in request.data.get('evidence'):
            ev_g = request.data.get('evidence').get('grades') or {}
            gb_data = {
                "s_grade_count": ev_g.get("S", 0),
                "a_plus_grade_count": ev_g.get("APlus", 0),
                "a_grade_count": ev_g.get("A", 0),
                "other_pass_count": ev_g.get("OtherPass", 0) or ev_g.get("B", 0),
                "failed_count": ev_g.get("Fail", 0),
                "total_students": request.data.get('evidence').get("totalStudents", 0)
            }
        
        upd_ev = request.data.get('evidence', submission.evidence)
        if isinstance(gb_data, dict):
            s_cnt = int(gb_data.get('s_grade_count', 0) or 0)
            ap_cnt = int(gb_data.get('a_plus_grade_count', 0) or 0)
            a_cnt = int(gb_data.get('a_grade_count', 0) or 0)
            oth_cnt = int(gb_data.get('other_pass_count', 0) or 0)
            fail_cnt = int(gb_data.get('failed_count', 0) or 0)
            t_students = int(gb_data.get('total_students', 0) or 0)

            if s_cnt < 0 or ap_cnt < 0 or a_cnt < 0 or oth_cnt < 0 or fail_cnt < 0 or t_students < 0:
                return Response({"error": "Grade counts and total students cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

            passed = max(0, t_students - fail_cnt)
            if oth_cnt <= 0 and passed > (s_cnt + ap_cnt + a_cnt):
                oth_cnt = passed - (s_cnt + ap_cnt + a_cnt)

            g_sum = s_cnt + ap_cnt + a_cnt + oth_cnt + fail_cnt
            if t_students <= 0:
                t_students = max(1, g_sum)

            if g_sum != t_students:
                return Response({"error": f"Sum of grade counts ({g_sum}) must strictly equal total students ({t_students})."}, status=status.HTTP_400_BAD_REQUEST)

            pass_pct = round((passed / float(t_students)) * 100.0, 2)
            gb_sync_defaults = {
                "s_grade_count": s_cnt,
                "a_plus_grade_count": ap_cnt,
                "a_grade_count": a_cnt,
                "other_pass_count": oth_cnt,
                "failed_count": fail_cnt,
                "class_pass_percentage": pass_pct,
                "total_students": t_students
            }
            # Ensure evidence does not duplicate grade data in JSON
            if isinstance(upd_ev, dict):
                upd_ev = dict(upd_ev)
                upd_ev.pop("grades", None)
                upd_ev.pop("markBreakdown", None)
                upd_ev.pop("classPassPercentage", None)
                upd_ev.pop("totalStudents", None)
                upd_ev.pop("passCount", None)

        # 1. Validate Workflow State Machine Transition & Role Scope
        req_status = request.data.get('status', submission.status)
        from .workflow import validate_workflow_transition, execute_workflow_transition
        is_valid, err_msg, stage_num, stage_name = validate_workflow_transition(
            submission=submission,
            target_status_input=req_status,
            user=user,
            data=request.data
        )
        if not is_valid:
            if err_msg.startswith("Invalid workflow state transition"):
                status_code = status.HTTP_400_BAD_REQUEST
            elif "Unauthorized" in err_msg or "record has been locked" in err_msg.lower():
                status_code = status.HTTP_403_FORBIDDEN
            else:
                status_code = status.HTTP_400_BAD_REQUEST
            return Response({"error": err_msg}, status=status_code)

        # 2. Authorization Check: Students cannot assign marks.
        if user and user_role == 'student' and 'marks' in request.data and request.data.get('marks') is not None:
            return Response(
                {"error": "Unauthorized: Students cannot assign evaluation marks."},
                status=status.HTTP_403_FORBIDDEN
            )

        # 3. Score Range & Type Bounds Verification
        if 'marks' in request.data and request.data.get('marks') is not None:
            try:
                req_marks = float(request.data.get('marks'))
            except (ValueError, TypeError):
                return Response(
                    {"error": "Invalid marks value provided."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            criteria_item = CriteriaItem.objects.filter(pk=target_criteria_id).first()
            if criteria_item:
                target_ev = request.data.get('evidence', submission.evidence)
                allowed_min, allowed_max, details = get_criteria_allowed_bounds(criteria_item, target_ev)

                is_negative = (criteria_item.type in ('negative', 'academic_grades')) or (allowed_min < 0)
                if req_marks < 0 and not is_negative:
                    return Response(
                        {"error": f"Score ({req_marks}) cannot be negative for non-penalty criteria."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if req_marks > (allowed_max + 1e-5):
                    return Response(
                        {"error": f"Requested score ({req_marks}) exceeds the maximum allowed limit ({allowed_max}) for criteria '{criteria_item.title}'{details}."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if allowed_min is not None and req_marks < (allowed_min - 1e-5):
                    return Response(
                        {"error": f"Requested score ({req_marks}) is below the minimum allowed limit ({allowed_min}) for criteria '{criteria_item.title}'."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

        # 4. Save updates and record audit log inside atomic transaction
        from django.db import IntegrityError
        try:
            with transaction.atomic():
                submission = Submission.objects.select_for_update().get(pk=pk)
                if submission.status == 'Locked' and not (user_role in ('admin', 'iqac') or getattr(user, 'is_superuser', False)):
                    return Response(
                        {"error": "This submission record has been locked and cannot be modified."},
                        status=status.HTTP_403_FORBIDDEN
                    )

                if 'gb_sync_defaults' in locals() and gb_sync_defaults:
                    AcademicGradeBreakdown.objects.update_or_create(
                        submission=submission,
                        defaults=gb_sync_defaults
                    )

                prev_status = submission.status
                target_status = request.data.get('status', submission.status)

                extra_updates = {}
                if 'criteriaId' in request.data:
                    extra_updates['criteria_id'] = int(request.data.get('criteriaId'))
                if 'academicYear' in request.data:
                    extra_updates['academic_year'] = request.data.get('academicYear')
                if 'description' in request.data:
                    extra_updates['description'] = request.data.get('description')
                if 'proof' in request.data:
                    extra_updates['proof'] = request.data.get('proof')
                if 'eventId' in request.data:
                    extra_updates['event_id'] = request.data.get('eventId')
                if 'evidence' in request.data:
                    extra_updates['evidence'] = upd_ev
                if 'start_date' in request.data or 'startDate' in request.data:
                    extra_updates['start_date'] = request.data.get('start_date') or request.data.get('startDate')
                elif 'evidence' in request.data and isinstance(request.data.get('evidence'), dict):
                    ev = request.data.get('evidence')
                    if ev.get('startDate') or ev.get('examDate'):
                        extra_updates['start_date'] = ev.get('startDate') or ev.get('examDate')

                if 'end_date' in request.data or 'endDate' in request.data:
                    extra_updates['end_date'] = request.data.get('end_date') or request.data.get('endDate')
                elif 'evidence' in request.data and isinstance(request.data.get('evidence'), dict):
                    ev = request.data.get('evidence')
                    if ev.get('endDate'):
                        extra_updates['end_date'] = ev.get('endDate')

                remarks_val = (
                    request.data.get('remarks') or
                    request.data.get('teacherRemarks') or
                    request.data.get('repRemarks') or
                    request.data.get('evaluatorRemarks') or
                    ""
                )
                marks_val = request.data.get('marks')

                submission = execute_workflow_transition(
                    submission=submission,
                    target_status=target_status,
                    user=user,
                    remarks=remarks_val,
                    marks=marks_val,
                    request=request,
                    **extra_updates
                )
        except IntegrityError as e:
            logger.warning(f"IntegrityError updating submission #{pk}: {e}")
            return Response(
                {"error": "A duplicate submission with this certificate or proof document was already recorded in the system."},
                status=status.HTTP_400_BAD_REQUEST
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "id": submission.id,
            "studentId": submission.user.id if submission.user else 1,
            "criteriaId": submission.criteria_id,
            "academicYear": submission.academic_year,
            "description": submission.description,
            "status": submission.status,
            "remarks": submission.remarks,
            "marks": submission.marks,
            "proof": submission.proof,
            "eventId": submission.event_id,
            "startDate": submission.start_date,
            "start_date": submission.start_date,
            "endDate": submission.end_date,
            "end_date": submission.end_date,
            "evaluatorVerified": submission.evaluator_verified,
            "evidence": submission.evidence,
            "verifiedByName": submission.verified_by_name,
            "repVerifiedByName": submission.rep_verified_by_name,
            "repRemarks": submission.rep_remarks,
            "teacherVerifiedByName": submission.teacher_verified_by_name,
            "teacherRemarks": submission.teacher_remarks,
            "evaluatorVerifiedByName": submission.evaluator_verified_by_name,
            "evaluatorRemarks": submission.evaluator_remarks
        })

    def delete(self, request, pk):
        user = request.user
        if not user or not getattr(user, 'is_authenticated', False):
            return Response({"error": "Authentication credentials were not provided."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            submission = Submission.objects.get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found"}, status=status.HTTP_404_NOT_FOUND)

        is_owner = (submission.user_id == user.id)
        is_admin = bool(getattr(user, 'role', None) == 'admin' or user.is_staff or user.is_superuser)

        if is_owner:
            if submission.status in UNEDITABLE_BY_STUDENT_STATES:
                return Response(
                    {"error": f"Submissions in '{submission.status}' status cannot be deleted by students."},
                    status=status.HTTP_403_FORBIDDEN
                )
            if submission.proof:
                safe_p = resolve_safe_private_path(submission.proof)
                if safe_p and os.path.isfile(safe_p):
                    try:
                        os.remove(safe_p)
                    except Exception:
                        pass
            submission.delete()
            return Response({"success": True}, status=status.HTTP_200_OK)
        elif is_admin:
            if submission.proof:
                safe_p = resolve_safe_private_path(submission.proof)
                if safe_p and os.path.isfile(safe_p):
                    try:
                        os.remove(safe_p)
                    except Exception:
                        pass
            submission.delete()
            return Response({"success": True}, status=status.HTTP_200_OK)
        else:
            return Response(
                {"error": "You do not have permission to delete this submission."},
                status=status.HTTP_403_FORBIDDEN
            )


class SubmissionEvidenceView(APIView):
    """
    Secure Evidence File Access & Upload endpoint.
    GET /api/submissions/<int:pk>/evidence/
    POST /api/submissions/<int:pk>/evidence/
    DELETE /api/submissions/<int:pk>/evidence/
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _check_access_permission(self, user, submission, action="view"):
        user_role = getattr(user, 'role', None)
        # Superuser, admin, iqac always have access
        if user.is_superuser or user_role in ('admin', 'iqac'):
            return True, None

        if action in ("upload", "delete"):
            # Only student owner (or admin/iqac) can upload/delete evidence
            if user_role == 'student':
                if submission.user_id != user.id:
                    return False, "You cannot modify evidence for another student's submission."
                if submission.status not in ('Draft', 'Correction Requested', 'Correction'):
                    return False, f"Cannot modify evidence when submission is in '{submission.status}' status."
                return True, None
            return False, "Only the submission owner or administrators can modify evidence files."

        # action == "view" (download/stream)
        if user_role == 'evaluation':
            criteria_item = CriteriaItem.objects.filter(pk=submission.criteria_id).select_related('category').first()
            if criteria_item and criteria_item.category and criteria_item.category.evaluators:
                cat_evaluators = [str(e).strip().lower() for e in criteria_item.category.evaluators if e]
                user_email = (user.email or '').strip().lower()
                if cat_evaluators and user_email not in cat_evaluators:
                    return False, "Unauthorized: Evaluator is not assigned to evaluate this criteria category."
            return True, None

        if user_role == 'faculty':
            advised_classes = Class.objects.filter(class_teacher=user)
            is_class_teacher = bool(submission.user and submission.user.class_name in advised_classes)
            is_same_dept = bool(submission.user and user.department_id and (submission.user.department_id == user.department_id))
            if is_class_teacher or is_same_dept:
                return True, None
            return False, "Faculty cannot access evidence outside their advised class or department."

        if user_role == 'student':
            if submission.user_id == user.id:
                return True, None
            if is_user_student_rep(user):
                rep_classes = Class.objects.filter(
                    Q(dqc_member=user) | Q(dqc_member__email__iexact=user.email)
                )
                if submission.user and submission.user.class_name in rep_classes:
                    return True, None
            return False, "You do not have permission to access another student's evidence file."

        return False, "Unauthorized access."

    def get(self, request, pk):
        try:
            submission = Submission.objects.select_related('user', 'user__class_name', 'user__department').get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found."}, status=status.HTTP_404_NOT_FOUND)

        allowed, err_msg = self._check_access_permission(request.user, submission, action="view")
        if not allowed:
            return Response({"error": err_msg or "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        proof_path = submission.proof
        if not proof_path and isinstance(submission.evidence, dict):
            proof_path = submission.evidence.get('filePath') or submission.evidence.get('proofPath')

        if not proof_path:
            return Response({"error": "No evidence file recorded for this submission."}, status=status.HTTP_404_NOT_FOUND)

        safe_path = resolve_safe_private_path(proof_path)
        if not safe_path:
            # Fallback to MEDIA_ROOT if historical, verifying canonical path
            media_root = os.path.abspath(settings.MEDIA_ROOT)
            candidate = os.path.abspath(os.path.join(media_root, str(proof_path).replace('/', os.sep).lstrip(os.sep)))
            try:
                if os.path.commonpath([media_root, candidate]) == media_root and os.path.isfile(candidate):
                    safe_path = candidate
            except (ValueError, Exception):
                safe_path = None

        if not safe_path or not os.path.isfile(safe_path):
            return Response({"error": "Evidence file not found on disk or path is invalid."}, status=status.HTTP_404_NOT_FOUND)

        import mimetypes
        content_type, _ = mimetypes.guess_type(safe_path)
        if not content_type:
            content_type = 'application/octet-stream'

        filename = os.path.basename(safe_path)
        response = FileResponse(open(safe_path, 'rb'), content_type=content_type)
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    def post(self, request, pk):
        try:
            submission = Submission.objects.select_related('user', 'user__class_name').get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found."}, status=status.HTTP_404_NOT_FOUND)

        allowed, err_msg = self._check_access_permission(request.user, submission, action="upload")
        if not allowed:
            return Response({"error": err_msg or "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        file_obj = (
            request.FILES.get('file') or
            request.FILES.get('proof_file') or
            request.FILES.get('evidence_file')
        )
        if not file_obj:
            return Response({"error": "No file uploaded. Please provide a file under 'file' or 'proof_file'."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rel_path, sha256_hash, file_size, detected_mime = save_private_evidence_file(
                file_obj,
                academic_year=submission.academic_year or 'general'
            )
        except ValueError as ve:
            return Response({"error": str(ve)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Evidence upload error")
            return Response({"error": f"Failed to save evidence: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        # Remove old evidence file if different
        if submission.proof and submission.proof != rel_path:
            old_safe = resolve_safe_private_path(submission.proof)
            if old_safe and os.path.isfile(old_safe):
                try:
                    os.remove(old_safe)
                except Exception:
                    pass

        submission.proof = rel_path
        submission.proof_hash = sha256_hash
        submission.save(update_fields=['proof', 'proof_hash'])

        create_audit_entry(
            submission=submission,
            actor=request.user,
            stage=1,
            stage_name="Evidence Upload",
            prev_status=submission.status,
            new_status=submission.status,
            comments=f"Uploaded evidence file '{os.path.basename(rel_path)}' (hash: {sha256_hash[:12]}...)",
            request=request
        )

        record_system_audit_event(
            action='EVIDENCE_UPLOAD',
            object_type='Submission',
            object_id=submission.id,
            actor=request.user,
            object_repr=f"Submission #{submission.id} evidence uploaded ({os.path.basename(rel_path)})",
            old_value=None,
            new_value={'proof': rel_path, 'proof_hash': sha256_hash, 'file_size': file_size},
            reason="New evidence document uploaded",
            request=request
        )

        return Response({
            "success": True,
            "proof": rel_path,
            "proofHash": sha256_hash,
            "fileSize": file_size,
            "mimeType": detected_mime
        }, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        try:
            submission = Submission.objects.get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found."}, status=status.HTTP_404_NOT_FOUND)

        allowed, err_msg = self._check_access_permission(request.user, submission, action="delete")
        if not allowed:
            return Response({"error": err_msg or "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        old_proof = submission.proof
        if submission.proof:
            safe_path = resolve_safe_private_path(submission.proof)
            if safe_path and os.path.isfile(safe_path):
                try:
                    os.remove(safe_path)
                except Exception as e:
                    logger.warning(f"Failed to remove file from disk: {e}")

        submission.proof = None
        submission.proof_hash = None
        submission.save(update_fields=['proof', 'proof_hash'])

        create_audit_entry(
            submission=submission,
            actor=request.user,
            stage=1,
            stage_name="Evidence Deletion",
            prev_status=submission.status,
            new_status=submission.status,
            comments="Deleted evidence file",
            request=request
        )

        record_system_audit_event(
            action='EVIDENCE_DELETE',
            object_type='Submission',
            object_id=submission.id,
            actor=request.user,
            object_repr=f"Submission #{submission.id} evidence deleted ({old_proof})",
            old_value={'proof': old_proof},
            new_value=None,
            reason="Evidence document deleted",
            request=request
        )

        return Response({"success": True, "message": "Evidence removed successfully."}, status=status.HTTP_200_OK)


class SystemSettingView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request):
        settings_objs = SystemSetting.objects.all()
        data = {s.key: s.value for s in settings_objs}
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        if not isinstance(request.data, dict):
            return Response({"error": "Payload must be a dictionary of key-value settings."}, status=status.HTTP_400_BAD_REQUEST)
        for key, value in request.data.items():
            clean_k = str(key).strip()
            if not clean_k or len(clean_k) > 100:
                return Response({"error": "Setting key must be non-empty and <= 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
            if isinstance(value, bool):
                val_str = 'true' if value else 'false'
            elif value is None:
                val_str = ''
            else:
                val_str = str(value)
            if len(val_str) > 5000:
                return Response({"error": f"Setting value for '{clean_k}' cannot exceed 5000 characters."}, status=status.HTTP_400_BAD_REQUEST)
            
            old_s = SystemSetting.objects.filter(key=clean_k).first()
            old_val = old_s.value if old_s else None

            SystemSetting.objects.update_or_create(
                key=clean_k,
                defaults={'value': val_str}
            )

            record_system_audit_event(
                action='ADMIN_SETTING_CHANGE',
                object_type='SystemSetting',
                object_id=clean_k,
                actor=request.user,
                object_repr=f"System Setting '{clean_k}'",
                old_value={'value': old_val},
                new_value={'value': val_str},
                reason=f"Setting modified by {getattr(request.user, 'email', '')}",
                request=request
            )
        return Response({"success": True}, status=status.HTTP_200_OK)


class UserGroupListView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request):
        groups = UserGroupModel.objects.all()
        data = [
            {
                "id": g.group_id,
                "name": g.name,
                "description": g.description,
                "members": g.members or []
            }
            for g in groups
        ]
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        group_id = request.data.get('id')
        name = request.data.get('name')
        description = request.data.get('description', '')
        members = request.data.get('members', [])

        if not group_id or not name:
            return Response({"error": "id and name are required"}, status=status.HTTP_400_BAD_REQUEST)

        clean_id = str(group_id).strip()
        clean_name = str(name).strip()
        if len(clean_id) > 100:
            return Response({"error": "id cannot exceed 100 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if len(clean_name) > 150:
            return Response({"error": "name cannot exceed 150 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if description and len(str(description)) > 2000:
            return Response({"error": "description cannot exceed 2000 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(members, list):
            return Response({"error": "members must be a list of email strings."}, status=status.HTTP_400_BAD_REQUEST)

        group, _ = UserGroupModel.objects.update_or_create(
            group_id=clean_id,
            defaults={
                'name': clean_name,
                'description': str(description),
                'members': members
            }
        )

        return Response({
            "id": group.group_id,
            "name": group.name,
            "description": group.description,
            "members": group.members
        }, status=status.HTTP_200_OK)


class UserGroupDetailView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request, pk):
        try:
            g = UserGroupModel.objects.get(group_id=pk)
            return Response({
                "id": g.group_id,
                "name": g.name,
                "description": g.description,
                "members": g.members or []
            }, status=status.HTTP_200_OK)
        except UserGroupModel.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

    def put(self, request, pk):
        try:
            g = UserGroupModel.objects.get(group_id=pk)
            g.name = request.data.get('name', g.name)
            g.description = request.data.get('description', g.description)
            if 'members' in request.data:
                g.members = request.data.get('members')
            g.save()
            return Response({
                "id": g.group_id,
                "name": g.name,
                "description": g.description,
                "members": g.members
            }, status=status.HTTP_200_OK)
        except UserGroupModel.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, request, pk):
        try:
            g = UserGroupModel.objects.get(group_id=pk)
            g.delete()
            return Response({"success": True}, status=status.HTTP_200_OK)
        except UserGroupModel.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)


from .models import CriteriaCategory, CriteriaItem, CriteriaRule, CriteriaVersion
from .serializers import CriteriaCategorySerializer, CriteriaItemSerializer, CriteriaRuleSerializer, CriteriaVersionSerializer

class CriteriaVersionListView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request):
        year = request.query_params.get('year', None)
        qs = CriteriaVersion.objects.all()
        if year:
            qs = qs.filter(academic_year=year)
        serializer = CriteriaVersionSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        academic_year = request.data.get('academic_year')
        if not academic_year:
            return Response({"error": "academic_year is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            latest = CriteriaVersion.objects.select_for_update().filter(academic_year=academic_year).order_by('-version').first()
            next_v = (latest.version + 1) if latest else 1

            name = request.data.get('name', f"{academic_year} v{next_v}")
            new_version = CriteriaVersion.objects.create(
                academic_year=academic_year,
                version=next_v,
                name=name,
                is_locked=False
            )

            clone_from_id = request.data.get('clone_from_version_id')
            if clone_from_id:
                source_items = CriteriaItem.objects.filter(version_id=clone_from_id).prefetch_related('rules')
                for src_item in source_items:
                    new_item = CriteriaItem.objects.create(
                        category=src_item.category,
                        version=new_version,
                        title=src_item.title,
                        type=src_item.type,
                        marks=src_item.marks,
                        rules_json=src_item.rules_json
                    )
                    for src_rule in src_item.rules.all():
                        CriteriaRule.objects.create(
                            item=new_item,
                            rule_type=src_rule.rule_type,
                            maximum_marks=src_rule.maximum_marks,
                            min_count=src_rule.min_count,
                            max_count=src_rule.max_count,
                            is_negative=src_rule.is_negative,
                            multiplier=src_rule.multiplier,
                            extra_config=src_rule.extra_config
                        )

        serializer = CriteriaVersionSerializer(new_version)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CriteriaVersionDetailView(APIView):
    permission_classes = [IsAdminRole]

    def put(self, request, pk):
        try:
            cv = CriteriaVersion.objects.get(pk=pk)
        except CriteriaVersion.DoesNotExist:
            return Response({"error": "Criteria version not found."}, status=status.HTTP_404_NOT_FOUND)

        old_locked = cv.is_locked
        if 'is_locked' in request.data:
            cv.is_locked = bool(request.data['is_locked'])
            if cv.is_locked and not cv.published_at:
                from django.utils import timezone
                cv.published_at = timezone.now()

        if 'name' in request.data:
            cv.name = request.data['name']

        cv.save()

        record_system_audit_event(
            action='CRITERIA_CHANGE',
            object_type='CriteriaVersion',
            object_id=cv.id,
            actor=request.user,
            object_repr=f"CriteriaVersion {cv.name} ({cv.academic_year})",
            old_value={'is_locked': old_locked},
            new_value={'is_locked': cv.is_locked, 'name': cv.name},
            reason=f"Criteria version modified by {getattr(request.user, 'email', '')}",
            request=request
        )

        serializer = CriteriaVersionSerializer(cv)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CriteriaCategoryListView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request):
        categories = CriteriaCategory.objects.prefetch_related('items').all().order_by('id')
        serializer = CriteriaCategorySerializer(categories, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = CriteriaCategorySerializer(data=request.data)
        if serializer.is_valid():
            cat = serializer.save()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaCategory',
                object_id=cat.code or cat.id,
                actor=request.user,
                object_repr=f"CriteriaCategory '{cat.name}' ({cat.code})",
                old_value=None,
                new_value=serializer.data,
                reason=f"Criteria category created by {getattr(request.user, 'email', '')}",
                request=request
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class CriteriaCategoryDetailView(APIView):
    permission_classes = [IsAdminRole]

    def put(self, request, pk):
        try:
            if str(pk).isdigit():
                category = CriteriaCategory.objects.get(pk=int(pk))
            else:
                category = CriteriaCategory.objects.get(code=pk)
        except CriteriaCategory.DoesNotExist:
            return Response({"error": "Category not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = CriteriaCategorySerializer(category, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaCategory',
                object_id=category.code or category.id,
                actor=request.user,
                object_repr=f"CriteriaCategory '{category.name}'",
                old_value=None,
                new_value=serializer.data,
                reason=f"Criteria category updated by {getattr(request.user, 'email', '')}",
                request=request
            )
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        try:
            if str(pk).isdigit():
                category = CriteriaCategory.objects.get(pk=int(pk))
            else:
                category = CriteriaCategory.objects.get(code=pk)
            cat_name = category.name
            category.delete()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaCategory',
                object_id=pk,
                actor=request.user,
                object_repr=f"CriteriaCategory '{cat_name}' deleted",
                old_value={'name': cat_name},
                new_value=None,
                reason=f"Criteria category deleted by {getattr(request.user, 'email', '')}",
                request=request
            )
        except CriteriaCategory.DoesNotExist:
            pass
        return Response({"success": True}, status=status.HTTP_200_OK)


class CriteriaItemListView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        serializer = CriteriaItemSerializer(data=request.data)
        if serializer.is_valid():
            item = serializer.save()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaItem',
                object_id=item.id,
                actor=request.user,
                object_repr=f"CriteriaItem #{item.id} '{item.title}'",
                old_value=None,
                new_value=serializer.data,
                reason=f"Criteria item created by {getattr(request.user, 'email', '')}",
                request=request
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CriteriaItemDetailView(APIView):
    permission_classes = [IsAdminRole]

    def put(self, request, pk):
        try:
            item = CriteriaItem.objects.get(pk=pk)
        except CriteriaItem.DoesNotExist:
            return Response({"error": "Item not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = CriteriaItemSerializer(item, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaItem',
                object_id=item.id,
                actor=request.user,
                object_repr=f"CriteriaItem #{item.id} '{item.title}'",
                old_value=None,
                new_value=serializer.data,
                reason=f"Criteria item updated by {getattr(request.user, 'email', '')}",
                request=request
            )
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        try:
            item = CriteriaItem.objects.get(pk=pk)
            title = item.title
            item.delete()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaItem',
                object_id=pk,
                actor=request.user,
                object_repr=f"CriteriaItem #{pk} '{title}' deleted",
                old_value={'title': title},
                new_value=None,
                reason=f"Criteria item deleted by {getattr(request.user, 'email', '')}",
                request=request
            )
        except CriteriaItem.DoesNotExist:
            pass
        return Response({"success": True}, status=status.HTTP_200_OK)


class UserGroupDetailView(APIView):
    permission_classes = [IsAdminRole]
    
    def put(self, request, pk):
        try:
            group = UserGroupModel.objects.get(group_id=pk)
        except UserGroupModel.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        
        name = request.data.get('name', group.name)
        description = request.data.get('description', group.description)
        members = request.data.get('members', group.members)
        
        group.name = name
        group.description = description
        group.members = members
        group.save()
        
        return Response({
            "id": group.group_id,
            "name": group.name,
            "description": group.description,
            "members": group.members
        }, status=status.HTTP_200_OK)
        
    def delete(self, request, pk):
        try:
            group = UserGroupModel.objects.get(group_id=pk)
            group.delete()
        except UserGroupModel.DoesNotExist:
            pass
        return Response({"success": True}, status=status.HTTP_200_OK)


class ChampionListView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        champions = Champion.objects.all()
        serializer = ChampionSerializer(champions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        image = request.FILES.get('image')
        if image:
            is_valid, err, _ = validate_file_upload(image, ALLOWED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE_BYTES)
            if not is_valid:
                return Response({'error': err, 'image': [err]}, status=status.HTTP_400_BAD_REQUEST)
        serializer = ChampionSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ChampionDetailView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def put(self, request, pk):
        try:
            champion = Champion.objects.get(pk=pk)
        except Champion.DoesNotExist:
            return Response({'error': 'Champion not found'}, status=status.HTTP_404_NOT_FOUND)

        image = request.FILES.get('image')
        if image:
            is_valid, err, _ = validate_file_upload(image, ALLOWED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE_BYTES)
            if not is_valid:
                return Response({'error': err, 'image': [err]}, status=status.HTTP_400_BAD_REQUEST)

        serializer = ChampionSerializer(champion, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        try:
            champion = Champion.objects.get(pk=pk)
            champion.delete()
        except Champion.DoesNotExist:
            pass
        return Response({'success': True}, status=status.HTTP_200_OK)


class BugReportView(APIView):
    """
    API endpoint for submitting and retrieving system bug & issue reports.
    Permits public creation so anyone facing login or access issues can still report.
    """
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        user = request.user
        if not (user and user.is_authenticated and (getattr(user, 'role', None) in ('admin', 'iqac') or user.is_staff or user.is_superuser)):
            return Response(
                {"error": "Authentication required. Only administrators and IQAC coordinators can view bug reports."},
                status=status.HTTP_401_UNAUTHORIZED if not (user and user.is_authenticated) else status.HTTP_403_FORBIDDEN
            )
        reports = BugReport.objects.all()
        status_filter = request.query_params.get('status')
        if status_filter:
            reports = reports.filter(status=status_filter)
        serializer = BugReportSerializer(reports[:50], many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        screenshot = request.FILES.get('screenshot')
        if screenshot:
            is_valid, err, _ = validate_file_upload(screenshot, ALLOWED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE_BYTES)
            if not is_valid:
                return Response({'error': err, 'screenshot': [err]}, status=status.HTTP_400_BAD_REQUEST)

        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        
        # Auto-fill reporter info if user is authenticated
        if request.user and request.user.is_authenticated:
            if not data.get('reporter_email'):
                data['reporter_email'] = request.user.email
            if not data.get('reporter_name'):
                data['reporter_name'] = request.user.get_full_name() or request.user.email

        serializer = BugReportSerializer(data=data)
        if serializer.is_valid():
            report = serializer.save()
            logger.info(f"New Bug Report filed #{report.id}: {report.title} [{report.priority}]")
            safe_data = BugReportSafeSerializer(report).data
            return Response(safe_data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SystemAuditLogView(APIView):
    """
    Read-only institutional audit ledger endpoint.
    Strictly restricted to Admin and IQAC coordinators.
    No modifications or deletions are allowed via this or any endpoint.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user_role = getattr(user, 'role', '')
        if not (user.is_superuser or user_role in ('admin', 'iqac')):
            return Response(
                {"error": "Unauthorized: Only administrators and IQAC coordinators can inspect the system audit trail."},
                status=status.HTTP_403_FORBIDDEN
            )

        qs = SystemAuditLog.objects.all()
        action = request.query_params.get('action')
        object_type = request.query_params.get('object_type')
        object_id = request.query_params.get('object_id')
        actor_email = request.query_params.get('actor_email')

        if action:
            qs = qs.filter(action=action)
        if object_type:
            qs = qs.filter(object_type=object_type)
        if object_id:
            qs = qs.filter(object_id=object_id)
        if actor_email:
            qs = qs.filter(actor_email__iexact=actor_email)

        limit = min(int(request.query_params.get('limit', 100)), 500)
        serializer = SystemAuditLogSerializer(qs[:limit], many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SubmissionAuditTrailView(APIView):
    """
    Read-only submission-specific audit trail endpoint.
    Authorized for the submission owner, class teacher, student rep for the class, and staff/admin.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            submission = Submission.objects.select_related('user', 'user__class_name').get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        user_role = getattr(user, 'role', '')
        is_owner = bool(submission.user_id == user.id)

        allowed = False
        if user.is_superuser or user_role in ('admin', 'iqac', 'evaluation'):
            allowed = True
        elif is_owner:
            allowed = True
        elif user_role == 'faculty':
            advised_classes = Class.objects.filter(class_teacher=user)
            if submission.user and submission.user.class_name in advised_classes:
                allowed = True
            elif submission.user and user.department_id and submission.user.department_id == user.department_id:
                allowed = True
        elif user_role == 'student' and is_user_student_rep(user):
            rep_classes = Class.objects.filter(
                Q(dqc_member=user) | Q(dqc_member__email__iexact=user.email)
            )
            if submission.user and submission.user.class_name in rep_classes:
                allowed = True

        if not allowed:
            return Response({"error": "You do not have permission to view this submission's audit trail."}, status=status.HTTP_403_FORBIDDEN)

        trails = WorkflowAuditTrail.objects.filter(submission=submission).order_by('created_at')
        serializer = WorkflowAuditTrailSerializer(trails, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


