"""
Forward-Only Multi-Tier Verification Controllers
=================================================
Enforces the strict 3-round verification pipeline:

  Round 1 — DQC / Student Rep Group  → TEACHER_PENDING   (no marks)
  Round 2 — Class Teacher             → EVALUATOR_PENDING (no marks)
  Round 3 — Evaluator Committee       → APPROVED          (marks credited)

Dual-role staff must execute Teacher (Round 2) and Evaluator (Round 3) steps separately.
Legacy 'Submitted' records are expected to be migrated to 'DQC_PENDING' by the
migrate_submitted_to_dqc_pending management command before this pipeline is active.
"""

import logging
from django.db import transaction
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from users.permissions import AuthorizeRound1Verifier

from users.models import Submission, CriteriaItem
from users.services.submission_service import SubmissionService
from users.workflow import (
    WorkflowState,
    normalize_status,
    is_user_student_rep_for_class,
    is_user_class_advisor,
    is_evaluator_assigned_to_item,
    execute_workflow_transition,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# States that are eligible for DQC (Round 1) intake
DQC_INTAKE_STATES = {
    WorkflowState.DQC_PENDING,
    WorkflowState.PENDING_REP_VERIFICATION,
}

# States that are eligible for Teacher (Round 2) intake
TEACHER_INTAKE_STATES = {
    WorkflowState.TEACHER_PENDING,
    WorkflowState.STUDENT_REP_VERIFIED,
}

# States that are eligible for Evaluator (Round 3) intake
EVALUATOR_INTAKE_STATES = {
    WorkflowState.EVALUATOR_PENDING,
    WorkflowState.TEACHER_VERIFIED,
}

# Valid action tokens per round
DQC_VALID_ACTIONS = {'VERIFY_AND_FORWARD', 'SEND_BACK', 'REJECT'}
TEACHER_VALID_ACTIONS = {'VERIFY_AND_FORWARD', 'SEND_BACK', 'REJECT'}
EVALUATOR_VALID_ACTIONS = {'APPROVE_AND_CREDIT', 'SEND_BACK', 'REJECT'}


def _get_submission_or_error(submission_id):
    """Fetch a submission by ID with related objects, or return None."""
    try:
        return Submission.objects.select_related(
            'user', 'user__class_name', 'user__department', 'category'
        ).get(pk=int(submission_id))
    except (Submission.DoesNotExist, ValueError, TypeError):
        return None


def _require_remarks(action, data):
    """Return an error string if remarks are missing for SEND_BACK/REJECT actions."""
    if action in ('SEND_BACK', 'REJECT'):
        remarks = (
            data.get('remarks') or
            data.get('teacherRemarks') or
            data.get('repRemarks') or
            data.get('evaluatorRemarks') or
            ''
        ).strip()
        if not remarks:
            return f"A remark is required when performing '{action}'."
    return None


# ---------------------------------------------------------------------------
# Round 1: DQC / Student Representative Group Verification
# ---------------------------------------------------------------------------

class DQCVerificationView(APIView):
    """
    POST /api/verification/dqc

    Authorized roles: Student Representative (grp-student-reps) OR
                      DQC Member (grp-dqc-student-rep)
    Both groups share identical Round 1 powers — authorization is resolved via
    is_user_student_rep_for_class() which checks both groups transparently.

    Actions:
      VERIFY_AND_FORWARD  → status: TEACHER_PENDING  (no marks written)
      SEND_BACK           → status: SENT_BACK        (remarks required)
      REJECT              → status: REJECTED         (remarks required)

    CRITICAL: Marks are NEVER written at Round 1 regardless of payload.
    """
    permission_classes = [IsAuthenticated, AuthorizeRound1Verifier]

    def post(self, request):
        user = request.user
        user_role = getattr(user, 'role', '')

        # Authorization check for Round 1 Verification:
        # User must belong to 'Student Representatives' or 'DQC Student Rep Group'
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
            UserService.is_user_dqc_rep(user) or
            user_role == 'admin' or
            getattr(user, 'is_superuser', False)
        )

        if not is_authorized or (user_role != 'student' and user_role != 'admin' and not getattr(user, 'is_superuser', False)):
            return Response(
                {
                    "error": "Access Denied",
                    "message": "You do not have Round 1 verification authority."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        submission_id = request.data.get('submissionId') or request.data.get('submission_id')
        if not submission_id:
            return Response({"error": "submissionId is required."}, status=status.HTTP_400_BAD_REQUEST)

        action = (request.data.get('action') or request.data.get('actionType') or '').strip().upper()
        if action not in DQC_VALID_ACTIONS:
            return Response(
                {"error": f"Invalid action '{action}'. Allowed: {sorted(DQC_VALID_ACTIONS)}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        remarks = (
            request.data.get('remarks') or
            request.data.get('repRemarks') or
            ''
        ).strip()

        # Remarks required for SEND_BACK / REJECT
        remarks_err = _require_remarks(action, request.data)
        if remarks_err:
            return Response({"error": remarks_err}, status=status.HTTP_400_BAD_REQUEST)

        submission = _get_submission_or_error(submission_id)
        if not submission:
            return Response({"error": "Submission not found."}, status=status.HTTP_404_NOT_FOUND)

        sub_class = submission.class_obj or (submission.user.class_name if submission.user else None)

        # Role guard: must be a DQC member or student rep for THIS class
        if not is_user_student_rep_for_class(user, sub_class):
            return Response(
                {"error": "Unauthorized: You are not a designated DQC Member or Student Representative for this class."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Queue state guard: submission must be in the DQC intake queue
        current_norm = normalize_status(submission.status)
        if current_norm not in DQC_INTAKE_STATES:
            return Response(
                {
                    "error": f"This submission is not in the DQC verification queue. Current status: '{submission.status}'.",
                    "currentStatus": submission.status,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Map action to target status
        if action == 'VERIFY_AND_FORWARD':
            target_status = 'TEACHER_PENDING'
        elif action == 'SEND_BACK':
            target_status = 'SENT_BACK'
        else:  # REJECT
            target_status = 'REJECTED'

        # CRITICAL: Strip marks completely — DQC / Student Rep never awards marks
        try:
            with transaction.atomic():
                updated_submission = execute_workflow_transition(
                    submission=submission,
                    target_status=target_status,
                    user=user,
                    remarks=remarks or None,
                    marks=None,          # explicitly None — never credit marks at Round 1
                    request=request,
                    role_context='dqc',
                    actionType=action,
                )
        except Exception as e:
            logger.exception(f"DQC verification failed for submission #{submission_id}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "success": True,
                "message": f"Round 1 (DQC / Student Representative) action '{action}' applied successfully.",
                "submissionId": updated_submission.id,
                "previousStatus": submission.status,
                "newStatus": updated_submission.status,
                "action": action,
                "verifierName": user.get_full_name() or user.username,
            },
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------------------------------
# Round 2: Class Teacher Verification
# ---------------------------------------------------------------------------

class TeacherVerificationView(APIView):
    """
    POST /api/verification/teacher

    Authorized roles: Faculty assigned as Class Teacher for the submission's class.
    Dual-role staff must use this endpoint separately from the Evaluator endpoint.

    Actions:
      VERIFY_AND_FORWARD  → status: EVALUATOR_PENDING  (no marks written)
      SEND_BACK           → status: SENT_BACK          (remarks required)
      REJECT              → status: REJECTED           (remarks required)

    CRITICAL: APPROVE_AND_CREDIT is explicitly blocked. Marks are NEVER written
    at Round 2. Teachers cannot award marks under any circumstance.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        user_role = getattr(user, 'role', '')

        if user_role not in ('faculty', 'teacher', 'staff') and not getattr(user, 'is_staff', False):
            return Response(
                {"error": "Unauthorized: Only Class Teachers (faculty) can perform Round 2 verification."},
                status=status.HTTP_403_FORBIDDEN
            )

        submission_id = request.data.get('submissionId') or request.data.get('submission_id')
        if not submission_id:
            return Response({"error": "submissionId is required."}, status=status.HTTP_400_BAD_REQUEST)

        action = (request.data.get('action') or request.data.get('actionType') or '').strip().upper()

        # Explicit guard: APPROVE_AND_CREDIT is a Round 3 exclusive action
        if action == 'APPROVE_AND_CREDIT':
            return Response(
                {"error": "Unauthorized: APPROVE_AND_CREDIT is a Round 3 (Evaluator) exclusive action. Class Teachers cannot award marks."},
                status=status.HTTP_403_FORBIDDEN
            )

        if action not in TEACHER_VALID_ACTIONS:
            return Response(
                {"error": f"Invalid action '{action}'. Allowed: {sorted(TEACHER_VALID_ACTIONS)}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        remarks = (
            request.data.get('remarks') or
            request.data.get('teacherRemarks') or
            ''
        ).strip()

        remarks_err = _require_remarks(action, request.data)
        if remarks_err:
            return Response({"error": remarks_err}, status=status.HTTP_400_BAD_REQUEST)

        submission = _get_submission_or_error(submission_id)
        if not submission:
            return Response({"error": "Submission not found."}, status=status.HTTP_404_NOT_FOUND)

        sub_class = submission.user.class_name if submission.user else None

        # Role guard: must be the class teacher for THIS class
        if not is_user_class_advisor(user, sub_class):
            return Response(
                {"error": "Unauthorized: You are not the assigned Class Teacher for this submission's class."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Queue state guard: submission must be in the Teacher intake queue
        current_norm = normalize_status(submission.status)
        if current_norm not in TEACHER_INTAKE_STATES:
            return Response(
                {
                    "error": f"This submission is not in the Teacher verification queue. Current status: '{submission.status}'.",
                    "currentStatus": submission.status,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Map action to target status
        if action == 'VERIFY_AND_FORWARD':
            target_status = WorkflowState.TEACHER_VERIFIED   # normalizes → EVALUATOR_PENDING
        elif action == 'SEND_BACK':
            target_status = WorkflowState.CORRECTION_REQUESTED
        else:  # REJECT
            target_status = WorkflowState.REJECTED

        # CRITICAL: Strip marks — teachers never award marks
        try:
            with transaction.atomic():
                updated_submission = execute_workflow_transition(
                    submission=submission,
                    target_status=target_status,
                    user=user,
                    remarks=remarks or None,
                    marks=None,          # explicitly None — never credit marks at Round 2
                    request=request,
                    role_context='teacher',
                    actionType=action,
                )
        except Exception as e:
            logger.exception(f"Teacher verification failed for submission #{submission_id}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "success": True,
                "message": f"Round 2 (Class Teacher) action '{action}' applied successfully.",
                "submissionId": updated_submission.id,
                "previousStatus": submission.status,
                "newStatus": updated_submission.status,
                "action": action,
                "verifierName": user.get_full_name() or user.username,
            },
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------------------------------
# Round 3: Evaluator Final Evaluation
# ---------------------------------------------------------------------------

class EvaluatorVerificationView(APIView):
    """
    POST /api/verification/evaluator

    Authorized roles: Evaluation Committee members assigned to the submission's category.
    Dual-role staff must use this endpoint separately from the Teacher endpoint.

    Actions:
      APPROVE_AND_CREDIT  → status: APPROVED  (marks written & class ledger recalculated)
      SEND_BACK           → status: SENT_BACK (remarks required)
      REJECT              → status: REJECTED  (remarks required)

    This is the ONLY endpoint that awards marks. 'marks' is required for APPROVE_AND_CREDIT.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        user_role = getattr(user, 'role', '')

        if user_role not in ('evaluation', 'evaluator', 'faculty', 'admin') and not getattr(user, 'is_superuser', False):
            return Response(
                {"error": "Unauthorized: Only Evaluation Committee members can perform Round 3 evaluation."},
                status=status.HTTP_403_FORBIDDEN
            )

        submission_id = request.data.get('submissionId') or request.data.get('submission_id')
        if not submission_id:
            return Response({"error": "submissionId is required."}, status=status.HTTP_400_BAD_REQUEST)

        action = (request.data.get('action') or request.data.get('actionType') or '').strip().upper()
        if action not in EVALUATOR_VALID_ACTIONS:
            return Response(
                {"error": f"Invalid action '{action}'. Allowed: {sorted(EVALUATOR_VALID_ACTIONS)}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        remarks = (
            request.data.get('remarks') or
            request.data.get('evaluatorRemarks') or
            ''
        ).strip()

        remarks_err = _require_remarks(action, request.data)
        if remarks_err:
            return Response({"error": remarks_err}, status=status.HTTP_400_BAD_REQUEST)

        submission = _get_submission_or_error(submission_id)
        if not submission:
            return Response({"error": "Submission not found."}, status=status.HTTP_404_NOT_FOUND)

        # Role guard: must be assigned evaluator for this submission's category
        is_admin = user_role == 'admin' or getattr(user, 'is_superuser', False)
        if not is_admin and not is_evaluator_assigned_to_item(user, submission.criteria_id):
            return Response(
                {"error": "Unauthorized: You are not assigned to evaluate this criteria category."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Queue state guard: submission must be in the Evaluator intake queue
        current_norm = normalize_status(submission.status)
        if current_norm not in EVALUATOR_INTAKE_STATES:
            return Response(
                {
                    "error": f"This submission is not in the Evaluator verification queue. Current status: '{submission.status}'.",
                    "currentStatus": submission.status,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Subcategory-based dynamic mark resolution:
        # For all categories containing subcategories, the calculated marks are strictly driven
        # by the specific subcategory's defined base mark (subcategories.default_marks).
        from users.models import SubCategory
        subcat = None
        criteria_item = CriteriaItem.objects.filter(pk=submission.criteria_id).select_related('category').first()
        cat_code_check = (criteria_item.category.code or '').strip().lower() if (criteria_item and criteria_item.category) else ''

        # Only categories containing subcategories are resolved via SubCategory table
        if cat_code_check not in ('cat-academics', 'cat-career-advancement', 'cat-documentation'):
            if submission.subcategory_id:
                subcat = SubCategory.objects.filter(id=submission.subcategory_id).first()

            if not subcat and criteria_item and criteria_item.category:
                subcat = SubCategory.find_subcategory(
                    subcategory_id=submission.subcategory_id,
                    category_id=getattr(criteria_item.category, 'id', None),
                    category_code=cat_code_check,
                    criteria_item=criteria_item,
                    evidence=submission.evidence
                )
                if subcat:
                    submission.subcategory_id = subcat.id

        marks_val = None
        if subcat:
            count_val = 1
            ev = submission.evidence if isinstance(submission.evidence, dict) else {}
            if (criteria_item and criteria_item.type == 'count') or 'count' in ev:
                try:
                    count_val = max(1, int(ev.get('count', 1)))
                except (ValueError, TypeError):
                    count_val = 1
            expected_mark = float(subcat.default_marks) * count_val
            # Strictly drive marks by subcategory default mark
            if action == 'APPROVE_AND_CREDIT':
                marks_val = expected_mark
        else:
            # Categories without subcategories (Academics / Career Advancement manual eval)
            if action == 'APPROVE_AND_CREDIT':
                raw_marks = request.data.get('marks')
                if raw_marks is None:
                    return Response(
                        {"error": "marks is required for APPROVE_AND_CREDIT action."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                try:
                    marks_val = float(raw_marks)
                except (ValueError, TypeError):
                    return Response({"error": "marks must be a valid number."}, status=status.HTTP_400_BAD_REQUEST)

            if marks_val is not None and criteria_item:
                is_manual = (
                    getattr(criteria_item, 'is_manual_eval', False) or
                    (criteria_item.category and getattr(criteria_item.category, 'is_manual_eval', False))
                )
                if is_manual:
                    if marks_val < 0 or marks_val > 500:
                        return Response(
                            {"error": "Manual evaluation score must be between 0 and 500."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                else:
                    allowed_min, allowed_max, details = SubmissionService.get_criteria_allowed_bounds(
                        criteria_item, submission.evidence
                    )
                    is_negative = criteria_item.type in ('negative', 'academic_grades') or (allowed_min is not None and allowed_min < 0)
                    if marks_val < 0 and not is_negative:
                        return Response(
                            {"error": f"Score ({marks_val}) cannot be negative for non-penalty criteria."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    if allowed_max is not None and marks_val > (allowed_max + 1e-5):
                        return Response(
                            {"error": f"Score ({marks_val}) exceeds the maximum allowed limit ({allowed_max}) for '{criteria_item.title}'{details}."},
                            status=status.HTTP_400_BAD_REQUEST
                        )

        # Map action to target status
        if action == 'APPROVE_AND_CREDIT':
            target_status = WorkflowState.EVALUATED   # normalizes → APPROVED, credits marks
        elif action == 'SEND_BACK':
            target_status = WorkflowState.CORRECTION_REQUESTED
        else:  # REJECT
            target_status = WorkflowState.REJECTED

        try:
            with transaction.atomic():
                updated_submission = execute_workflow_transition(
                    submission=submission,
                    target_status=target_status,
                    user=user,
                    remarks=remarks or None,
                    marks=marks_val,     # Only Round 3 writes marks to the ledger
                    request=request,
                    role_context='evaluator',
                    actionType=action,
                )
        except Exception as e:
            logger.exception(f"Evaluator verification failed for submission #{submission_id}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        response_data = {
            "success": True,
            "message": f"Round 3 (Evaluator) action '{action}' applied successfully.",
            "submissionId": updated_submission.id,
            "previousStatus": submission.status,
            "newStatus": updated_submission.status,
            "action": action,
            "verifierName": user.get_full_name() or user.username,
        }
        if action == 'APPROVE_AND_CREDIT':
            response_data["marksAwarded"] = updated_submission.marks
            response_data["calculatedMarks"] = updated_submission.calculated_marks

        return Response(response_data, status=status.HTTP_200_OK)
