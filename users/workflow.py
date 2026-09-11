"""
Workflow Engine & State Machine for Marian Excellence Grid
===========================================================
Enforces authoritative institutional state transitions, role-level restrictions,
object-level scoping, evidence locking, rejection tracking, and audit trail integrity.
"""

import logging
from typing import Tuple, Optional, Dict, Any, List
from django.db import transaction
from django.db.models import Q
from .models import User, Class, CriteriaItem, Submission, WorkflowAuditTrail

logger = logging.getLogger(__name__)


class WorkflowState:
    DRAFT = 'Draft'
    SUBMITTED = 'Submitted'
    PENDING_REP_VERIFICATION = 'Pending Rep Verification'
    STUDENT_REP_VERIFIED = 'Student Rep Verified'
    TEACHER_VERIFIED = 'Teacher Verified'
    EVALUATED = 'Evaluated'
    LOCKED = 'Locked'
    CORRECTION_REQUESTED = 'Correction Requested'
    REJECTED = 'Rejected'

    # Aliases
    APPROVED = 'Approved'       # maps to TEACHER_VERIFIED
    VERIFIED = 'Verified'       # maps to TEACHER_VERIFIED
    PENDING = 'Pending'         # maps to PENDING_REP_VERIFICATION
    PENDING_VERIFICATION = 'Pending Verification'
    CORRECTION = 'Correction'   # maps to CORRECTION_REQUESTED
    VERIFIED_BY_STUDENT_REP = 'Verified by Student Rep'

    ALL_CANONICAL = [
        DRAFT,
        SUBMITTED,
        PENDING_REP_VERIFICATION,
        STUDENT_REP_VERIFIED,
        TEACHER_VERIFIED,
        EVALUATED,
        LOCKED,
        CORRECTION_REQUESTED,
        REJECTED,
    ]


ALIAS_MAP = {
    'Approved': WorkflowState.TEACHER_VERIFIED,
    'Verified': WorkflowState.TEACHER_VERIFIED,
    'Pending': WorkflowState.PENDING_REP_VERIFICATION,
    'Pending Verification': WorkflowState.PENDING_REP_VERIFICATION,
    'Correction': WorkflowState.CORRECTION_REQUESTED,
    'Verified by Student Rep': WorkflowState.STUDENT_REP_VERIFIED,
}


def normalize_status(status_str: Optional[str]) -> str:
    """Normalize aliases to canonical workflow states while preserving case."""
    if not status_str:
        return WorkflowState.DRAFT
    s = str(status_str).strip()
    return ALIAS_MAP.get(s, s)


# States in which regular students CANNOT modify evidence, proof, or description
UNEDITABLE_BY_STUDENT_STATES = {
    WorkflowState.STUDENT_REP_VERIFIED,
    WorkflowState.TEACHER_VERIFIED,
    WorkflowState.APPROVED,
    WorkflowState.VERIFIED,
    WorkflowState.EVALUATED,
    WorkflowState.LOCKED,
}


def is_user_student_rep_for_class(user: User, class_obj: Optional[Class]) -> bool:
    """Determine if an authenticated student user is a verified DQC rep or Student Rep for a class."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'role', '') != 'student':
        return False
    if not class_obj:
        return getattr(user, 'is_student_rep', False) or getattr(user, 'is_dqc_member', False)

    user_email = (user.email or '').strip().lower()

    # 1. Direct DQC assignment on the class
    if class_obj.dqc_member_id == user.id:
        return True
    if user_email and class_obj.dqc_member and class_obj.dqc_member.email and class_obj.dqc_member.email.strip().lower() == user_email:
        return True
    if user_email and Class.objects.filter(id=class_obj.id, dqc_member__email__iexact=user_email).exists():
        return True

    # 2. General rep flag on user matching class
    from users.services.user_service import UserService
    is_general_rep = (
        getattr(user, 'is_student_rep', False) or
        getattr(user, 'is_dqc_member', False) or
        UserService.is_user_student_rep(user)
    )
    if is_general_rep and user.class_name_id == class_obj.id:
        return True

    # 3. Check UserGroupMember table
    from users.models import UserGroupMember
    if user_email:
        members = UserGroupMember.objects.filter(
            group__group_id__in=['grp-student-reps', 'grp-dqc-student-rep'],
            email__iexact=user_email
        )
        for m in members:
            if m.assigned_class and m.assigned_class.strip().lower() == class_obj.name.strip().lower():
                return True
        if members.exists() and user.class_name_id == class_obj.id:
            return True
    return False


def is_user_class_advisor(user: User, class_obj: Optional[Class]) -> bool:
    """Determine if an authenticated faculty user is the assigned advisor for a class."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'role', '') == 'admin' or getattr(user, 'is_superuser', False):
        return True
    if getattr(user, 'role', '') not in ('faculty', 'teacher'):
        return False
    if not class_obj:
        return True
    # Primary check: Class.class_teacher
    if class_obj.class_teacher_id == user.id:
        return True
    user_email = (user.email or '').strip().lower()
    if class_obj.class_teacher and class_obj.class_teacher.email.strip().lower() == user_email:
        return True
    from users.models import UserGroupMember
    if user_email:
        ct_member = UserGroupMember.objects.filter(
            group__group_id='grp-class-teachers',
            email__iexact=user_email
        ).first()
        if ct_member and ct_member.assigned_class and ct_member.assigned_class.strip().lower() == class_obj.name.strip().lower():
            return True
    # Secondary check: Department affiliation if no specific teacher is set
    if not class_obj.class_teacher_id and class_obj.department_id and user.department_id == class_obj.department_id:
        return True
    return False


def is_evaluator_assigned_to_item(user: User, criteria_id: int) -> bool:
    """Check if an evaluator is assigned to the criteria item's category."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'role', '') == 'admin' or getattr(user, 'is_superuser', False):
        return True
    user_email = (user.email or '').strip().lower()
    from users.models import UserGroupMember, EvaluatorCategoryAssignment
    is_eval_role = (
        getattr(user, 'role', '') in ('evaluation', 'evaluator') or
        UserGroupMember.objects.filter(group__group_id='grp-evaluation-committee', email__iexact=user_email).exists()
    )
    if not is_eval_role:
        return False
    
    item = CriteriaItem.objects.filter(pk=criteria_id).select_related('category').first()
    if not item or not item.category:
        return True

    if EvaluatorCategoryAssignment.objects.filter(category=item.category, member__email__iexact=user_email).exists():
        return True
    
    cat_evaluators = item.category.evaluators
    if not cat_evaluators:
        return True
    
    allowed_emails = [str(e).strip().lower() for e in cat_evaluators if e]
    return user_email in allowed_emails


def determine_stage(target_status: str, actor: Optional[User]) -> Tuple[int, str]:
    """Map canonical state & actor to institutional 7-stage pipeline number & name."""
    norm = normalize_status(target_status)
    actor_role = getattr(actor, 'role', '') if actor else ''

    if norm in (WorkflowState.DRAFT, WorkflowState.SUBMITTED, WorkflowState.PENDING_REP_VERIFICATION):
        return 1, "Student Claims"
    elif norm == WorkflowState.STUDENT_REP_VERIFIED:
        return 2, "DQC Member Verification"
    elif norm in (WorkflowState.TEACHER_VERIFIED, WorkflowState.APPROVED, WorkflowState.VERIFIED):
        return 3, "Class Teacher Verification"
    elif norm == WorkflowState.EVALUATED:
        return 4, "Central Evaluation"
    elif norm == WorkflowState.LOCKED:
        return 5, "Mark Moderation & Locking"
    elif norm in (WorkflowState.CORRECTION_REQUESTED, WorkflowState.REJECTED):
        # Stage corresponds to the reviewing actor requesting correction / rejecting
        if actor_role == 'student':
            return 2, "DQC Member Verification"
        elif actor_role == 'faculty':
            return 3, "Class Teacher Verification"
        elif actor_role == 'evaluation':
            return 4, "Central Evaluation"
        elif actor_role == 'admin':
            return 5, "Mark Moderation & Locking"
        return 2, "Review Decision"
    return 1, "Workflow Transition"


def validate_workflow_transition(
    submission: Submission,
    target_status_input: str,
    user: User,
    data: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str, int, str]:
    """
    Validate whether the requested state transition is legally permissible under institutional rules.

    Returns:
        (is_valid, error_message, stage_number, stage_name)
    """
    data = data or {}
    current_status = submission.status
    current_norm = normalize_status(current_status)
    target_status = target_status_input
    target_norm = normalize_status(target_status)

    stage_num, stage_name = determine_stage(target_norm, user)

    # 1. Check Authentication
    if not user or not user.is_authenticated:
        return False, "Authentication required to perform workflow transitions.", stage_num, stage_name

    user_role = getattr(user, 'role', '')
    is_admin = user_role == 'admin' or getattr(user, 'is_superuser', False)
    sub_owner = submission.user
    sub_class = sub_owner.class_name if sub_owner else None

    # 2. Terminal Lock Protection
    if current_norm == WorkflowState.LOCKED:
        # Locked submissions are immutable against modifications while locked.
        # Only an authorized admin explicit transition out of Locked is permitted.
        if target_norm == WorkflowState.LOCKED:
            return False, "This submission record has been locked and cannot be modified.", stage_num, stage_name
        if not is_admin:
            return False, "This submission record has been locked by Admin and cannot be modified.", stage_num, stage_name

    # Same-state update: check evidence editing restrictions
    if current_norm == target_norm:
        if user_role == 'student' and current_norm in UNEDITABLE_BY_STUDENT_STATES:
            evidence_fields = {'description', 'evidence', 'proof', 'criteriaId', 'start_date', 'startDate', 'end_date', 'endDate'}
            if any(f in data for f in evidence_fields):
                return False, f"Submissions in '{current_status}' state cannot have evidence edited by students.", stage_num, stage_name
        return True, "", stage_num, stage_name

    # 3. Permitted Transition Table
    # Structure: current_norm -> set of (target_norm, allowed_roles)
    # Roles: 'student_owner', 'student_rep', 'faculty', 'evaluation', 'admin'
    ALLOWED_MATRIX = {
        WorkflowState.DRAFT: {
            WorkflowState.SUBMITTED: {'student_owner', 'admin'},
            WorkflowState.PENDING_REP_VERIFICATION: {'student_owner', 'admin'},
        },
        WorkflowState.SUBMITTED: {
            WorkflowState.DRAFT: {'student_owner', 'admin'},
            WorkflowState.STUDENT_REP_VERIFIED: {'student_rep', 'faculty', 'admin'},
            WorkflowState.TEACHER_VERIFIED: {'faculty', 'admin'},  # Faculty direct verification
            WorkflowState.CORRECTION_REQUESTED: {'student_rep', 'faculty', 'admin'},
            WorkflowState.REJECTED: {'student_rep', 'faculty', 'admin'},
        },
        WorkflowState.PENDING_REP_VERIFICATION: {
            WorkflowState.DRAFT: {'student_owner', 'admin'},
            WorkflowState.STUDENT_REP_VERIFIED: {'student_rep', 'faculty', 'admin'},
            WorkflowState.TEACHER_VERIFIED: {'faculty', 'admin'},
            WorkflowState.CORRECTION_REQUESTED: {'student_rep', 'faculty', 'admin'},
            WorkflowState.REJECTED: {'student_rep', 'faculty', 'admin'},
        },
        WorkflowState.STUDENT_REP_VERIFIED: {
            WorkflowState.TEACHER_VERIFIED: {'faculty', 'admin'},
            WorkflowState.EVALUATED: {'evaluation', 'admin'},
            WorkflowState.CORRECTION_REQUESTED: {'student_rep', 'faculty', 'evaluation', 'admin'},
            WorkflowState.REJECTED: {'student_rep', 'faculty', 'evaluation', 'admin'},
        },
        WorkflowState.TEACHER_VERIFIED: {
            WorkflowState.EVALUATED: {'evaluation', 'admin'},
            WorkflowState.LOCKED: {'admin'},
            WorkflowState.CORRECTION_REQUESTED: {'faculty', 'evaluation', 'admin'},
            WorkflowState.REJECTED: {'faculty', 'evaluation', 'admin'},
        },
        WorkflowState.EVALUATED: {
            WorkflowState.LOCKED: {'admin'},
            WorkflowState.CORRECTION_REQUESTED: {'evaluation', 'admin'},
            WorkflowState.REJECTED: {'evaluation', 'admin'},
        },
        WorkflowState.CORRECTION_REQUESTED: {
            WorkflowState.SUBMITTED: {'student_owner', 'admin'},
            WorkflowState.PENDING_REP_VERIFICATION: {'student_owner', 'admin'},
            WorkflowState.DRAFT: {'student_owner', 'admin'},
            WorkflowState.REJECTED: {'faculty', 'evaluation', 'admin'},
        },
        WorkflowState.REJECTED: {
            WorkflowState.DRAFT: {'student_owner', 'admin'},
            WorkflowState.SUBMITTED: {'student_owner', 'admin'},
            WorkflowState.PENDING_REP_VERIFICATION: {'student_owner', 'admin'},
        },
        WorkflowState.LOCKED: {
            WorkflowState.EVALUATED: {'admin'},
            WorkflowState.TEACHER_VERIFIED: {'admin'},
        }
    }

    allowed_targets = ALLOWED_MATRIX.get(current_norm, {})
    if target_norm not in allowed_targets:
        return False, f"Invalid workflow state transition from '{current_status}' to '{target_status}'.", stage_num, stage_name

    permitted_roles = allowed_targets[target_norm]

    # 4. Role & Object Permission Verification
    is_student_owner = (user_role == 'student' and sub_owner and user.id == sub_owner.id)
    is_rep = is_user_student_rep_for_class(user, sub_class)
    is_advisor = is_user_class_advisor(user, sub_class)
    is_eval = (user_role == 'evaluation' and is_evaluator_assigned_to_item(user, submission.criteria_id))

    role_matched = False
    if 'admin' in permitted_roles and is_admin:
        role_matched = True
    elif 'student_owner' in permitted_roles and is_student_owner:
        role_matched = True
    elif 'student_rep' in permitted_roles and is_rep:
        role_matched = True
    elif 'faculty' in permitted_roles and is_advisor:
        role_matched = True
    elif 'evaluation' in permitted_roles and is_eval:
        role_matched = True

    if not role_matched:
        # Provide specific failure reason
        if user_role == 'student':
            if target_norm in (WorkflowState.EVALUATED, WorkflowState.TEACHER_VERIFIED, WorkflowState.LOCKED):
                return False, "Unauthorized: Students cannot alter verification or evaluation status.", stage_num, stage_name
            if not is_rep:
                return False, "Unauthorized: Only designated Class Representatives can verify classmate submissions.", stage_num, stage_name
            if not is_student_owner and not is_rep:
                return False, "Unauthorized: You do not have permission to transition this submission.", stage_num, stage_name
        elif user_role == 'faculty':
            if not is_advisor:
                return False, "Unauthorized: Faculty can only verify submissions for their advised class.", stage_num, stage_name
            if target_norm == WorkflowState.EVALUATED:
                return False, "Unauthorized: Faculty cannot assign evaluation marks or transition to Evaluated.", stage_num, stage_name
        elif user_role == 'evaluation':
            if not is_eval:
                return False, "Unauthorized: Evaluator is not assigned to evaluate this criteria category.", stage_num, stage_name
            if target_norm == WorkflowState.LOCKED:
                return False, "Unauthorized: Only administrators can lock submissions.", stage_num, stage_name

        return False, f"Unauthorized: Role '{user_role}' cannot transition submission from '{current_status}' to '{target_status}'.", stage_num, stage_name

    # 5. Required Data Verification
    # Rejection & Correction Requested MUST have a reason/remark
    if target_norm in (WorkflowState.REJECTED, WorkflowState.CORRECTION_REQUESTED):
        remarks = (
            data.get('remarks') or
            data.get('teacherRemarks') or
            data.get('repRemarks') or
            data.get('evaluatorRemarks') or
            data.get('comments') or
            ''
        ).strip()
        if not remarks:
            return False, f"A remark or explanation is strictly required when transitioning to '{target_status}'.", stage_num, stage_name

    # Evaluation transitions MUST specify marks
    if target_norm == WorkflowState.EVALUATED:
        marks = data.get('marks') if 'marks' in data else submission.marks
        if marks is None:
            return False, "Evaluation score (marks) must be provided when transitioning to 'Evaluated'.", stage_num, stage_name

    return True, "", stage_num, stage_name


def execute_workflow_transition(
    submission: Submission,
    target_status: str,
    user: User,
    remarks: Optional[str] = None,
    marks: Optional[float] = None,
    request: Optional[Any] = None,
    **extra_fields
) -> Submission:
    """
    Atomically execute the transition, update verifier identity tracking fields,
    and record an immutable, cryptographically chained WorkflowAuditTrail entry.
    """
    from .views import create_audit_entry  # unified audit creator

    norm = normalize_status(target_status)
    prev_status = submission.status
    old_marks = submission.marks
    actor_name = user.get_full_name() or user.username
    user_role = getattr(user, 'role', '')

    with transaction.atomic():
        sub = Submission.objects.select_for_update().get(pk=submission.pk)
        sub.status = target_status

        # Verifier attribution
        if norm == WorkflowState.STUDENT_REP_VERIFIED or (user_role == 'student' and norm in (WorkflowState.CORRECTION_REQUESTED, WorkflowState.REJECTED)):
            sub.rep_verified_by_name = actor_name
            if remarks:
                sub.rep_remarks = remarks
            sub.verified_by_name = actor_name

        elif norm in (WorkflowState.TEACHER_VERIFIED, WorkflowState.APPROVED, WorkflowState.VERIFIED) or (user_role == 'faculty' and norm in (WorkflowState.CORRECTION_REQUESTED, WorkflowState.REJECTED)):
            sub.teacher_verified_by_name = actor_name
            if remarks:
                sub.teacher_remarks = remarks
            sub.verified_by_name = actor_name

        elif norm == WorkflowState.EVALUATED or (user_role == 'evaluation' and norm in (WorkflowState.CORRECTION_REQUESTED, WorkflowState.REJECTED)):
            sub.evaluator_verified_by_name = actor_name
            sub.evaluator_verified = (norm == WorkflowState.EVALUATED)
            if marks is not None:
                sub.marks = int(round(float(marks)))
            if remarks:
                sub.evaluator_remarks = remarks
            sub.verified_by_name = actor_name

        elif user_role == 'admin' or getattr(user, 'is_superuser', False):
            sub.verified_by_name = actor_name
            if marks is not None:
                sub.marks = int(round(float(marks)))
            if remarks:
                sub.remarks = remarks

        if remarks and not sub.remarks:
            sub.remarks = remarks

        # Apply any explicit extra fields
        for k, v in extra_fields.items():
            if hasattr(sub, k):
                setattr(sub, k, v)

        sub.save()

        # Audit Logging: WorkflowAuditTrail
        stage_num, stage_name = determine_stage(norm, user)
        audit_comments = remarks or f"Workflow transition from '{prev_status}' to '{target_status}' by {actor_name} ({user_role})"
        create_audit_entry(
            submission=sub,
            actor=user,
            stage=stage_num,
            stage_name=stage_name,
            prev_status=prev_status,
            new_status=sub.status,
            comments=audit_comments,
            request=request
        )

        # Audit Logging: SystemAuditLog
        audit_action = 'SUBMISSION_UPDATE'
        if norm in (WorkflowState.STUDENT_REP_VERIFIED, WorkflowState.TEACHER_VERIFIED, WorkflowState.APPROVED, WorkflowState.VERIFIED):
            audit_action = 'SUBMISSION_VERIFY'
        elif norm == WorkflowState.REJECTED:
            audit_action = 'SUBMISSION_REJECT'
        elif norm == WorkflowState.EVALUATED:
            audit_action = 'SUBMISSION_EVALUATE'
        elif norm == WorkflowState.LOCKED:
            audit_action = 'SUBMISSION_LOCK'
        elif prev_status == 'Locked' and norm != 'Locked':
            audit_action = 'SUBMISSION_UNLOCK'
        elif marks is not None and old_marks is not None and marks != old_marks:
            audit_action = 'SCORE_CHANGE'

        from users.audit import record_system_audit_event
        record_system_audit_event(
            action=audit_action,
            object_type='Submission',
            object_id=sub.id,
            actor=user,
            object_repr=f"Submission #{sub.id} ({sub.criteria_id}) -> {sub.status}",
            old_value={'status': prev_status, 'marks': old_marks},
            new_value={'status': sub.status, 'marks': sub.marks},
            reason=remarks or audit_comments,
            request=request
        )

        # Invalidate in-process ranking cache if marks, evaluation, or locking state changed
        if sub.status in ('Evaluated', 'Locked') or prev_status in ('Evaluated', 'Locked') or marks != old_marks:
            try:
                from users.services.ranking_service import RankingService
                RankingService.invalidate_cache(sub.academic_year)
            except Exception:
                pass

        return sub


class WorkflowService:
    """
    Domain service encapsulation for workflow state machine validation and transitions.
    """
    validate_transition = staticmethod(validate_workflow_transition)
    execute_transition = staticmethod(execute_workflow_transition)
    normalize_status = staticmethod(normalize_status)
    determine_stage = staticmethod(determine_stage)
    is_user_student_rep = staticmethod(is_user_student_rep_for_class)
    is_user_class_advisor = staticmethod(is_user_class_advisor)
    is_evaluator_assigned = staticmethod(is_evaluator_assigned_to_item)
