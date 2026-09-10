import logging

logger = logging.getLogger(__name__)

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
    'Locked': []  # Locked is terminal
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


from users.audit import record_system_audit_event
from users.services.user_service import UserService
from users.services.submission_service import SubmissionService

determine_role_from_email = UserService.determine_role_from_email
parse_name_from_email = UserService.parse_name_from_email
parse_email_code = UserService.parse_email_code
get_active_year_start = UserService.get_active_year_start
get_year_roman = UserService.get_year_roman
parse_student_email = UserService.parse_student_email
allocate_student_from_email = UserService.allocate_student_from_email
get_tokens_for_user = UserService.get_tokens_for_user
is_user_student_rep = UserService.is_user_student_rep
is_staff_email = UserService.is_staff_email
is_student_email = UserService.is_student_email
is_user_dqc_rep = UserService.is_user_dqc_rep
get_student_rep_classes = UserService.get_student_rep_classes

check_duplicate_submission = SubmissionService.check_duplicate_submission
get_online_courses_item_ids = SubmissionService.get_online_courses_item_ids
get_upsc_psc_item_ids = SubmissionService.get_upsc_psc_item_ids
get_criteria_allowed_bounds = SubmissionService.get_criteria_allowed_bounds
