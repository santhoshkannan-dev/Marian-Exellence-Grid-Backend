"""
End-to-End System Scenario Integration Test Suite
=================================================
Validates the complete lifecycle of activity submissions across:
1. SSO Login & Dynamic Role Resolution
2. Category-Specific Submission Rules
3. Forward-Only 3-Tier Verification Pipeline
4. Subcategory & Category 1 Mark Calculations
5. Final Class Ledger Allocation

Scenario Matrix:
- Scenario A: DQC Student Rep, Cat 1 (Academics), Formula marks awarded to Class Ledger
- Scenario B: Standard Student, Cat 2 (Swayam Course), Subcategory lookup mark (5.00) awarded
- Scenario C: Standard Student, Cat 12 (Career Adv.), Manual Evaluator input marks awarded
- Scenario D: Dual-Role Staff, Cat 3 (NET Exam), Verification isolation verified (No marks in R2)
- Scenario E: Student Rep / DQC, Cat 2 (MOOC), Verification send-back loop validated
"""

import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from apps.submissions.models import Submission, Category, Subcategory, ClassLedger
from apps.verifications.models import VerificationLog
from users.models import (
    AcademicYear,
    CriteriaCategory,
    CriteriaItem,
    EvaluatorCategoryAssignment,
    TeacherClassAssignment,
    UserGroupModel,
    UserGroupMember,
    ClassIndexResult,
)
from users.services.user_service import (
    determine_role_from_email,
    get_user_roles_and_dual_status,
    get_user_badge,
)

User = get_user_model()


@pytest.mark.django_db
class TestE2EScenarioPipeline:

    @pytest.fixture
    def client(self):
        from rest_framework.test import APIClient
        return APIClient()

    @pytest.fixture(autouse=True)
    def setup_entities(self):
        """Setup test roles, categories, subcategories, and assignments."""
        # 0. Active Academic Year
        self.ay, _ = AcademicYear.objects.get_or_create(year="2025-2026", defaults={"is_active": True})

        # 1. Users & Roles
        self.student = User.objects.create_user(
            email="john.24bca101@mariancollege.org",
            role="STUDENT",
            class_id="24BCA1"
        )
        self.student_rep = User.objects.create_user(
            email="rep.24bca101@mariancollege.org",
            role="STUDENT_REP",
            class_id="24BCA1"
        )
        self.teacher = User.objects.create_user(
            email="teacher@mariancollege.org",
            role="FACULTY",
            is_class_teacher=True,
            assigned_class_id="24BCA1"
        )
        self.evaluator = User.objects.create_user(
            email="evaluator@mariancollege.org",
            role="FACULTY",
            is_evaluator=True,
            evaluator_category_id=2
        )

        # 2. Categories & Subcategories
        # Category 1: Academics
        self.cat1 = Category.objects.create(id=1, name="Academics")
        self.crit_cat1, _ = CriteriaCategory.objects.get_or_create(
            code="cat-academics",
            defaults={"category": "Academics", "access_level": "dqc_only", "evaluators": [self.evaluator.email]}
        )
        self.crit_item_academics, _ = CriteriaItem.objects.get_or_create(
            category=self.crit_cat1,
            title="Class Pass Percentage %",
            defaults={"type": "academic_grades", "marks": 50.0, "access_level": "dqc_only"}
        )
        EvaluatorCategoryAssignment.objects.get_or_create(
            category=self.crit_cat1,
            evaluator=self.evaluator,
            academic_year=2025
        )

        # Category 2: Online Courses
        self.cat2 = Category.objects.create(id=2, name="Online Courses")
        self.sub_swayam = Subcategory.objects.create(
            id=101,
            category=self.cat2,
            subcategory_name="Swayam / NPTEL Course",
            default_marks=Decimal("5.00")
        )
        self.sub_mooc = Subcategory.objects.create(
            id=102,
            category=self.cat2,
            subcategory_name="MOOC Course",
            default_marks=Decimal("2.00")
        )
        self.crit_cat2, _ = CriteriaCategory.objects.get_or_create(
            code="cat-online-courses",
            defaults={"category": "Online Courses", "access_level": "all_students", "evaluators": [self.evaluator.email]}
        )
        self.crit_item_swayam, _ = CriteriaItem.objects.get_or_create(
            category=self.crit_cat2,
            title="Swayam / NPTEL Course",
            defaults={"type": "fixed", "marks": 5.0, "access_level": "all_students"}
        )
        self.crit_item_mooc, _ = CriteriaItem.objects.get_or_create(
            category=self.crit_cat2,
            title="MOOC Course",
            defaults={"type": "fixed", "marks": 2.0, "access_level": "all_students"}
        )
        EvaluatorCategoryAssignment.objects.get_or_create(
            category=self.crit_cat2,
            evaluator=self.evaluator,
            academic_year=2025
        )

        # Category 3: Competitive Exams
        self.cat3 = Category.objects.create(id=3, name="Competitive Exams")
        self.sub_net = Subcategory.objects.create(
            id=103,
            category=self.cat3,
            subcategory_name="NET Passed",
            default_marks=Decimal("10.00")
        )
        self.crit_cat3, _ = CriteriaCategory.objects.get_or_create(
            code="cat-competitive-exams",
            defaults={"category": "Competitive Exams", "access_level": "all_students", "evaluators": [self.evaluator.email]}
        )
        self.crit_item_net, _ = CriteriaItem.objects.get_or_create(
            category=self.crit_cat3,
            title="NET Passed",
            defaults={"type": "fixed", "marks": 10.0, "access_level": "all_students"}
        )

        # Category 12: Career Advancement (Manual Eval)
        self.cat12 = Category.objects.create(id=12, name="Career Advancement")
        self.crit_cat12, _ = CriteriaCategory.objects.get_or_create(
            code="cat-career-advancement",
            defaults={"category": "Career Advancement", "is_manual_eval": True, "evaluators": [self.evaluator.email]}
        )
        self.crit_item_career, _ = CriteriaItem.objects.get_or_create(
            category=self.crit_cat12,
            title="LinkedIn - Profile Completion (Active Profile)",
            defaults={"type": "fixed", "marks": 15.0, "is_manual_eval": True, "access_level": "all_students"}
        )
        EvaluatorCategoryAssignment.objects.get_or_create(
            category=self.crit_cat12,
            evaluator=self.evaluator,
            academic_year=2025
        )

        # Dual-Role Staff User (Teacher for 24BCA1 AND Evaluator for Category 3)
        self.dual_staff = User.objects.create_user(
            email="dual.staff@mariancollege.org",
            role="FACULTY",
            is_class_teacher=True,
            assigned_class_id="24BCA1"
        )
        grp_ec, _ = UserGroupModel.objects.get_or_create(
            group_id="grp-evaluation-committee",
            defaults={"name": "Evaluation Committee"}
        )
        UserGroupMember.objects.get_or_create(group=grp_ec, email=self.dual_staff.email, defaults={"user": self.dual_staff})
        if self.dual_staff.email not in (self.crit_cat3.evaluators or []):
            self.crit_cat3.evaluators = list(self.crit_cat3.evaluators or []) + [self.dual_staff.email]
            self.crit_cat3.save(update_fields=["evaluators"])
        EvaluatorCategoryAssignment.objects.get_or_create(
            category=self.crit_cat3,
            evaluator=self.dual_staff,
            academic_year=2025
        )

        # 3. Initial Class Ledger
        self.ledger, _ = ClassLedger.objects.get_or_create(
            class_id="24BCA1",
            defaults={"total_marks": Decimal("0.00")}
        )
        self.ledger.total_marks = Decimal("0.00")
        self.ledger.save()

    # ======================================================================
    # Scenario A: DQC Student Rep — Cat 1 (Academics)
    # Expected: Formula marks awarded to Class Ledger
    # ======================================================================
    def test_scenario_a_academics_formula_lifecycle(self, client):
        """
        Scenario A: End-to-end Academics (Category 1) submission by DQC Student Rep.
        Formula: (Count >= 90% * 5) + (Count 80-90% * 4) + (Count 70-80% * 3) + Pass Rate Bonus - Count Fail
        - S count = 2 (* 5 = 10)
        - APlus count = 4 (* 4 = 16)
        - A count = 3 (* 3 = 9)
        - Fail count = 1 (- 1)
        - Total students = 10
        - Pass rate = (9 / 10) * 100 = 90.0% -> Pass bonus = 4.0
        - Total expected formula mark = 10 + 16 + 9 + 4 - 1 = 38.00
        """
        # STEP 1: DQC Student Rep submits academic grade breakdown
        client.force_login(self.student_rep)
        submit_payload = {
            "criteriaId": self.crit_item_academics.id,
            "academicYear": "2025-2026",
            "description": "Semester 1 Consolidated Results for 24BCA1",
            "proof_url": "https://drive.google.com/file/d/academics_result_sheet",
            "evidence": {
                "totalStudents": 10,
                "grades": {
                    "S": 2,
                    "APlus": 4,
                    "A": 3,
                    "Fail": 1
                }
            }
        }
        res_submit = client.post(reverse("submission-list"), submit_payload, format="json")
        assert res_submit.status_code == status.HTTP_201_CREATED

        submission_id = res_submit.data["id"]
        submission = Submission.objects.get(id=submission_id)
        assert submission.status == "DQC_PENDING"
        assert submission.calculated_marks == Decimal("0.00")

        # STEP 2: Round 1 Verification (Student Rep Check & Forward)
        client.force_login(self.student_rep)
        r1_payload = {
            "submission_id": submission_id,
            "action": "VERIFY_AND_FORWARD",
            "remarks": "Academic grade sheet verified against college tabulations."
        }
        res_r1 = client.post(reverse("verification-round1"), r1_payload, format="json")
        assert res_r1.status_code == status.HTTP_200_OK

        submission.refresh_from_db()
        assert submission.status == "TEACHER_PENDING"
        assert submission.calculated_marks == Decimal("0.00")

        # STEP 3: Round 2 Verification (Class Teacher Check & Forward)
        client.force_login(self.teacher)
        r2_payload = {
            "submission_id": submission_id,
            "action": "VERIFY_AND_FORWARD",
            "remarks": "Class teacher cross-checked grade breakdown."
        }
        res_r2 = client.post(reverse("verification-round2"), r2_payload, format="json")
        assert res_r2.status_code == status.HTTP_200_OK

        submission.refresh_from_db()
        assert submission.status == "EVALUATOR_PENDING"
        assert submission.calculated_marks == Decimal("0.00")

        # STEP 4: Round 3 Verification (Evaluator Approval & Formula Mark Credit)
        client.force_login(self.evaluator)
        r3_payload = {
            "submission_id": submission_id,
            "action": "APPROVE_AND_CREDIT",
            "marks": 38.00,
            "remarks": "Academic marks audited and verified per formula."
        }
        res_r3 = client.post(reverse("verification-round3"), r3_payload, format="json")
        assert res_r3.status_code == status.HTTP_200_OK

        submission.refresh_from_db()
        self.ledger.refresh_from_db()

        assert submission.status == "APPROVED"
        assert submission.calculated_marks == Decimal("38.00")
        assert self.ledger.total_marks == Decimal("38.00")

        logs = VerificationLog.objects.filter(submission_id=submission_id).order_by("id")
        assert logs.count() == 3
        assert logs[0].verification_level == "DQC"
        assert logs[1].verification_level == "CLASS_TEACHER"
        assert logs[2].verification_level == "EVALUATOR"

    # ======================================================================
    # Scenario B: Standard Student — Cat 2 (Swayam Course)
    # Expected: Subcategory lookup mark (5.00) awarded
    # ======================================================================
    def test_scenario_b_standard_subcategory_lifecycle(self, client):
        """
        Scenario B: Full end-to-end flow for subcategory-based mark allocation.
        Step 1: Student submits Swayam Course.
        Step 2: Student Rep performs Round 1 (Check & Forward).
        Step 3: Class Teacher performs Round 2 (Check & Forward).
        Step 4: Evaluator performs Round 3 (Approve & Credit Marks).
        """
        # ------------------------------------------------------------------
        # STEP 1: Student Submits Activity
        # ------------------------------------------------------------------
        client.force_login(self.student)
        submit_payload = {
            "category_id": 2,
            "subcategory_id": 101,
            "proof_url": "https://drive.google.com/file/d/sample_proof",
            "description": "Completed 12-week NPTEL Course"
        }
        res_submit = client.post(reverse("submission-list"), submit_payload, format="json")
        assert res_submit.status_code == 201

        submission_id = res_submit.data["id"]
        submission = Submission.objects.get(id=submission_id)
        assert submission.status == "DQC_PENDING"
        assert submission.calculated_marks == Decimal("0.00")  # Marks NOT calculated yet

        # ------------------------------------------------------------------
        # STEP 2: Round 1 Verification (Student Rep / DQC Check-and-Forward)
        # ------------------------------------------------------------------
        client.force_login(self.student_rep)
        r1_payload = {
            "submission_id": submission_id,
            "action": "VERIFY_AND_FORWARD",
            "remarks": "Proof link accessible and accurate."
        }
        res_r1 = client.post(reverse("verification-round1"), r1_payload, format="json")
        assert res_r1.status_code == 200

        submission.refresh_from_db()
        assert submission.status == "TEACHER_PENDING"
        assert submission.calculated_marks == Decimal("0.00")  # Marks STILL NOT credited

        # ------------------------------------------------------------------
        # STEP 3: Round 2 Verification (Class Teacher Check-and-Forward)
        # ------------------------------------------------------------------
        client.force_login(self.teacher)
        r2_payload = {
            "submission_id": submission_id,
            "action": "VERIFY_AND_FORWARD",
            "remarks": "Verified student participation."
        }
        res_r2 = client.post(reverse("verification-round2"), r2_payload, format="json")
        assert res_r2.status_code == 200

        submission.refresh_from_db()
        assert submission.status == "EVALUATOR_PENDING"
        assert submission.calculated_marks == Decimal("0.00")  # Marks STILL NOT credited

        # ------------------------------------------------------------------
        # STEP 4: Round 3 Verification (Evaluator Final Approval & Marking)
        # ------------------------------------------------------------------
        client.force_login(self.evaluator)
        r3_payload = {
            "submission_id": submission_id,
            "action": "APPROVE_AND_CREDIT",
            "remarks": "Approved. Full 5 marks awarded."
        }
        res_r3 = client.post(reverse("verification-round3"), r3_payload, format="json")
        assert res_r3.status_code == 200

        submission.refresh_from_db()
        self.ledger.refresh_from_db()

        # ------------------------------------------------------------------
        # ASSERTIONS: Final State & Ledger Verification
        # ------------------------------------------------------------------
        assert submission.status == "APPROVED"
        assert submission.calculated_marks == Decimal("5.00")  # Subcategory default mark applied
        assert self.ledger.total_marks == Decimal("5.00")      # Class Ledger updated successfully

        # Verify Audit Log Trail
        logs = VerificationLog.objects.filter(submission_id=submission_id).order_by("id")
        assert logs.count() == 3
        assert logs[0].verification_level == "DQC"
        assert logs[1].verification_level == "CLASS_TEACHER"
        assert logs[2].verification_level == "EVALUATOR"

    # ======================================================================
    # Scenario C: Standard Student — Cat 12 (Career Adv.)
    # Expected: Manual Evaluator input marks awarded
    # ======================================================================
    def test_scenario_c_career_advancement_manual_eval(self, client):
        """
        Scenario C: Category 12 manual evaluation by Evaluator.
        Marks are entered directly by the Evaluator during Round 3 approval.
        """
        # STEP 1: Student submits LinkedIn Profile activity
        client.force_login(self.student)
        submit_payload = {
            "criteriaId": self.crit_item_career.id,
            "academicYear": "2025-2026",
            "description": "Completed full LinkedIn profile with skill badges",
            "proof_url": "https://linkedin.com/in/john-sample",
        }
        res_submit = client.post(reverse("submission-list"), submit_payload, format="json")
        assert res_submit.status_code == status.HTTP_201_CREATED

        submission_id = res_submit.data["id"]
        submission = Submission.objects.get(id=submission_id)
        assert submission.status == "DQC_PENDING"
        assert submission.calculated_marks == Decimal("0.00")

        # STEP 2: Round 1 (Student Rep Check & Forward)
        client.force_login(self.student_rep)
        res_r1 = client.post(reverse("verification-round1"), {
            "submission_id": submission_id,
            "action": "VERIFY_AND_FORWARD",
            "remarks": "Profile link valid and active."
        }, format="json")
        assert res_r1.status_code == status.HTTP_200_OK

        # STEP 3: Round 2 (Class Teacher Check & Forward)
        client.force_login(self.teacher)
        res_r2 = client.post(reverse("verification-round2"), {
            "submission_id": submission_id,
            "action": "VERIFY_AND_FORWARD",
            "remarks": "Verified active career profile."
        }, format="json")
        assert res_r2.status_code == status.HTTP_200_OK

        # STEP 4: Round 3 (Evaluator Manual Evaluation)
        client.force_login(self.evaluator)
        res_r3 = client.post(reverse("verification-round3"), {
            "submission_id": submission_id,
            "action": "APPROVE_AND_CREDIT",
            "marks": 15.00,
            "remarks": "Excellent profile completeness and engagement."
        }, format="json")
        assert res_r3.status_code == status.HTTP_200_OK

        submission.refresh_from_db()
        self.ledger.refresh_from_db()

        assert submission.status == "APPROVED"
        assert submission.calculated_marks == Decimal("15.00")
        assert self.ledger.total_marks == Decimal("15.00")

    # ======================================================================
    # Scenario D: Dual-Role Staff — Cat 3 (NET Exam)
    # Expected: Verification isolation verified (No marks in R2)
    # ======================================================================
    def test_scenario_d_dual_role_staff_isolation(self, client):
        """
        Scenario D: Dual-role staff member holds both Class Teacher and Evaluator roles.
        Must verify that:
        1. In Round 2 (Teacher window), staff cannot award marks (APPROVE_AND_CREDIT is blocked).
        2. In Round 3 (Evaluator window), staff can approve and credit marks.
        """
        # Create submission at TEACHER_PENDING (passed Round 1)
        sub = Submission.objects.create(
            user=self.student,
            class_obj=self.student.class_name,
            category=self.crit_cat3,
            criteria_id=self.crit_item_net.id,
            subcategory_id=self.sub_net.id,
            academic_year="2025-2026",
            status="TEACHER_PENDING",
            description="Passed UGC NET December Cycle",
            proof="https://drive.google.com/file/d/net_cert",
            calculated_marks=0.0
        )

        # 1. Round 2: Dual-role staff logs in as Teacher
        client.force_login(self.dual_staff)

        # Attempting APPROVE_AND_CREDIT at Round 2 is strictly forbidden (403)
        res_r2_invalid = client.post(reverse("verification-round2"), {
            "submission_id": sub.id,
            "action": "APPROVE_AND_CREDIT",
            "marks": 10.0
        }, format="json")
        assert res_r2_invalid.status_code == status.HTTP_403_FORBIDDEN

        # Staff correctly executes VERIFY_AND_FORWARD with marks in payload -> marks stripped
        res_r2 = client.post(reverse("verification-round2"), {
            "submission_id": sub.id,
            "action": "VERIFY_AND_FORWARD",
            "marks": 10.0,  # Must be stripped
            "remarks": "Teacher confirmed NET certificate authenticity."
        }, format="json")
        assert res_r2.status_code == status.HTTP_200_OK

        sub.refresh_from_db()
        self.ledger.refresh_from_db()
        assert sub.status == "EVALUATOR_PENDING"
        assert sub.calculated_marks == Decimal("0.00")
        assert self.ledger.total_marks == Decimal("0.00")

        # 2. Round 3: Dual-role staff switches to Evaluator window
        res_r3 = client.post(reverse("verification-round3"), {
            "submission_id": sub.id,
            "action": "APPROVE_AND_CREDIT",
            "remarks": "Evaluated official NTA UGC NET scorecard."
        }, format="json")
        assert res_r3.status_code == status.HTTP_200_OK

        sub.refresh_from_db()
        self.ledger.refresh_from_db()
        assert sub.status == "APPROVED"
        assert sub.calculated_marks == Decimal("10.00")
        assert self.ledger.total_marks == Decimal("10.00")

    # ======================================================================
    # Scenario E: Student Rep / DQC — Cat 2 (MOOC)
    # Expected: Verification send-back loop validated
    # ======================================================================
    def test_scenario_send_back_rejection_loop(self, client):
        """
        Validates that Round 1 'SEND_BACK' returns submission to student
        and prevents intermediate mark crediting.
        """
        # Create submission directly in DQC_PENDING state
        submission = Submission.objects.create(
            user=self.student,
            class_obj=self.student.class_name,
            category=self.cat2,
            subcategory_id=self.sub_swayam.id,
            criteria_id=self.crit_item_swayam.id,
            status="DQC_PENDING",
            calculated_marks=0.0
        )

        # Student Rep sends back due to broken link
        client.force_login(self.student_rep)
        r1_payload = {
            "submission_id": submission.id,
            "action": "SEND_BACK",
            "remarks": "Invalid Google Drive link permission."
        }
        res_r1 = client.post(reverse("verification-round1"), r1_payload, format="json")
        assert res_r1.status_code == 200

        submission.refresh_from_db()
        assert submission.status == "SENT_BACK"
        assert submission.calculated_marks == Decimal("0.00")

        # Verify Class Ledger remains untouched
        self.ledger.refresh_from_db()
        assert self.ledger.total_marks == Decimal("0.00")

    def test_scenario_e_complete_send_back_resubmit_approval_loop(self, client):
        """
        Scenario E full flow:
        DQC_PENDING -> SENT_BACK -> Student corrections -> DQC_PENDING -> Approved.
        """
        # STEP 1: Student submits MOOC course
        client.force_login(self.student)
        submit_payload = {
            "category_id": 2,
            "subcategory_id": 102,
            "proof_url": "https://drive.google.com/file/d/broken_permission",
            "description": "Coursera Python Course"
        }
        res_submit = client.post(reverse("submission-list"), submit_payload, format="json")
        assert res_submit.status_code == status.HTTP_201_CREATED
        sub_id = res_submit.data["id"]

        # STEP 2: Student Rep sends back
        client.force_login(self.student_rep)
        res_sb = client.post(reverse("verification-round1"), {
            "submission_id": sub_id,
            "action": "SEND_BACK",
            "remarks": "Please provide public view access to Drive link."
        }, format="json")
        assert res_sb.status_code == status.HTTP_200_OK

        sub = Submission.objects.get(id=sub_id)
        assert sub.status == "SENT_BACK"
        assert sub.calculated_marks == Decimal("0.00")

        # STEP 3: Student fixes proof link and resubmits
        client.force_login(self.student)
        res_edit = client.put(f"/api/submissions/{sub_id}/", {
            "proof": "https://drive.google.com/file/d/corrected_public_link",
            "status": "DQC_PENDING",
            "description": "Coursera Python Course - permission updated to Anyone with link"
        }, format="json")
        assert res_edit.status_code == status.HTTP_200_OK

        sub.refresh_from_db()
        assert sub.status == "DQC_PENDING"

        # STEP 4: Student Rep approves correction & forwards
        client.force_login(self.student_rep)
        res_fwd1 = client.post(reverse("verification-round1"), {
            "submission_id": sub_id,
            "action": "VERIFY_AND_FORWARD",
            "remarks": "Link is now accessible."
        }, format="json")
        assert res_fwd1.status_code == status.HTTP_200_OK

        # STEP 5: Teacher forwards
        client.force_login(self.teacher)
        res_fwd2 = client.post(reverse("verification-round2"), {
            "submission_id": sub_id,
            "action": "VERIFY_AND_FORWARD",
            "remarks": "Teacher checked."
        }, format="json")
        assert res_fwd2.status_code == status.HTTP_200_OK

        # STEP 6: Evaluator approves and credits MOOC marks (2.00)
        client.force_login(self.evaluator)
        res_fwd3 = client.post(reverse("verification-round3"), {
            "submission_id": sub_id,
            "action": "APPROVE_AND_CREDIT",
            "remarks": "MOOC certificate evaluated."
        }, format="json")
        assert res_fwd3.status_code == status.HTTP_200_OK

        sub.refresh_from_db()
        self.ledger.refresh_from_db()
        assert sub.status == "APPROVED"
        assert sub.calculated_marks == Decimal("2.00")
        assert self.ledger.total_marks == Decimal("2.00")

    # ======================================================================
    # Supporting Validations: SSO Login & Dynamic Role Resolution
    # ======================================================================
    def test_sso_login_and_dynamic_role_resolution(self):
        """
        Validates institutional SSO email parsing and dynamic role determination.
        """
        # Student email
        assert determine_role_from_email("john.24bca101@mariancollege.org") == "student"

        # Staff email
        assert determine_role_from_email("teacher@mariancollege.org") == "faculty"

        # Badge resolution
        assert get_user_badge(self.student_rep) in ("Student Rep", "DQC member")
        assert get_user_badge(self.student) is None

        # Dual-role resolution
        dual_status = get_user_roles_and_dual_status(self.dual_staff)
        assert dual_status["has_dual_role"] is True
        assert "teacher" in dual_status["available_roles"]
        assert "evaluator" in dual_status["available_roles"]

    # ======================================================================
    # Supporting Validations: Category-Specific Submission Rules
    # ======================================================================
    def test_category_specific_submission_rules(self, client):
        """
        Validates access boundaries across categories:
        - Cat 1 (Academics): DQC-only. Regular student denied (403).
        - Cat 2 (Online Courses): Open to all students.
        """
        client.force_login(self.student)

        # Standard student attempting to submit Cat 1 Academics -> 403 Forbidden
        res_cat1_denied = client.post(reverse("submission-list"), {
            "criteriaId": self.crit_item_academics.id,
            "academicYear": "2025-2026",
            "description": "Student attempting to submit semester grades"
        }, format="json")
        assert res_cat1_denied.status_code == status.HTTP_403_FORBIDDEN

        # Standard student submitting Cat 2 Online Courses -> 201 Created
        res_cat2_ok = client.post(reverse("submission-list"), {
            "category_id": 2,
            "subcategory_id": 101,
            "description": "Valid student Swayam submission",
            "proof_url": "https://drive.google.com/file/d/proof"
        }, format="json")
        assert res_cat2_ok.status_code == status.HTTP_201_CREATED
