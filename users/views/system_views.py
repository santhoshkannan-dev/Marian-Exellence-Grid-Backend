import logging
from django.db.models import Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from users.models import (
    SystemSetting, UserGroupModel, UserGroupMember, EvaluatorCategoryAssignment,
    Department, User, BugReport, SystemAuditLog, Submission, Class, WorkflowAuditTrail
)
from users.serializers import BugReportSerializer, BugReportSafeSerializer, SystemAuditLogSerializer, WorkflowAuditTrailSerializer
from users.permissions import IsAdminOrReadOnly, IsAdminRole
from users.file_security import validate_file_upload, ALLOWED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE_BYTES
from users.services.user_service import UserService
from .base import record_system_audit_event, is_user_student_rep

logger = logging.getLogger(__name__)

OFFICIAL_USER_GROUPS = {
    'grp-evaluation-committee': {
        'name': 'Evaluation Committee',
        'policy': 'staff_only',
        'allowed_desc': 'Staff only (e.g. name.name@mariancollege.org)',
        'description': 'Evaluator members assigned to review activity submissions.',
        'badge': '',
    },
    'grp-class-teachers': {
        'name': 'Class Teachers Council',
        'policy': 'staff_only',
        'allowed_desc': 'Staff only (e.g. name.name@mariancollege.org)',
        'description': 'Faculty members acting as class advisors.',
        'badge': '',
    },
    'grp-dqc-student-rep': {
        'name': 'DQC Student Rep Group',
        'policy': 'student_only',
        'allowed_desc': 'Student only (e.g. name.YYLCCDXX@mariancollege.org)',
        'description': 'Data Quality Cell student representatives responsible for initial verification of peer submissions, and access to all categories for submissions of class they belong to.',
        'badge': 'DQC member',
    },
    'grp-student-reps': {
        'name': 'Student Representatives',
        'policy': 'student_only',
        'allowed_desc': 'Student only (e.g. name.YYLCCDXX@mariancollege.org)',
        'description': 'Class representatives responsible for initial verification of peer submissions of class they belong to only.',
        'badge': 'Student Rep',
    },
}

OFFICIAL_GROUP_ORDER = [
    'grp-evaluation-committee',
    'grp-class-teachers',
    'grp-dqc-student-rep',
    'grp-student-reps'
]


def validate_group_email_policy(group_id, email):
    clean = str(email).strip().lower()
    if not clean.endswith('@mariancollege.org'):
        return False, "Email must end with @mariancollege.org"
    username_part = clean[:-len('@mariancollege.org')]
    parts = username_part.split('.')
    is_student = (len(parts) >= 2 and any(any(c.isdigit() for c in p) for p in parts)) or (UserService.parse_student_email(clean) is not None)

    if group_id in ('grp-evaluation-committee', 'grp-class-teachers'):
        if is_student:
            return False, "Only staff emails (e.g. name.name@mariancollege.org) are permitted in this group."
        return True, ""
    elif group_id in ('grp-dqc-student-rep', 'grp-student-reps'):
        if not is_student:
            return False, "Only student emails (e.g. name.YYLCCDXX@mariancollege.org) are permitted in this group."
        return True, ""
    return True, ""


def add_member_to_group(group, email):
    clean_email = str(email).strip().lower()
    valid, err_msg = validate_group_email_policy(group.group_id, clean_email)
    if not valid:
        return None, err_msg

    user = User.objects.filter(email__iexact=clean_email).first()
    name = ""
    dept = None
    assigned_class = None
    badge = ""

    if group.group_id == 'grp-dqc-student-rep':
        badge = 'DQC member'
    elif group.group_id == 'grp-student-reps':
        badge = 'Student Rep'

    if user:
        name = user.get_full_name() or user.username
        dept = user.department
        assigned_class = user.class_name
    else:
        parsed = UserService.parse_student_email(clean_email)
        if parsed:
            name = parsed.get('name') or clean_email.split('@')[0]
            if parsed.get('department_code'):
                dept = Department.objects.filter(code=parsed.get('department_code')).first()
            if parsed.get('class_name'):
                assigned_class = Class.objects.filter(name=parsed.get('class_name')).first()
        else:
            name = UserService.parse_name_from_email(clean_email)

    if group.group_id == 'grp-class-teachers' and not assigned_class and user:
        cls_advised = Class.objects.filter(class_teacher=user).first()
        if cls_advised:
            assigned_class = cls_advised

    member, _ = UserGroupMember.objects.update_or_create(
        group=group,
        email=clean_email,
        defaults={
            'user': user,
            'name': name,
            'department': dept,
            'assigned_class': assigned_class,
            'badge': badge,
        }
    )
    group.sync_json_members()
    return member, None


def serialize_user_group(group):
    memberships = group.memberships.select_related('department', 'assigned_class', 'user').prefetch_related('category_assignments__category').all()
    members_data = []
    emails_list = []
    for m in memberships:
        emails_list.append(m.email)
        dept_name = m.department.name if m.department else (m.user.department.name if m.user and m.user.department else None)
        dept_code = m.department.code if m.department else (m.user.department.code if m.user and m.user.department else None)
        cls_name = m.assigned_class.name if m.assigned_class else (m.user.class_name.name if m.user and m.user.class_name else None)
        categories = [
            {"id": ca.category.id, "code": ca.category.code, "name": ca.category.category}
            for ca in m.category_assignments.all()
        ]
        members_data.append({
            "id": m.id,
            "email": m.email,
            "name": m.name or (m.user.get_full_name() if m.user else m.email.split('@')[0]),
            "department": dept_name,
            "department_code": dept_code,
            "assigned_class": cls_name,
            "badge": m.badge,
            "categories": categories,
        })

    policy = 'staff_only' if group.group_id in ('grp-evaluation-committee', 'grp-class-teachers') else 'student_only'
    allowed_desc = 'Staff only (e.g. name.name@mariancollege.org)' if policy == 'staff_only' else 'Student only (e.g. name.YYLCCDXX@mariancollege.org)'

    return {
        "id": group.group_id,
        "name": group.name,
        "description": group.description,
        "policy": policy,
        "allowed_desc": allowed_desc,
        "emails": emails_list,
        "members": emails_list,
        "member_details": members_data,
    }


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
            if len(clean_k) > 100:
                return Response({"error": "Setting key exceeds 100 characters limit."}, status=status.HTTP_400_BAD_REQUEST)
            clean_v = str(value)
            if len(clean_v) > 5000:
                return Response({"error": "Setting value exceeds 5000 characters limit."}, status=status.HTTP_400_BAD_REQUEST)
            SystemSetting.objects.update_or_create(
                key=clean_k,
                defaults={'value': clean_v}
            )
            record_system_audit_event(
                action='ADMIN_SETTING_CHANGE',
                object_type='SystemSetting',
                object_id=clean_k,
                actor=request.user,
                object_repr=f"SystemSetting '{clean_k}'",
                old_value=None,
                new_value={'key': clean_k, 'value': clean_v},
                reason=f"System setting modified by {getattr(request.user, 'email', '')}",
                request=request
            )
        return Response({"success": True}, status=status.HTTP_200_OK)


class UserGroupListView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request):
        # Ensure the 4 official groups always exist
        for gid, ginfo in OFFICIAL_USER_GROUPS.items():
            UserGroupModel.objects.get_or_create(
                group_id=gid,
                defaults={'name': ginfo['name'], 'description': ginfo['description']}
            )

        groups_map = {g.group_id: g for g in UserGroupModel.objects.all()}
        sorted_groups = [groups_map[gid] for gid in OFFICIAL_GROUP_ORDER if gid in groups_map]
        for gid, g in groups_map.items():
            if gid not in OFFICIAL_GROUP_ORDER:
                sorted_groups.append(g)
        data = [serialize_user_group(g) for g in sorted_groups]
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        group_id = request.data.get('id')
        email_to_add = request.data.get('email')
        members = request.data.get('members', [])

        if not group_id:
            return Response({"error": "id is required"}, status=status.HTTP_400_BAD_REQUEST)

        clean_id = str(group_id).strip()
        ginfo = OFFICIAL_USER_GROUPS.get(clean_id)
        grp_name = request.data.get('name') or (ginfo['name'] if ginfo else clean_id)
        grp_desc = request.data.get('description') or (ginfo['description'] if ginfo else '')

        group, _ = UserGroupModel.objects.get_or_create(
            group_id=clean_id,
            defaults={'name': grp_name, 'description': grp_desc}
        )

        if email_to_add:
            member, err = add_member_to_group(group, email_to_add)
            if err:
                return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
            return Response(serialize_user_group(group), status=status.HTTP_200_OK)

        if members and isinstance(members, list):
            for e in members:
                _, err = add_member_to_group(group, e)
                if err:
                    return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
            return Response(serialize_user_group(group), status=status.HTTP_200_OK)

        return Response(serialize_user_group(group), status=status.HTTP_200_OK)


class UserGroupDetailView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request, pk):
        try:
            g = UserGroupModel.objects.get(group_id=pk)
            return Response(serialize_user_group(g), status=status.HTTP_200_OK)
        except UserGroupModel.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

    def put(self, request, pk):
        try:
            g = UserGroupModel.objects.get(group_id=pk)
        except UserGroupModel.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

        add_email = request.data.get('add_email')
        remove_email = request.data.get('remove_email')

        if add_email:
            _, err = add_member_to_group(g, add_email)
            if err:
                return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
            return Response(serialize_user_group(g), status=status.HTTP_200_OK)

        if remove_email:
            clean = str(remove_email).strip().lower()
            UserGroupMember.objects.filter(group=g, email__iexact=clean).delete()
            g.sync_json_members()
            return Response(serialize_user_group(g), status=status.HTTP_200_OK)

        if 'members' in request.data:
            new_emails = [str(e).strip().lower() for e in request.data.get('members', []) if e]
            # Validate all first
            for e in new_emails:
                valid, err = validate_group_email_policy(g.group_id, e)
                if not valid:
                    return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
            # Remove members not in list
            UserGroupMember.objects.filter(group=g).exclude(email__in=new_emails).delete()
            # Add new members
            for e in new_emails:
                add_member_to_group(g, e)
            return Response(serialize_user_group(g), status=status.HTTP_200_OK)

        if 'name' in request.data and g.group_id not in OFFICIAL_USER_GROUPS:
            g.name = request.data.get('name', g.name)
        if 'description' in request.data:
            g.description = request.data.get('description', g.description)
        g.save()
        return Response(serialize_user_group(g), status=status.HTTP_200_OK)

    def delete(self, request, pk):
        try:
            g = UserGroupModel.objects.get(group_id=pk)
        except UserGroupModel.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

        remove_email = request.query_params.get('email')
        member_id = request.query_params.get('member_id')

        if remove_email:
            clean = str(remove_email).strip().lower()
            UserGroupMember.objects.filter(group=g, email__iexact=clean).delete()
            g.sync_json_members()
            return Response({"success": True}, status=status.HTTP_200_OK)

        if member_id:
            UserGroupMember.objects.filter(group=g, id=member_id).delete()
            g.sync_json_members()
            return Response({"success": True}, status=status.HTTP_200_OK)

        if g.group_id in OFFICIAL_USER_GROUPS:
            return Response({
                "error": "Official system user groups cannot be deleted."
            }, status=status.HTTP_400_BAD_REQUEST)

        g.delete()
        return Response({"success": True}, status=status.HTTP_200_OK)


class BugReportView(APIView):
    """
    API endpoint for submitting and retrieving system bug & issue reports.
    Permits public creation so anyone facing login or access issues can still report.
    """
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        user = request.user
        if not (user and user.is_authenticated and (getattr(user, 'role', None) == 'admin' or user.is_staff or user.is_superuser)):
            return Response(
                {"error": "Authentication required. Only administrators can view bug reports."},
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
    Strictly restricted to Administrators.
    No modifications or deletions are allowed via this or any endpoint.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user_role = getattr(user, 'role', '')
        if not (user.is_superuser or user_role == 'admin'):
            return Response(
                {"error": "Unauthorized: Only administrators can inspect the system audit trail."},
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
        if user.is_superuser or user_role in ('admin', 'evaluation'):
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


class UserGroupMemberActionView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        try:
            g = UserGroupModel.objects.get(group_id=pk)
        except UserGroupModel.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        email = request.data.get('email')
        if not email:
            return Response({"error": "email is required"}, status=status.HTTP_400_BAD_REQUEST)
        member, err = add_member_to_group(g, email)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_user_group(g), status=status.HTTP_200_OK)

    def delete(self, request, pk):
        try:
            g = UserGroupModel.objects.get(group_id=pk)
        except UserGroupModel.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        email = request.data.get('email') or request.query_params.get('email')
        if not email:
            return Response({"error": "email is required"}, status=status.HTTP_400_BAD_REQUEST)
        clean = str(email).strip().lower()
        UserGroupMember.objects.filter(group=g, email__iexact=clean).delete()
        g.sync_json_members()
        return Response(serialize_user_group(g), status=status.HTTP_200_OK)

