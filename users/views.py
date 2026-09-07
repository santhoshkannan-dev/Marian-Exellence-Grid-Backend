
from .models import Champion
from .serializers import ChampionSerializer
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
import logging
import hashlib
from datetime import datetime
from django.conf import settings

logger = logging.getLogger(__name__)

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny

from rest_framework_simplejwt.tokens import RefreshToken

try:
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
except ImportError:
    id_token = None
    google_requests = None

from django.db import transaction
from django.db.models import Q, Sum
from .models import User, Class, Department, Course, Submission, AcademicYear, SystemSetting, UserGroupModel, CriteriaCategory, CriteriaItem, CriteriaRule


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
        if user_email in ('santhosh.25pmc152@mariancollege.org', 'santhosh.25ubc154@mariancollege.org'):
            return True
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
                {"error": f"Invalid Google token: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response(
                {"error": f"Google authentication failed: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
            return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": "Invalid or expired refresh token."}, status=status.HTTP_400_BAD_REQUEST)


class DevBypassLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = 'login'

    def post(self, request):

        if not (settings.DEBUG and getattr(settings, 'ENABLE_DEV_BYPASS', False)):
            return Response(
                {"error": "Developer bypass login is disabled in this environment."},
                status=status.HTTP_403_FORBIDDEN
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
            
            # Allow frontend to override the role for testing specific flows with one user
            if override_role and user.role != override_role:
                user.role = override_role
                user.save(update_fields=['role'])
                
            # Fix incorrect role assignment for special users in dev environment
            if email == 'admin@mariancollege.org' and user.role != 'admin':
                user.role = 'admin'
                user.save(update_fields=['role'])
            elif email == 'iqac@mariancollege.org' and user.role != 'iqac':
                user.role = 'iqac'
                user.save(update_fields=['role'])
        except User.DoesNotExist:
            if email == 'admin@mariancollege.org':
                user = User.objects.create(username=email, email=email, first_name="System", last_name="Administrator", role='admin', is_staff=True, is_superuser=True)
            elif email == 'iqac@mariancollege.org':
                user = User.objects.create(username=email, email=email, first_name="IQAC", last_name="Coordinator", role='iqac', is_staff=True)
            elif email == 'kochumol.abraham@mariancollege.org':
                user = User.objects.create(username=email, email=email, first_name="Kochumol", last_name="Abraham", role=override_role or 'faculty', is_staff=True)
            elif email == 'allen.george@mariancollege.org':
                user = User.objects.create(username=email, email=email, first_name="Allen", last_name="George", role=override_role or 'evaluation', is_staff=True)
            else:
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
                        {"error": "User not found. Only student accounts can be auto-created."},
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

        if name:
            parts = name.strip().split(' ', 1)
            user.first_name = parts[0]
            if len(parts) > 1:
                user.last_name = parts[1]
            else:
                user.last_name = ""

        if class_name_str:
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
    permission_classes = [AllowAny]

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

        ay, created = AcademicYear.objects.get_or_create(year=year_str)
        if is_active:
            AcademicYear.objects.all().update(is_active=False)
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

        try:
            ay = AcademicYear.objects.get(year=year_str)
        except AcademicYear.DoesNotExist:
            ay = AcademicYear.objects.create(year=year_str, is_active=is_active)

        if is_active:
            AcademicYear.objects.all().update(is_active=False)
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
    permission_classes = [AllowAny]

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
        if not code:
            code = ''.join([w[0] for w in name.split()]).upper()[:5] or "DEPT"

        dept, created = Department.objects.get_or_create(
            code=code,
            defaults={"name": name, "email_prefix": email_prefix, "level": level}
        )
        if not created:
            dept.name = name
            dept.email_prefix = email_prefix
            dept.level = level
            dept.save(update_fields=['name', 'email_prefix', 'level'])
        from .serializers import DepartmentSerializer
        return Response(DepartmentSerializer(dept).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class DepartmentDetailView(APIView):
    """GET / PUT / DELETE a single Department by its integer pk."""
    permission_classes = [AllowAny]

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
        dept.name = name
        dept.code = code
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
    permission_classes = [AllowAny]

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
            duration_years=int(duration_years),
        )
        from .serializers import CourseSerializer
        return Response(CourseSerializer(course).data, status=status.HTTP_201_CREATED)


class CourseDetailView(APIView):
    """GET / PUT / DELETE a single Course by pk."""
    permission_classes = [AllowAny]

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
        course.name = request.data.get('name', course.name)
        course.abbreviation = request.data.get('abbreviation', course.abbreviation).upper()
        course.email_code = request.data.get('email_code', course.email_code).lower()
        course.is_multi_batch = request.data.get('is_multi_batch', course.is_multi_batch)
        course.duration_years = int(request.data.get('duration_years', course.duration_years))
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
    permission_classes = [AllowAny]

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
                "classTeacher": c.class_teacher.email if c.class_teacher else None,
                "classTeacherName": c.class_teacher.get_full_name() or c.class_teacher.username if c.class_teacher else None,
                "dqcMember": c.dqc_member.email if c.dqc_member else None,
                "dqcMemberName": c.dqc_member.get_full_name() or c.dqc_member.username if c.dqc_member else None,
                "num_students": c.num_students,
                "negative_points": c.negative_points,
            }
            for c in classes
        ])

    def post(self, request):
        """Create a new class. Accepts either dept_code (legacy) or course_id + year_number + section."""
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
        name = request.data.get('name')
        teacher_email = request.data.get('classTeacher')
        dqc_email = request.data.get('dqcMember')

        if not name:
            return Response({"error": "Class name is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cls = Class.objects.get(name=name)
        except Class.DoesNotExist:
            return Response({"error": f"Class '{name}' not found"}, status=status.HTTP_404_NOT_FOUND)

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
                    student.is_student_rep = True
                    student.save(update_fields=['is_student_rep'])
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
    permission_classes = [AllowAny]

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
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)
        class_name = cls.name
        cls.delete()
        return Response({"success": True, "deleted": class_name})

    def patch(self, request, pk):
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)

        update_fields = []
        if 'num_students' in request.data:
            try:
                cls.num_students = int(request.data['num_students'])
                update_fields.append('num_students')
            except (ValueError, TypeError):
                return Response({"error": "num_students must be an integer"}, status=status.HTTP_400_BAD_REQUEST)
        if 'negative_points' in request.data:
            try:
                cls.negative_points = abs(float(request.data['negative_points']))
                update_fields.append('negative_points')
            except (ValueError, TypeError):
                return Response({"error": "negative_points must be a number"}, status=status.HTTP_400_BAD_REQUEST)
        if 'name' in request.data:
            cls.name = request.data['name']
            update_fields.append('name')
        if 'year_number' in request.data:
            cls.year_number = int(request.data['year_number'])
            update_fields.append('year_number')
        if 'section' in request.data:
            cls.section = request.data['section'].strip().upper()
            update_fields.append('section')
        if 'batch_start_year' in request.data:
            cls.batch_start_year = int(request.data['batch_start_year']) if request.data['batch_start_year'] else None
            update_fields.append('batch_start_year')

        if update_fields:
            cls.save(update_fields=update_fields)
        return Response(self._serialize_class(cls))

    def put(self, request, pk):
        """Full update — handles class_teacher, dqcMember, and all moderation fields."""
        cls = self._get_cls(pk)
        if not cls:
            return Response({"error": "Class not found"}, status=status.HTTP_404_NOT_FOUND)

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
                    student.is_student_rep = True
                    student.save(update_fields=['is_student_rep'])
                except User.DoesNotExist:
                    return Response({"error": f"Student with email '{dqc_email}' not found"}, status=status.HTTP_404_NOT_FOUND)

        if 'num_students' in request.data:
            try:
                cls.num_students = int(request.data['num_students'])
            except (ValueError, TypeError):
                return Response({"error": "num_students must be an integer"}, status=status.HTTP_400_BAD_REQUEST)
        if 'negative_points' in request.data:
            try:
                cls.negative_points = abs(float(request.data['negative_points']))
            except (ValueError, TypeError):
                return Response({"error": "negative_points must be a number"}, status=status.HTTP_400_BAD_REQUEST)
        if 'name' in request.data:
            cls.name = request.data['name']
        if 'year_number' in request.data:
            cls.year_number = int(request.data['year_number'])
        if 'section' in request.data:
            cls.section = request.data['section'].strip().upper()
        if 'batch_start_year' in request.data:
            cls.batch_start_year = int(request.data['batch_start_year']) if request.data['batch_start_year'] else None
        if 'course' in request.data:
            try:
                cls.course = Course.objects.get(pk=request.data['course'])
            except Course.DoesNotExist:
                return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)

        cls.save()
        return Response(self._serialize_class(cls))



class ClassIndexView(APIView):
    """Compute and return the moderated class index M for all classes.

    Formula: M = (S - P) / (N^2) * (1 + 100 * (N - n))
      S = sum of marks on Locked submissions for the class
      P = Class.negative_points
      N = Class.num_students
      n = SystemSetting['smallest_class_size']

    Query param: ?year=2025-2026 (optional, filters by submission academic_year)
    """
    permission_classes = [AllowAny]

    def get(self, request):
        year = request.query_params.get('year', None)

        # Fetch n (smallest class size) from system settings
        try:
            n_setting = SystemSetting.objects.get(key='smallest_class_size')
            n = float(n_setting.value) if n_setting.value else 0.0
        except SystemSetting.DoesNotExist:
            n = 0.0

        all_classes = Class.objects.select_related('department').all()
        ranked = []
        unranked = []  # classes with N=0

        for cls in all_classes:
            N = cls.num_students
            P = cls.negative_points

            # Build submission queryset for this class
            sub_qs = Submission.objects.filter(
                status__in=['Locked', 'Evaluated'],
                user__class_name=cls,
                marks__isnull=False
            )
            if year:
                sub_qs = sub_qs.filter(academic_year=year)

            S = sub_qs.aggregate(total=Sum('marks'))['total'] or 0.0

            if N > 0:
                M = (S - P) / (N * N) * (1 + 100 * (N - n))
                ranked.append({
                    "class_name": cls.name,
                    "department": cls.department.name,
                    "department_code": cls.department.code,
                    "N": N,
                    "S": round(float(S), 2),
                    "P": round(float(P), 2),
                    "n": n,
                    "M": round(float(M), 4),
                })
            else:
                unranked.append({
                    "class_name": cls.name,
                    "department": cls.department.name,
                    "department_code": cls.department.code,
                    "N": 0,
                    "S": round(float(S), 2),
                    "P": round(float(P), 2),
                    "n": n,
                    "M": None,
                    "rank": None,
                })

        # Sort by M descending and assign ranks
        ranked.sort(key=lambda x: x['M'], reverse=True)
        for i, entry in enumerate(ranked):
            entry['rank'] = i + 1

        return Response(ranked + unranked, status=status.HTTP_200_OK)


class UserManagementView(APIView):
    permission_classes = [AllowAny]

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

        username = email.split('@')[0]
        dept = Department.objects.filter(code=dept_code).first() if dept_code else None
        cls = Class.objects.filter(name=class_name_str).first() if class_name_str else None

        names = name.split(' ', 1)
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
        if not created:
            user.role = role
            user.department = dept
            user.class_name = cls
            user.first_name = first_name
            user.last_name = last_name
            user.save()

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

def get_criteria_allowed_bounds(criteria_item, evidence=None):
    """
    Computes (allowed_min, allowed_max, details) for a given CriteriaItem based on:
    1. Dynamic subItems in rules_json (e.g. Publications, Patents, Book Publications, Prizes, etc.)
    2. Academic grade rules in rules_json (e.g. Class Pass Percentage)
    3. Count multipliers for count-based criteria
    4. Associated CriteriaRule bounds (minimum_marks, maximum_marks, is_negative)
    """
    if not criteria_item:
        return 0.0, 100.0, ""

    ev = evidence
    if isinstance(ev, str):
        try:
            import json
            ev = json.loads(ev)
        except Exception:
            ev = {}
    elif not isinstance(ev, dict):
        ev = {}

    rule = CriteriaRule.objects.filter(item=criteria_item).first()
    
    # Base mark calculation
    base_mark = float(criteria_item.marks or 0.0)
    details = ""

    # 1. SubItems mapping in rules_json (Publications, Patents, Book Publications, Prizes, etc.)
    sub_items = None
    if isinstance(criteria_item.rules_json, dict) and 'subItems' in criteria_item.rules_json:
        sub_items = criteria_item.rules_json.get('subItems')

    if isinstance(sub_items, dict) and len(sub_items) > 0:
        submitted_sub_item = (
            ev.get('subItem') or 
            ev.get('researchSubItem') or 
            ev.get('prizesSubItem')
        )
        matched_val = None
        if submitted_sub_item:
            # Check exact match
            if submitted_sub_item in sub_items:
                matched_val = float(sub_items[submitted_sub_item])
                details = f" (subcategory '{submitted_sub_item}': {matched_val})"
            else:
                # Case-insensitive / trimmed match
                sub_norm = str(submitted_sub_item).strip().lower()
                for k, v in sub_items.items():
                    if str(k).strip().lower() == sub_norm:
                        matched_val = float(v)
                        details = f" (subcategory '{k}': {matched_val})"
                        break
        
        if matched_val is not None:
            base_mark = matched_val
        else:
            # Fallback to maximum mark among defined subItems if specific sub-item not identified
            base_mark = float(max(sub_items.values()))
            details = f" (max subcategory: {base_mark})"

    # 2. Count multiplier for count-based items
    count_val = 1
    if criteria_item.type == 'count' or 'count' in ev:
        try:
            count_val = max(1, int(ev.get('count', 1)))
        except (ValueError, TypeError):
            count_val = 1

    allowed_max = base_mark * count_val

    # 3. Dynamic handling for Academic Grades
    if criteria_item.type == 'academic_grades' or (
        isinstance(criteria_item.rules_json, dict) and 'pass_percentage_ranges' in criteria_item.rules_json
    ):
        rules = criteria_item.rules_json or {}
        m90 = float(rules.get('90_above', 5.0))
        m80 = float(rules.get('80_90', 4.0))
        m70 = float(rules.get('70_80', 3.0))
        ranges = rules.get('pass_percentage_ranges', [])
        max_pass_mark = max([float(r.get('marks', 0)) for r in ranges], default=5.0) if ranges else 5.0
        
        total_students = 100
        try:
            total_students = max(1, int(ev.get('totalStudents', 100)))
        except (ValueError, TypeError):
            total_students = 100
        allowed_max = (total_students * max(m90, m80, m70)) + max_pass_mark
        details = " (academic grades breakdown)"

    # 4. CriteriaRule overrides/caps
    allowed_min = 0.0
    is_negative = (criteria_item.type in ('negative', 'academic_grades')) or (rule and rule.is_negative)
    if rule:
        if rule.maximum_marks is not None:
            rule_max = float(rule.maximum_marks)
            if allowed_max > 0:
                allowed_max = min(allowed_max, rule_max)
            else:
                allowed_max = rule_max
        if rule.minimum_marks is not None:
            allowed_min = float(rule.minimum_marks)
        if rule.is_negative:
            is_negative = True

    if is_negative and allowed_min == 0.0:
        allowed_min = -1000.0

    return allowed_min, allowed_max, details


class SubmissionListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        user = request.user
        email_param = request.query_params.get('email')
        
        # Return all submissions to support real-time peer group verification and multi-role evaluation
        queryset = Submission.objects.all()
            
        academic_year = request.query_params.get('academicYear')
        if academic_year:
            queryset = queryset.filter(academic_year=academic_year)
            
        data = []
        for s in queryset:
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
                "evaluatorVerifiedByName": s.evaluator_verified_by_name,
                "evaluatorRemarks": s.evaluator_remarks
            })
        return Response(data)

    def post(self, request):
        user = request.user
        email = request.data.get('email')
        if not user.is_authenticated or (email and user.email != email):
            if email:
                user = User.objects.filter(email=email).first()
            if not user:
                user = User.objects.filter(role='student').first() or User.objects.first()

        criteria_id = request.data.get('criteriaId')
        academic_year = request.data.get('academicYear', '2025-2026')
        description = request.data.get('description', '')
        status_val = request.data.get('status', 'Pending Verification')
        remarks = request.data.get('remarks', '')
        marks = request.data.get('marks')
        if marks is not None and user and getattr(user, 'role', None) == 'student':
            marks = None
        elif marks is not None:
            try:
                req_marks = float(marks)
                criteria_item = CriteriaItem.objects.filter(pk=criteria_id).first()
                if criteria_item:
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
        proof = request.data.get('proof', '')
        event_id = request.data.get('eventId', '')
        evidence = request.data.get('evidence')
        start_date = request.data.get('start_date') or request.data.get('startDate')
        if not start_date and isinstance(evidence, dict):
            start_date = evidence.get('startDate') or evidence.get('examDate')
        end_date = request.data.get('end_date') or request.data.get('endDate')
        if not end_date and isinstance(evidence, dict):
            end_date = evidence.get('endDate')
        
        if not criteria_id:
            return Response({"error": "criteriaId is required"}, status=status.HTTP_400_BAD_REQUEST)
            
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
        
        proof_h = request.data.get('proofHash')
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

        try:
            criteria_id_int = int(criteria_id)
        except (ValueError, TypeError):
            criteria_id_int = abs(int(hashlib.md5(str(criteria_id).encode()).hexdigest(), 16)) % 1000000

        try:
            submission = Submission.objects.create(
                user=user,
                criteria_id=criteria_id_int,
                academic_year=academic_year,
                description=description,
                status=status_val,
                remarks=remarks,
                marks=marks,
                proof=proof,
                proof_hash=proof_h,
                certificate_id=cert_id,
                event_id=event_id,
                evidence=evidence,
                start_date=start_date,
                end_date=end_date
            )
        except Exception as e:
            return Response({"error": f"Failed to create submission: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Sync relational models (AcademicGradeBreakdown & WorkflowAuditTrail)
        try:
            from users.models import AcademicGradeBreakdown, WorkflowAuditTrail
            sub_evidence = evidence or {}
            sub_type = sub_evidence.get("submissionType")
            if sub_type:
                submission.submission_type = sub_type
                submission.save(update_fields=["submission_type"])
            grades = sub_evidence.get("grades")
            if isinstance(grades, dict):
                AcademicGradeBreakdown.objects.update_or_create(
                    submission=submission,
                    defaults={
                        "s_grade_count": grades.get("S", 0),
                        "a_plus_grade_count": grades.get("APlus", 0),
                        "a_grade_count": grades.get("A", 0),
                        "failed_count": grades.get("Fail", 0),
                        "class_pass_percentage": sub_evidence.get("classPassPercentage", 0.0),
                        "total_students": sub_evidence.get("totalStudents", 0)
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
        except Exception as e:
            logger.warning(f"Error syncing relational models for submission #{submission.id}: {e}")
        
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
    permission_classes = [AllowAny]

    def put(self, request, pk):
        user = request.user
        if not user or not getattr(user, 'is_authenticated', False):
            email = request.data.get('email')
            user = User.objects.filter(email=email).first() if email else None
            if not user or not getattr(user, 'is_authenticated', False):
                user = User.objects.first()

        try:
            submission = Submission.objects.get(pk=pk)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found"}, status=status.HTTP_404_NOT_FOUND)

        # Check online courses & UPSC/PSC limits on update if changing criteriaId or status
        target_criteria_id = int(request.data.get('criteriaId', submission.criteria_id))
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
        # Academic Grade Breakdown Validation on update
        upd_ev = request.data.get('evidence', submission.evidence)
        if isinstance(upd_ev, dict) and "grades" in upd_ev:
            grades_data = upd_ev.get("grades") or {}
            s_cnt = int(grades_data.get("S", 0))
            ap_cnt = int(grades_data.get("APlus", 0))
            a_cnt = int(grades_data.get("A", 0))
            fail_cnt = int(grades_data.get("Fail", 0))
            t_students = int(upd_ev.get("totalStudents", 0))

            if s_cnt < 0 or ap_cnt < 0 or a_cnt < 0 or fail_cnt < 0 or t_students < 0:
                return Response({"error": "Grade counts and total students cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

            g_sum = s_cnt + ap_cnt + a_cnt + fail_cnt
            if t_students <= 0:
                t_students = max(1, g_sum)
                upd_ev["totalStudents"] = t_students

            if g_sum > t_students:
                return Response({"error": f"Sum of student grades ({g_sum}) exceeds total class students ({t_students})."}, status=status.HTTP_400_BAD_REQUEST)

            passed = max(0, t_students - fail_cnt)
            pass_pct = round((passed / float(t_students)) * 100.0, 2)
            upd_ev["classPassPercentage"] = pass_pct
            upd_ev["passCount"] = passed

        # 1. Locked Record Guard
        if submission.status == 'Locked':
            return Response(
                {"error": "This submission record has been locked and cannot be modified."},
                status=status.HTTP_403_FORBIDDEN
            )

        # 1b. Workflow State Machine Transition Guard
        if target_status != submission.status:
            allowed_transitions = VALID_STATE_TRANSITIONS.get(submission.status, [])
            if target_status not in allowed_transitions:
                return Response(
                    {"error": f"Invalid workflow state transition from '{submission.status}' to '{target_status}'."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # 1c. Student Evidence Locking Guard
        user_role = getattr(user, 'role', None)
        if user and user_role == 'student' and submission.status in UNEDITABLE_BY_STUDENT_STATES:
            evidence_fields = {'description', 'evidence', 'proof', 'criteriaId', 'start_date', 'startDate', 'end_date', 'endDate'}
            if any(f in request.data for f in evidence_fields):
                return Response(
                    {"error": f"Submissions in '{submission.status}' state cannot have evidence edited by students."},
                    status=status.HTTP_403_FORBIDDEN
                )

        # 2. Authorization Check: Students cannot assign marks. Regular students cannot alter verification/evaluation status.
        # Class Representatives (Student Reps) are authorized to transition status to: 'Student Rep Verified', 'Correction Requested', 'Rejected', 'Pending Rep Verification'.
        if user and user_role == 'student':
            if 'marks' in request.data and request.data.get('marks') is not None:
                return Response(
                    {"error": "Unauthorized: Students cannot assign evaluation marks."},
                    status=status.HTTP_403_FORBIDDEN
                )

            req_status = request.data.get('status')
            if req_status and req_status != submission.status:
                is_rep = is_user_student_rep(user)
                allowed_rep_statuses = {'Student Rep Verified', 'Correction Requested', 'Rejected', 'Pending Rep Verification', 'Pending', 'Submitted'}
                if is_rep and req_status in allowed_rep_statuses:
                    pass  # Authorized Class Representative verification action!
                elif req_status in ('Approved', 'Verified', 'Teacher Verified', 'Student Rep Verified', 'Evaluated', 'Locked'):
                    return Response(
                        {"error": "Unauthorized: Students cannot alter verification or evaluation status."},
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
        with transaction.atomic():
            if 'criteriaId' in request.data:
                submission.criteria_id = int(request.data.get('criteriaId'))
            if 'academicYear' in request.data:
                submission.academic_year = request.data.get('academicYear')
            if 'description' in request.data:
                submission.description = request.data.get('description')
            prev_status = submission.status
            if 'status' in request.data:
                submission.status = request.data.get('status')
                if user and user.role != 'student':
                    submission.verified_by_name = user.get_full_name() or user.username
            if 'verifiedByName' in request.data and request.data.get('verifiedByName'):
                submission.verified_by_name = request.data.get('verifiedByName')
            if 'remarks' in request.data:
                submission.remarks = request.data.get('remarks')
            if 'marks' in request.data:
                submission.marks = request.data.get('marks')
            if 'proof' in request.data:
                submission.proof = request.data.get('proof')
            if 'eventId' in request.data:
                submission.event_id = request.data.get('eventId')
            if 'evidence' in request.data:
                submission.evidence = request.data.get('evidence')
            if 'start_date' in request.data or 'startDate' in request.data:
                submission.start_date = request.data.get('start_date') or request.data.get('startDate')
            elif 'evidence' in request.data and isinstance(request.data.get('evidence'), dict):
                ev = request.data.get('evidence')
                if ev.get('startDate') or ev.get('examDate'):
                    submission.start_date = ev.get('startDate') or ev.get('examDate')

            if 'end_date' in request.data or 'endDate' in request.data:
                submission.end_date = request.data.get('end_date') or request.data.get('endDate')
            elif 'evidence' in request.data and isinstance(request.data.get('evidence'), dict):
                ev = request.data.get('evidence')
                if ev.get('endDate'):
                    submission.end_date = ev.get('endDate')
            if 'repVerifiedByName' in request.data:
                submission.rep_verified_by_name = request.data.get('repVerifiedByName')
            if 'repRemarks' in request.data:
                submission.rep_remarks = request.data.get('repRemarks')
            if 'teacherVerifiedByName' in request.data:
                submission.teacher_verified_by_name = request.data.get('teacherVerifiedByName')
            if 'teacherRemarks' in request.data:
                submission.teacher_remarks = request.data.get('teacherRemarks')
            if 'evaluatorVerifiedByName' in request.data:
                submission.evaluator_verified_by_name = request.data.get('evaluatorVerifiedByName')
            if 'evaluatorRemarks' in request.data:
                submission.evaluator_remarks = request.data.get('evaluatorRemarks')

            submission.save()

            if prev_status != submission.status:
                create_audit_entry(
                    submission=submission,
                    actor=user,
                    stage=3 if user and user.role == 'evaluation' else 2,
                    stage_name="Evaluation Update",
                    prev_status=prev_status,
                    new_status=submission.status,
                    comments=submission.remarks or "Status updated by evaluator/admin",
                    request=request
                )

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
        try:
            submission = Submission.objects.get(pk=pk)
            submission.delete()
        except Submission.DoesNotExist:
            pass
        return Response({"success": True}, status=status.HTTP_200_OK)


class SystemSettingView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        settings_objs = SystemSetting.objects.all()
        data = {s.key: s.value for s in settings_objs}
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        for key, value in request.data.items():
            if isinstance(value, bool):
                val_str = 'true' if value else 'false'
            elif value is None:
                val_str = ''
            else:
                val_str = str(value)
            SystemSetting.objects.update_or_create(
                key=key,
                defaults={'value': val_str}
            )
        return Response({"success": True}, status=status.HTTP_200_OK)


class UserGroupListView(APIView):
    permission_classes = [AllowAny]

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

        group, _ = UserGroupModel.objects.update_or_create(
            group_id=group_id,
            defaults={
                'name': name,
                'description': description,
                'members': members
            }
        )

        return Response({
            "id": group.group_id,
            "name": group.name,
            "description": group.description,
            "members": group.members
        }, status=status.HTTP_200_OK)
from .models import CriteriaCategory, CriteriaItem
from .serializers import CriteriaCategorySerializer, CriteriaItemSerializer

class CriteriaCategoryListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        categories = CriteriaCategory.objects.prefetch_related('items').all().order_by('id')
        serializer = CriteriaCategorySerializer(categories, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = CriteriaCategorySerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class CriteriaCategoryDetailView(APIView):
    permission_classes = [AllowAny]

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
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        try:
            if str(pk).isdigit():
                category = CriteriaCategory.objects.get(pk=int(pk))
            else:
                category = CriteriaCategory.objects.get(code=pk)
            category.delete()
        except CriteriaCategory.DoesNotExist:
            pass
        return Response({"success": True}, status=status.HTTP_200_OK)


class CriteriaItemListView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CriteriaItemSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CriteriaItemDetailView(APIView):
    permission_classes = [AllowAny]

    def put(self, request, pk):
        try:
            item = CriteriaItem.objects.get(pk=pk)
        except CriteriaItem.DoesNotExist:
            return Response({"error": "Item not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = CriteriaItemSerializer(item, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        try:
            item = CriteriaItem.objects.get(pk=pk)
            item.delete()
        except CriteriaItem.DoesNotExist:
            pass
        return Response({"success": True}, status=status.HTTP_200_OK)


class UserGroupDetailView(APIView):
    permission_classes = [AllowAny]
    
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
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        champions = Champion.objects.all()
        serializer = ChampionSerializer(champions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = ChampionSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ChampionDetailView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def put(self, request, pk):
        try:
            champion = Champion.objects.get(pk=pk)
        except Champion.DoesNotExist:
            return Response({'error': 'Champion not found'}, status=status.HTTP_404_NOT_FOUND)

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
