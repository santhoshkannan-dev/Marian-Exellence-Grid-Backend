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

    # Canonical 6-State Pipeline Tokens
    DQC_PENDING = 'DQC_PENDING'
    TEACHER_PENDING = 'TEACHER_PENDING'
    EVALUATOR_PENDING = 'EVALUATOR_PENDING'
    APPROVED = 'APPROVED'
    SENT_BACK = 'SENT_BACK'

    # Aliases
    Approved = 'Approved'       # legacy UI badge
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
        DQC_PENDING,
        TEACHER_PENDING,
        EVALUATOR_PENDING,
        APPROVED,
        SENT_BACK,
    ]


ALIAS_MAP = {
    'DQC_PENDING': WorkflowState.PENDING_REP_VERIFICATION,
    'TEACHER_PENDING': WorkflowState.STUDENT_REP_VERIFIED,
    'EVALUATOR_PENDING': WorkflowState.TEACHER_VERIFIED,
    'APPROVED': WorkflowState.EVALUATED,
    'Approved': WorkflowState.EVALUATED,
    'SENT_BACK': WorkflowState.CORRECTION_REQUESTED,
    'Correction Requested': WorkflowState.CORRECTION_REQUESTED,
    'Correction': WorkflowState.CORRECTION_REQUESTED,
    'REJECTED': WorkflowState.REJECTED,
    'Verified': WorkflowState.TEACHER_VERIFIED,
    'Pending': WorkflowState.PENDING_REP_VERIFICATION,
    'Pending Verification': WorkflowState.PENDING_REP_VERIFICATION,
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
            if m.assigned_class:
                if getattr(m, 'assigned_class_id', None) == class_obj.id:
                    return True
                assigned_name = getattr(m.assigned_class, 'name', str(m.assigned_class))
                if assigned_name and assigned_name.strip().lower() == class_obj.name.strip().lower():
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

    # Check TeacherClassAssignment table
    from users.models import UserGroupMember, TeacherClassAssignment
    if TeacherClassAssignment.objects.filter(teacher=user, class_obj=class_obj, is_active=True).exists():
        return True
    if user_email and TeacherClassAssignment.objects.filter(teacher__email__iexact=user_email, class_obj=class_obj, is_active=True).exists():
        return True

    if user_email:
        ct_member = UserGroupMember.objects.filter(
            group__group_id='grp-class-teachers',
            email__iexact=user_email
        ).first()
        if ct_member and ct_member.assigned_class:
            if getattr(ct_member, 'assigned_class_id', None) == class_obj.id:
                return True
            assigned_cls_name = getattr(ct_member.assigned_class, 'name', str(ct_member.assigned_class))
            if str(assigned_cls_name).strip().lower() == class_obj.name.strip().lower():
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
        getattr(user, 'is_staff', False) or
        UserGroupMember.objects.filter(group__group_id='grp-evaluation-committee', email__iexact=user_email).exists() or
        EvaluatorCategoryAssignment.objects.filter(Q(evaluator=user) | Q(member__email__iexact=user_email)).exists()
    )
    if not is_eval_role:
        return False

    item = CriteriaItem.objects.filter(pk=criteria_id).select_related('category').first()
    if not item or not item.category:
        return False

    # Direct Evaluator Category Assignment
    if EvaluatorCategoryAssignment.objects.filter(category=item.category, evaluator=user).exists():
        return True
    if user_email and EvaluatorCategoryAssignment.objects.filter(category=item.category, evaluator__email__iexact=user_email).exists():
        return True
    if EvaluatorCategoryAssignment.objects.filter(category=item.category, member__email__iexact=user_email).exists():
        return True

    cat_evaluators = item.category.evaluators
    if cat_evaluators:
        allowed_emails = [str(e).strip().lower() for e in cat_evaluators if e]
        if user_email in allowed_emails:
            return True

    return False


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
    data: Optional[Dict[str, Any]] = None,
    role_context: Optional[str] = None
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

    user_role = getattr(user, 'role', '')
    is_admin = user_role == 'admin' or getattr(user, 'is_superuser', False)
    sub_owner = submission.user
    sub_class = sub_owner.class_name if sub_owner else None

    # Resolve active role context (teacher vs evaluator vs student vs dqc)
    ctx = (role_context or data.get('role_context') or data.get('role') or '').strip().lower()
    action_type = (data.get('actionType') or data.get('action') or '').strip().upper()

    if not ctx:
        if action_type == 'VERIFY_AND_FORWARD' or target_status in ('Teacher Verified', 'EVALUATOR_PENDING'):
            ctx = 'teacher'
        elif action_type == 'APPROVE_AND_CREDIT' or target_status in ('Evaluated', 'APPROVED'):
            ctx = 'evaluator'
        elif user_role in ('faculty', 'staff') and current_norm in (WorkflowState.SUBMITTED, WorkflowState.STUDENT_REP_VERIFIED, WorkflowState.PENDING_REP_VERIFICATION):
            ctx = 'teacher'
        elif user_role in ('evaluation', 'evaluator') and current_norm == WorkflowState.TEACHER_VERIFIED:
            ctx = 'evaluator'

    # If acting in Class Teacher context:
    if ctx == 'teacher' and not is_admin:
        # Approval in Teacher context is strictly Round 2 verification — forward to EVALUATOR_PENDING only
        if target_status in ('Approved', 'APPROVED', 'Teacher Verified', 'EVALUATOR_PENDING') or action_type == 'VERIFY_AND_FORWARD':
            target_norm = WorkflowState.TEACHER_VERIFIED

        # Strict check: teacher can ONLY verify their own assigned class
        is_advisor = is_user_class_advisor(user, sub_class)
        if not is_advisor:
            stage_num, stage_name = determine_stage(target_norm, user)
            return False, "Unauthorized: Class Teacher can only verify submissions for their assigned class.", stage_num, stage_name

        # Strict guard: teacher CANNOT execute APPROVE_AND_CREDIT or award marks under any circumstance
        if action_type == 'APPROVE_AND_CREDIT':
            stage_num, stage_name = determine_stage(target_norm, user)
            return False, "Unauthorized: Class Teacher verification cannot award marks or finalize evaluation. Only Evaluators (Round 3) may credit marks.", stage_num, stage_name

        # Strict guard: teacher CANNOT directly jump to EVALUATED or APPROVED
        if target_norm in (WorkflowState.EVALUATED, WorkflowState.APPROVED):
            stage_num, stage_name = determine_stage(target_norm, user)
            return False, "Unauthorized: Class Teacher cannot finalize evaluation. Forward the submission to Evaluator Pending (Round 3) instead.", stage_num, stage_name

    # If acting in Evaluator context:
    if ctx == 'evaluator' and not is_admin:
        # Evaluator can only evaluate assigned categories across all classes
        is_eval = is_evaluator_assigned_to_item(user, submission.criteria_id)
        if not is_eval:
            stage_num, stage_name = determine_stage(target_norm, user)
            return False, "Unauthorized: Evaluator is not assigned to evaluate this criteria category.", stage_num, stage_name

        # Evaluator evaluates Round 3: submission must be in Round 3 ready state (TEACHER_VERIFIED / EVALUATOR_PENDING)
        if target_norm == WorkflowState.EVALUATED and current_norm not in (WorkflowState.TEACHER_VERIFIED, WorkflowState.EVALUATED):
            stage_num, stage_name = determine_stage(target_norm, user)
            return False, "Submission must complete Round 2 Teacher Verification before final evaluation.", stage_num, stage_name

    stage_num, stage_name = determine_stage(target_norm, user)

    # 1. Check Authentication
    if not user or not user.is_authenticated:
        return False, "Authentication required to perform workflow transitions.", stage_num, stage_name

    # 2. Terminal Lock Protection
    if current_norm == WorkflowState.LOCKED:
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

    # 3. Permitted Transition Table — STRICT FORWARD-ONLY PIPELINE
    # Round 1 (DQC/Rep) → STUDENT_REP_VERIFIED
    # Round 2 (Teacher)  → TEACHER_VERIFIED
    # Round 3 (Evaluator)→ EVALUATED / APPROVED
    # Marks are ONLY credited at Round 3. Teachers and DQC cannot award marks.
    ALLOWED_MATRIX = {
        WorkflowState.DRAFT: {
            WorkflowState.SUBMITTED: {'student_owner', 'admin'},
            WorkflowState.PENDING_REP_VERIFICATION: {'student_owner', 'admin'},
        },
        WorkflowState.SUBMITTED: {
            # Round 1: Only DQC/Rep can advance to STUDENT_REP_VERIFIED. Faculty cannot bypass Round 1.
            WorkflowState.DRAFT: {'student_owner', 'admin'},
            WorkflowState.STUDENT_REP_VERIFIED: {'student_rep', 'admin'},
            # Faculty can only send back or reject at this stage (e.g. flagging fraudulent submissions)
            WorkflowState.CORRECTION_REQUESTED: {'student_rep', 'faculty', 'admin'},
            WorkflowState.REJECTED: {'student_rep', 'faculty', 'admin'},
        },
        WorkflowState.PENDING_REP_VERIFICATION: {
            # Same constraints as SUBMITTED — canonical DQC intake state
            WorkflowState.DRAFT: {'student_owner', 'admin'},
            WorkflowState.STUDENT_REP_VERIFIED: {'student_rep', 'admin'},
            WorkflowState.CORRECTION_REQUESTED: {'student_rep', 'faculty', 'admin'},
            WorkflowState.REJECTED: {'student_rep', 'faculty', 'admin'},
        },
        WorkflowState.STUDENT_REP_VERIFIED: {
            # Round 2: Only Class Teacher can advance. Evaluators cannot bypass Round 2.
            WorkflowState.TEACHER_VERIFIED: {'faculty', 'admin'},
            # Evaluators and teachers may send back or reject after DQC passes
            WorkflowState.CORRECTION_REQUESTED: {'student_rep', 'faculty', 'evaluation', 'admin'},
            WorkflowState.REJECTED: {'student_rep', 'faculty', 'evaluation', 'admin'},
        },
        WorkflowState.TEACHER_VERIFIED: {
            # Round 3: Only Evaluators can advance to final EVALUATED state
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
    is_eval = is_evaluator_assigned_to_item(user, submission.criteria_id)

    role_matched = False
    if 'admin' in permitted_roles and is_admin:
        role_matched = True
    elif 'student_owner' in permitted_roles and is_student_owner:
        role_matched = True
    elif 'student_rep' in permitted_roles and is_rep:
        role_matched = True
    elif 'faculty' in permitted_roles and (ctx == 'teacher' or not ctx) and is_advisor:
        role_matched = True
    elif 'evaluation' in permitted_roles and (ctx == 'evaluator' or not ctx) and is_eval:
        role_matched = True

    if not role_matched:
        if user_role == 'student':
            if target_norm in (WorkflowState.EVALUATED, WorkflowState.TEACHER_VERIFIED, WorkflowState.LOCKED):
                return False, "Unauthorized: Students cannot alter verification or evaluation status.", stage_num, stage_name
            if not is_rep:
                return False, "Unauthorized: Only designated Class Representatives can verify classmate submissions.", stage_num, stage_name
            if not is_student_owner and not is_rep:
                return False, "Unauthorized: You do not have permission to transition this submission.", stage_num, stage_name
        elif ctx == 'teacher' or user_role in ('faculty', 'teacher'):
            if target_norm == WorkflowState.EVALUATED:
                return False, "Unauthorized: Class Teacher verification cannot award final marks or finalize evaluation. Submissions must be forwarded to Evaluator Pending (Round 3).", stage_num, stage_name
            elif not is_advisor:
                return False, "Unauthorized: Faculty can only verify submissions for their advised class.", stage_num, stage_name
        elif ctx == 'evaluator' or user_role in ('evaluation', 'evaluator'):
            if not is_eval:
                return False, "Unauthorized: Evaluator is not assigned to evaluate this criteria category.", stage_num, stage_name
            if target_norm == WorkflowState.LOCKED:
                return False, "Unauthorized: Only administrators can lock submissions.", stage_num, stage_name

        return False, f"Unauthorized: Role '{user_role}' cannot transition submission from '{current_status}' to '{target_status}'.", stage_num, stage_name

    # 5. Required Data Verification
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

    if target_norm == WorkflowState.EVALUATED:
        marks = data.get('marks') if 'marks' in data else submission.marks
        if marks is None:
            from users.models import SubCategory
            has_subcat = False
            if submission.subcategory_id and SubCategory.objects.filter(id=submission.subcategory_id).exists():
                has_subcat = True
            elif SubCategory.find_subcategory(criteria_item=getattr(submission, 'criteria_item', None), evidence=submission.evidence):
                has_subcat = True
            if not has_subcat:
                return False, "Evaluation score (marks) must be provided when transitioning to 'Evaluated'.", stage_num, stage_name

    return True, "", stage_num, stage_name


def execute_workflow_transition(
    submission: Submission,
    target_status: str,
    user: User,
    remarks: Optional[str] = None,
    marks: Optional[float] = None,
    request: Optional[Any] = None,
    role_context: Optional[str] = None,
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
    is_admin = user_role == 'admin' or getattr(user, 'is_superuser', False)

    # Resolve context
    ctx = (role_context or extra_fields.get('role_context') or extra_fields.get('role') or '').strip().lower()
    action_type = (extra_fields.get('actionType') or extra_fields.get('action') or '').strip().upper()
    if not ctx:
        if action_type == 'VERIFY_AND_FORWARD' or target_status in ('Teacher Verified', 'EVALUATOR_PENDING'):
            ctx = 'teacher'
        elif action_type == 'APPROVE_AND_CREDIT' or target_status in ('Evaluated', 'APPROVED'):
            ctx = 'evaluator'
        elif user_role in ('faculty', 'staff') and norm == WorkflowState.TEACHER_VERIFIED:
            ctx = 'teacher'
        elif user_role in ('evaluation', 'evaluator') and norm == WorkflowState.EVALUATED:
            ctx = 'evaluator'

    if ctx == 'teacher' and not is_admin:
        if target_status in ('Approved', 'APPROVED', 'Teacher Verified', 'EVALUATOR_PENDING') or action_type == 'VERIFY_AND_FORWARD':
            norm = WorkflowState.TEACHER_VERIFIED
            target_status = 'EVALUATOR_PENDING' if target_status == 'EVALUATOR_PENDING' else 'Teacher Verified'

    with transaction.atomic():
        sub = Submission.objects.select_for_update().get(pk=submission.pk)
        sub.status = target_status

        # Verifier attribution
        if norm == WorkflowState.STUDENT_REP_VERIFIED or (user_role == 'student' and norm in (WorkflowState.CORRECTION_REQUESTED, WorkflowState.REJECTED)):
            sub.rep_verified_by_name = actor_name
            if remarks:
                sub.rep_remarks = remarks
            sub.verified_by_name = actor_name

        elif norm == WorkflowState.TEACHER_VERIFIED or (ctx == 'teacher' and norm in (WorkflowState.CORRECTION_REQUESTED, WorkflowState.REJECTED)) or (user_role in ('faculty', 'staff') and norm in (WorkflowState.CORRECTION_REQUESTED, WorkflowState.REJECTED)):
            sub.teacher_verified_by_name = actor_name
            if remarks:
                sub.teacher_remarks = remarks
            sub.verified_by_name = actor_name
            # Teacher verification strictly DOES NOT award marks to student or class
            marks = None

        elif norm == WorkflowState.EVALUATED or (ctx == 'evaluator' and norm in (WorkflowState.CORRECTION_REQUESTED, WorkflowState.REJECTED)) or (user_role in ('evaluation', 'evaluator') and norm in (WorkflowState.CORRECTION_REQUESTED, WorkflowState.REJECTED)):
            sub.evaluator_verified_by_name = actor_name
            sub.evaluator_verified = (norm == WorkflowState.EVALUATED)

            # Subcategory-based mark calculation:
            # For all categories containing subcategories, the calculated marks are strictly
            # driven by subcategories.default_marks.
            from users.models import SubCategory, CriteriaItem
            crit_item = CriteriaItem.objects.filter(pk=sub.criteria_id).select_related('category').first()
            subcat = None
            if sub.subcategory_id:
                subcat = SubCategory.objects.filter(id=sub.subcategory_id).first()
            if not subcat and crit_item and crit_item.category:
                cat_code_check = (crit_item.category.code or '').strip().lower()
                if cat_code_check not in ('cat-academics', 'cat-career-advancement', 'cat-documentation'):
                    subcat = SubCategory.find_subcategory(
                        subcategory_id=sub.subcategory_id,
                        category_id=getattr(crit_item.category, 'id', None),
                        category_code=cat_code_check,
                        criteria_item=crit_item,
                        evidence=sub.evidence
                    )
                    if subcat:
                        sub.subcategory_id = subcat.id

            if subcat:
                count_val = 1
                ev = sub.evidence if isinstance(sub.evidence, dict) else {}
                if (crit_item and crit_item.type == 'count') or 'count' in ev:
                    try:
                        count_val = max(1, int(ev.get('count', 1)))
                    except (ValueError, TypeError):
                        count_val = 1
                final_m = float(subcat.default_marks) * count_val
                sub.calculated_marks = final_m
                sub.marks = int(round(final_m))
            elif marks is not None:
                sub.marks = int(round(float(marks)))
                sub.calculated_marks = float(marks)

            if remarks:
                sub.evaluator_remarks = remarks
            sub.verified_by_name = actor_name

        elif is_admin:
            sub.verified_by_name = actor_name
            from users.models import SubCategory, CriteriaItem
            crit_item = CriteriaItem.objects.filter(pk=sub.criteria_id).select_related('category').first()
            subcat = None
            if sub.subcategory_id:
                subcat = SubCategory.objects.filter(id=sub.subcategory_id).first()
            if not subcat and crit_item and crit_item.category:
                cat_code_check = (crit_item.category.code or '').strip().lower()
                if cat_code_check not in ('cat-academics', 'cat-career-advancement', 'cat-documentation'):
                    subcat = SubCategory.find_subcategory(
                        subcategory_id=sub.subcategory_id,
                        category_id=getattr(crit_item.category, 'id', None),
                        category_code=cat_code_check,
                        criteria_item=crit_item,
                        evidence=sub.evidence
                    )
                    if subcat:
                        sub.subcategory_id = subcat.id

            if subcat:
                count_val = 1
                ev = sub.evidence if isinstance(sub.evidence, dict) else {}
                if (crit_item and crit_item.type == 'count') or 'count' in ev:
                    try:
                        count_val = max(1, int(ev.get('count', 1)))
                    except (ValueError, TypeError):
                        count_val = 1
                final_m = float(subcat.default_marks) * count_val
                sub.calculated_marks = final_m
                sub.marks = int(round(final_m))
            elif marks is not None:
                sub.marks = int(round(float(marks)))
                sub.calculated_marks = float(marks)
            if remarks:
                sub.remarks = remarks

        if remarks and not sub.remarks:
            sub.remarks = remarks

        # Apply any explicit extra fields (excluding internal control keys)
        for k, v in extra_fields.items():
            if k not in ('status', 'marks', 'actionType', 'action', 'role_context', 'role') and hasattr(sub, k):
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

        # Audit Logging: VerificationLog
        from users.models import VerificationLog
        if ctx == 'evaluator' or norm == WorkflowState.EVALUATED:
            v_level = 'EVALUATOR'
        elif ctx == 'teacher' or norm == WorkflowState.TEACHER_VERIFIED:
            v_level = 'CLASS_TEACHER'
        elif user_role == 'student' or norm in (WorkflowState.STUDENT_REP_VERIFIED, 'DQC_PENDING'):
            v_level = 'DQC'
        elif user_role in ('evaluation', 'evaluator'):
            v_level = 'EVALUATOR'
        elif user_role in ('faculty', 'staff', 'teacher'):
            v_level = 'CLASS_TEACHER'
        else:
            v_level = 'DQC'

        # Contract action types: 'VERIFY_AND_FORWARD', 'SEND_BACK', 'REJECT', or 'APPROVE_AND_CREDIT'
        if action_type in ('VERIFY_AND_FORWARD', 'SEND_BACK', 'REJECT', 'APPROVE_AND_CREDIT'):
            v_action = action_type
        elif norm in (WorkflowState.EVALUATED, 'Approved', 'APPROVED') or (v_level == 'EVALUATOR' and norm not in (WorkflowState.CORRECTION_REQUESTED, WorkflowState.REJECTED)):
            v_action = 'APPROVE_AND_CREDIT'
        elif norm in (WorkflowState.CORRECTION_REQUESTED, 'SENT_BACK'):
            v_action = 'SEND_BACK'
        elif norm in (WorkflowState.REJECTED, 'REJECTED'):
            v_action = 'REJECT'
        else:
            v_action = 'VERIFY_AND_FORWARD'

        try:
            VerificationLog.log_action(
                submission_id=sub.id,
                verification_level=v_level,
                verifier_id=user.id if user else None,
                verifier_name=actor_name,
                action=v_action,
                remarks=remarks or None
            )
        except Exception as e:
            logger.warning(f"Could not write VerificationLog for sub #{sub.id}: {e}")

        # Invalidate in-process ranking cache and credit marks to leaderboard ledger upon Evaluator approval
        if norm in (WorkflowState.EVALUATED, 'Approved', 'APPROVED') and v_level == 'EVALUATOR':
            try:
                from users.services.ranking_service import RankingService
                RankingService.invalidate_cache(sub.academic_year)
            except Exception:
                pass

            # Credit Marks: Upon Evaluator approval, write calculated_marks to the class leaderboard ledger
            if sub.user and sub.user.class_name:
                try:
                    from users.scoring_engine import compute_class_scores
                    from users.models import ClassIndexResult, AcademicYear
                    ay_str = sub.academic_year or '2025-2026'
                    ay_obj = AcademicYear.objects.filter(year=ay_str).first()
                    if ay_obj:
                        res = compute_class_scores(sub.user.class_name, academic_year=ay_str)
                        ClassIndexResult.objects.update_or_create(
                            class_name=sub.user.class_name,
                            academic_year=ay_obj,
                            defaults={
                                'academic_score': res.get('academic_score', 0.0),
                                'co_curricular_score': res.get('co_curricular_score', 0.0),
                                'extra_curricular_score': res.get('extra_curricular_score', 0.0),
                                'final_index': res.get('M') or 0.0,
                                'snapshot_data': res
                            }
                        )
                except Exception as ex:
                    logger.warning(f"Could not auto-credit class leaderboard ledger: {ex}")

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
