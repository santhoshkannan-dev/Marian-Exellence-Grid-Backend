import os
import mimetypes
import logging
from django.conf import settings
from django.http import FileResponse
from django.db.models import Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from users.models import Submission, Class, CriteriaItem
from users.file_security import save_private_evidence_file, resolve_safe_private_path
from .base import create_audit_entry, record_system_audit_event, is_user_student_rep

logger = logging.getLogger(__name__)


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
