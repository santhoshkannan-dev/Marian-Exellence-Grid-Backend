import logging
from django.db.models import Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from users.models import SystemSetting, UserGroupModel, BugReport, SystemAuditLog, Submission, Class, WorkflowAuditTrail
from users.serializers import BugReportSerializer, BugReportSafeSerializer, SystemAuditLogSerializer, WorkflowAuditTrailSerializer
from users.permissions import IsAdminOrReadOnly
from users.file_security import validate_file_upload, ALLOWED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE_BYTES
from .base import record_system_audit_event, is_user_student_rep, is_staff_email, is_student_email

logger = logging.getLogger(__name__)


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

        if clean_id in ['grp-evaluators', 'grp-evaluation-committee'] or 'evaluat' in clean_name.lower() or 'evaluat' in clean_id.lower():
            return Response(
                {"error": "Evaluators cannot be managed through User Groups. Please use Evaluator Management."},
                status=status.HTTP_400_BAD_REQUEST
            )

        is_class_teachers_group = (clean_id == 'grp-class-teachers' or 'class teacher' in clean_name.lower())
        if is_class_teachers_group and members and isinstance(members, list):
            invalid_staff_emails = [m for m in members if isinstance(m, str) and not is_staff_email(m)]
            if invalid_staff_emails:
                return Response(
                    {"error": f"Class Teachers Council only permits staff emails (name.name@mariancollege.org, e.g. kochumol.abraham@mariancollege.org). Student emails are not permitted. Invalid emails: {', '.join(invalid_staff_emails)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        is_dqc_group = (
            clean_id == 'grp-dqc-student-rep' or
            'dqc' in clean_name.lower() or 'dac' in clean_name.lower() or
            'dqc' in clean_id.lower() or 'dac' in clean_id.lower()
        )
        if is_dqc_group and members and isinstance(members, list):
            invalid_student_emails = [m for m in members if isinstance(m, str) and not is_student_email(m)]
            if invalid_student_emails:
                return Response(
                    {"error": f"DQC Student Rep Group only permits student emails (name.startingwithnumber@mariancollege.org, e.g. amal.25pmc114@mariancollege.org). Staff emails are not permitted. Invalid emails: {', '.join(invalid_student_emails)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        is_student_rep_group = (
            clean_id == 'grp-student-reps' or
            'student rep' in clean_name.lower() or
            'student rep' in clean_id.lower()
        ) and not is_dqc_group
        if is_student_rep_group and members and isinstance(members, list):
            invalid_student_emails = [m for m in members if isinstance(m, str) and not is_student_email(m)]
            if invalid_student_emails:
                return Response(
                    {"error": f"Student Rep Group only permits student emails (name.startingwithnumber@mariancollege.org, e.g. amal.25pmc114@mariancollege.org). Staff emails are not permitted. Invalid emails: {', '.join(invalid_student_emails)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

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
            name = request.data.get('name', g.name)
            description = request.data.get('description', g.description)
            members = request.data.get('members', g.members)

            is_class_teachers_group = (pk == 'grp-class-teachers' or 'class teacher' in str(name).lower() or 'class teacher' in str(g.name).lower())
            if is_class_teachers_group and members and isinstance(members, list):
                invalid_staff_emails = [m for m in members if isinstance(m, str) and not is_staff_email(m)]
                if invalid_staff_emails:
                    return Response(
                        {"error": f"Class Teachers Council only permits staff emails (name.name@mariancollege.org, e.g. kochumol.abraham@mariancollege.org). Student emails are not permitted. Invalid emails: {', '.join(invalid_staff_emails)}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            is_dqc_group = (
                pk == 'grp-dqc-student-rep' or
                'dqc' in str(name).lower() or 'dqc' in str(g.name).lower() or
                'dac' in str(name).lower() or 'dac' in str(g.name).lower() or
                'dqc' in str(pk).lower() or 'dac' in str(pk).lower()
            )
            if is_dqc_group and members and isinstance(members, list):
                invalid_student_emails = [m for m in members if isinstance(m, str) and not is_student_email(m)]
                if invalid_student_emails:
                    return Response(
                        {"error": f"DQC Student Rep Group only permits student emails (name.startingwithnumber@mariancollege.org, e.g. amal.25pmc114@mariancollege.org). Staff emails are not permitted. Invalid emails: {', '.join(invalid_student_emails)}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            is_student_rep_group = (
                pk == 'grp-student-reps' or
                'student rep' in str(name).lower() or 'student rep' in str(g.name).lower() or
                'student rep' in str(pk).lower()
            ) and not is_dqc_group
            if is_student_rep_group and members and isinstance(members, list):
                invalid_student_emails = [m for m in members if isinstance(m, str) and not is_student_email(m)]
                if invalid_student_emails:
                    return Response(
                        {"error": f"Student Rep Group only permits student emails (name.startingwithnumber@mariancollege.org, e.g. amal.25pmc114@mariancollege.org). Staff emails are not permitted. Invalid emails: {', '.join(invalid_student_emails)}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            g.name = name
            g.description = description
            if 'members' in request.data:
                g.members = members
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
