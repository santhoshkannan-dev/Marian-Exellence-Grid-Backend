"""
Unit and Integration Test Suite: Round 3 Evaluator Window Marking Engine & Verification Controller
================================================================================================
Covers:
1. Category 1 Formula-Driven Calculation & Pass Bonus Tiers
2. Categories 2-11 Lookup-Driven Engine & Base Mark Protection
3. Category 12 Manual Input Engine & Validation
4. Missing Subcategory Error Handling
5. DQC-Restricted Category & Role Access Controls (HTTP 403 Forbidden on illegal combinations)
6. Round 3 Finality (intermediate rounds hold 0.00 marks; only Round 3 credits marks)
7. Atomic Database Transaction & ClassLedger Mark Allocation
"""

import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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
)
from evaluation_engine import (
    calculate_pass_bonus,
    calculate_academics_marks,
    resolve_evaluator_marks,
)

User = get_user_model()


class TestEvaluationEnginePureLogic:
    """Test mathematical formulas and specification engine logic directly."""

    def test_calculate_pass_bonus_tiers(self):
        """Verify pass bonus for all defined tiers (>90%, >80%, >70%, >60%, >50%, <=50%)."""
        # Tier 5: > 90.0%
        assert calculate_pass_bonus(95.0) == Decimal("5.00")
        assert calculate_pass_bonus(90.01) == Decimal("5.00")

        # Tier 4: > 80.0% and <= 90.0%
        assert calculate_pass_bonus(90.0) == Decimal("4.00")
        assert calculate_pass_bonus(85.0) == Decimal("4.00")
        assert calculate_pass_bonus(80.01) == Decimal("4.00")

        # Tier 3: > 70.0% and <= 80.0%
        assert calculate_pass_bonus(80.0) == Decimal("3.00")
        assert calculate_pass_bonus(75.5) == Decimal("3.00")
        assert calculate_pass_bonus(70.01) == Decimal("3.00")

        # Tier 2: > 60.0% and <= 70.0%
        assert calculate_pass_bonus(70.0) == Decimal("2.00")
        assert calculate_pass_bonus(65.0) == Decimal("2.00")
        assert calculate_pass_bonus(60.01) == Decimal("2.00")

        # Tier 1: > 50.0% and <= 60.0%
        assert calculate_pass_bonus(60.0) == Decimal("1.00")
        assert calculate_pass_bonus(55.0) == Decimal("1.00")
        assert calculate_pass_bonus(50.01) == Decimal("1.00")

        # Tier 0: <= 50.0%
        assert calculate_pass_bonus(50.0) == Decimal("0.00")
        assert calculate_pass_bonus(45.0) == Decimal("0.00")
        assert calculate_pass_bonus(0.0) == Decimal("0.00")

    def test_calculate_academics_marks_formula(self):
        """
        Formula: (N_>=90% * 5) + (N_80-90% * 4) + (N_70-80% * 3) + PassBonus - N_Fail
        """
        metadata = {
            "count_90_above": 2,   # 2 * 5 = 10
            "count_80_90": 4,      # 4 * 4 = 16
            "count_70_80": 3,      # 3 * 3 = 9
            "count_fail": 1,       # -1
            "pass_percentage": 90.0  # Tier 4 bonus = 4.00
        }
        # 10 + 16 + 9 + 4.00 - 1 = 38.00
        assert calculate_academics_marks(metadata) == Decimal("38.00")

    def test_calculate_academics_marks_non_negative_clamp(self):
        """Ensure academics marks clamp to 0.00 when net calculation is negative."""
        metadata = {
            "count_90_above": 0,
            "count_80_90": 0,
            "count_70_80": 0,
            "count_fail": 10,       # -10
            "pass_percentage": 10.0  # bonus 0
        }
        # -10 clamped to 0.00
        assert calculate_academics_marks(metadata) == Decimal("0.00")

    def test_resolve_evaluator_marks_category_1(self):
        """Category 1 evaluates dynamically via formula."""
        class MockSubmission:
            category_id = 1
            metadata = {
                "count_90_above": 10,
                "count_80_90": 5,
                "count_70_80": 2,
                "count_fail": 1,
                "pass_percentage": 92.5  # bonus 5.00
            }
            # (10*5) + (5*4) + (2*3) + 5 - 1 = 50 + 20 + 6 + 5 - 1 = 80.00

        sub = MockSubmission()
        assert resolve_evaluator_marks(sub) == Decimal("80.00")

    def test_resolve_evaluator_marks_category_12_manual(self):
        """Category 12 requires valid manual mark input."""
        class MockSubmission:
            category_id = 12

        sub = MockSubmission()
        # Valid input
        assert resolve_evaluator_marks(sub, Decimal("15.50")) == Decimal("15.50")
        assert resolve_evaluator_marks(sub, Decimal("0.00")) == Decimal("0.00")

        # Missing input raises ValidationError
        with pytest.raises(ValidationError):
            resolve_evaluator_marks(sub, None)

        # Negative input raises ValidationError
        with pytest.raises(ValidationError):
            resolve_evaluator_marks(sub, Decimal("-1.00"))

    def test_resolve_evaluator_marks_category_lookup_protection(self):
        """Categories 2-11 resolve via subcategory default marks and ignore manual alteration."""
        class MockSubcategory:
            default_marks = Decimal("5.00")

        class MockSubmission:
            category_id = 2
            subcategory = MockSubcategory()

        sub = MockSubmission()
        # Evaluator attempting to pass arbitrary manual marks is ignored; lookup mark is returned
        assert resolve_evaluator_marks(sub, Decimal("99.00")) == Decimal("5.00")

    def test_resolve_evaluator_marks_missing_subcategory_raises_error(self):
        """Categories 2-11 without subcategory must raise ValidationError."""
        class MockSubmission:
            category_id = 2
            subcategory = None
            subcategory_id = None
            category = None
            criteria_id = None
            evidence = None

        sub = MockSubmission()
        with pytest.raises(ValidationError):
            resolve_evaluator_marks(sub)


@pytest.mark.django_db
class TestRound3EvaluatorWindowIntegration:
    """Integration tests verifying backend controllers, access controls, and transactions."""

    @pytest.fixture
    def client(self):
        from rest_framework.test import APIClient
        return APIClient()

    @pytest.fixture(autouse=True)
    def setup_data(self):
        # 1. Academic Year
        self.ay, _ = AcademicYear.objects.get_or_create(year="2025-2026", defaults={"is_active": True})

        # 2. Users
        self.standard_student = User.objects.create_user(
            email="student.24bca201@mariancollege.org",
            role="STUDENT",
            class_id="24BCA1"
        )
        self.dqc_student_rep = User.objects.create_user(
            email="rep.24bca201@mariancollege.org",
            role="STUDENT_REP",
            class_id="24BCA1"
        )
        self.teacher = User.objects.create_user(
            email="faculty.teacher@mariancollege.org",
            role="FACULTY",
            is_class_teacher=True,
            assigned_class_id="24BCA1"
        )
        self.evaluator_cat1 = User.objects.create_user(
            email="evaluator.cat1@mariancollege.org",
            role="FACULTY",
            is_evaluator=True,
            evaluator_category_id=1
        )
        self.evaluator_cat2 = User.objects.create_user(
            email="evaluator.cat2@mariancollege.org",
            role="FACULTY",
            is_evaluator=True,
            evaluator_category_id=2
        )
        self.evaluator_cat12 = User.objects.create_user(
            email="evaluator.cat12@mariancollege.org",
            role="FACULTY",
            is_evaluator=True,
            evaluator_category_id=12
        )

        # 3. Categories & Criteria Items
        # Category 1: Academics (DQC-restricted, Formula)
        self.cat1 = Category.objects.create(id=1, name="Academics")
        self.crit_cat1, _ = CriteriaCategory.objects.get_or_create(
            code="cat-academics",
            defaults={"category": "Academics", "access_level": "dqc_only", "evaluators": [self.evaluator_cat1.email]}
        )
        self.crit_item_cat1, _ = CriteriaItem.objects.get_or_create(
            category=self.crit_cat1,
            title="Class Semester Results",
            defaults={"type": "academic_grades", "marks": 50.0, "access_level": "dqc_only"}
        )
        EvaluatorCategoryAssignment.objects.get_or_create(
            category=self.crit_cat1,
            evaluator=self.evaluator_cat1,
            academic_year=2025
        )

        # Category 2: Online Courses (Lookup)
        self.cat2 = Category.objects.create(id=2, name="Online Courses")
        self.sub_swayam = Subcategory.objects.create(
            id=201,
            category=self.cat2,
            subcategory_name="Swayam / NPTEL Course",
            default_marks=Decimal("5.00")
        )
        self.crit_cat2, _ = CriteriaCategory.objects.get_or_create(
            code="cat-online-courses",
            defaults={"category": "Online Courses", "access_level": "all_students", "evaluators": [self.evaluator_cat2.email]}
        )
        self.crit_item_cat2, _ = CriteriaItem.objects.get_or_create(
            category=self.crit_cat2,
            title="Swayam Course Certification",
            defaults={"type": "fixed", "marks": 5.0, "access_level": "all_students"}
        )
        EvaluatorCategoryAssignment.objects.get_or_create(
            category=self.crit_cat2,
            evaluator=self.evaluator_cat2,
            academic_year=2025
        )

        # Category 8: Prizes Won (Hybrid - Group is DQC-restricted)
        self.cat8 = Category.objects.create(id=8, name="Prizes Won")
        self.sub_group_prize = Subcategory.objects.create(
            id=208,
            category=self.cat8,
            subcategory_name="1st Prize (Group)",
            default_marks=Decimal("7.00"),
            requires_dqc=True
        )
        self.crit_cat8, _ = CriteriaCategory.objects.get_or_create(
            code="cat-prizes",
            defaults={"category": "Prizes Won", "access_level": "all_students", "evaluators": [self.evaluator_cat2.email]}
        )
        self.crit_item_cat8_group, _ = CriteriaItem.objects.get_or_create(
            category=self.crit_cat8,
            title="1st Prize (Group)",
            defaults={"type": "fixed", "marks": 7.0, "access_level": "dqc_only"}
        )
        EvaluatorCategoryAssignment.objects.get_or_create(
            category=self.crit_cat8,
            evaluator=self.evaluator_cat2,
            academic_year=2025
        )

        # Category 12: Career Advancement (Manual Input)
        self.cat12 = Category.objects.create(id=12, name="Career Advancement")
        self.crit_cat12, _ = CriteriaCategory.objects.get_or_create(
            code="cat-career-advancement",
            defaults={"category": "Career Advancement", "is_manual_eval": True, "evaluators": [self.evaluator_cat12.email]}
        )
        self.crit_item_cat12, _ = CriteriaItem.objects.get_or_create(
            category=self.crit_cat12,
            title="LinkedIn - Profile Completion (Active Profile)",
            defaults={"type": "fixed", "marks": 15.0, "is_manual_eval": True, "access_level": "all_students"}
        )
        EvaluatorCategoryAssignment.objects.get_or_create(
            category=self.crit_cat12,
            evaluator=self.evaluator_cat12,
            academic_year=2025
        )

        # 4. Class Ledger
        self.ledger, _ = ClassLedger.objects.get_or_create(
            class_id="24BCA1",
            defaults={"total_marks": Decimal("0.00")}
        )
        self.ledger.total_marks = Decimal("0.00")
        self.ledger.save()

    def test_dqc_restricted_category_approving_illegal_combination_forbidden(self, client):
        """
        Role & Access Controls:
        If a submission belongs to a DQC-restricted category (e.g. Category 1 Academics)
        but was authored by a standard student, attempting to approve it at Round 3
        MUST throw an HTTP 403 Forbidden error.
        """
        # Create submission directly with a standard student as author
        sub = Submission.objects.create(
            user=self.standard_student,
            class_obj=self.standard_student.class_name,
            category=self.crit_cat1,
            criteria_id=self.crit_item_cat1.id,
            status="EVALUATOR_PENDING",
            description="Illegal standard student academic submission",
            evidence={
                "totalStudents": 10,
                "grades": {"S": 2, "APlus": 4, "A": 3, "Fail": 1}
            }
        )

        client.force_login(self.evaluator_cat1)
        res = client.post(reverse("verification-round3"), {
            "submission_id": sub.id,
            "action": "APPROVE_AND_CREDIT",
            "remarks": "Attempting approval on illegal combination"
        }, format="json")

        assert res.status_code == status.HTTP_403_FORBIDDEN
        assert "strictly restricted to DQC Members" in res.data["error"]
        sub.refresh_from_db()
        assert sub.status == "EVALUATOR_PENDING"
        assert sub.calculated_marks == Decimal("0.00")

    def test_cat8_group_prize_approving_illegal_combination_forbidden(self, client):
        """
        Category 8 Group subcategories are DQC-restricted.
        Attempting to approve a group prize submission authored by a normal student
        MUST throw an HTTP 403 Forbidden error.
        """
        sub = Submission.objects.create(
            user=self.standard_student,
            class_obj=self.standard_student.class_name,
            category=self.crit_cat8,
            criteria_id=self.crit_item_cat8_group.id,
            subcategory_id=self.sub_group_prize.id,
            status="EVALUATOR_PENDING",
            description="Standard student claiming group prize"
        )

        client.force_login(self.evaluator_cat2)
        res = client.post(reverse("verification-round3"), {
            "submission_id": sub.id,
            "action": "APPROVE_AND_CREDIT",
            "remarks": "Attempting approval"
        }, format="json")

        assert res.status_code == status.HTTP_403_FORBIDDEN
        assert "strictly restricted to DQC Members" in res.data["error"]

    def test_lookup_driven_overrides_evaluator_manual_alteration(self, client):
        """
        Lookup-Driven (Categories 2–11):
        Auto-calculated directly from subcategory's predefined default_marks.
        Evaluators cannot alter the base mark.
        """
        sub = Submission.objects.create(
            user=self.standard_student,
            class_obj=self.standard_student.class_name,
            category=self.cat2,
            subcategory_id=self.sub_swayam.id,
            criteria_id=self.crit_item_cat2.id,
            status="EVALUATOR_PENDING",
            description="Completed NPTEL Course"
        )

        client.force_login(self.evaluator_cat2)
        # Evaluator sends 99.00 in payload, but lookup mark is 5.00
        res = client.post(reverse("verification-round3"), {
            "submission_id": sub.id,
            "action": "APPROVE_AND_CREDIT",
            "marks": 99.00,
            "remarks": "Approved. Base mark must prevail."
        }, format="json")

        assert res.status_code == status.HTTP_200_OK
        sub.refresh_from_db()
        self.ledger.refresh_from_db()

        # Must be 5.00, NOT 99.00
        assert sub.status == "APPROVED"
        assert sub.calculated_marks == Decimal("5.00")
        assert sub.marks == 5
        assert self.ledger.total_marks == Decimal("5.00")

    def test_category_12_manual_entry_requires_explicit_input(self, client):
        """
        Category 12 requires explicit manual numeric entry by the Evaluator.
        Missing or negative marks must return HTTP 400 Bad Request.
        """
        sub = Submission.objects.create(
            user=self.standard_student,
            class_obj=self.standard_student.class_name,
            category=self.crit_cat12,
            criteria_id=self.crit_item_cat12.id,
            status="EVALUATOR_PENDING",
            description="LinkedIn Profile Completion"
        )

        client.force_login(self.evaluator_cat12)

        # 1. Missing marks
        res_missing = client.post(reverse("verification-round3"), {
            "submission_id": sub.id,
            "action": "APPROVE_AND_CREDIT",
            "remarks": "Missing marks input"
        }, format="json")
        assert res_missing.status_code == status.HTTP_400_BAD_REQUEST

        # 2. Negative marks
        res_neg = client.post(reverse("verification-round3"), {
            "submission_id": sub.id,
            "action": "APPROVE_AND_CREDIT",
            "marks": -5.0,
            "remarks": "Negative marks input"
        }, format="json")
        assert res_neg.status_code == status.HTTP_400_BAD_REQUEST

        # 3. Valid manual marks
        res_valid = client.post(reverse("verification-round3"), {
            "submission_id": sub.id,
            "action": "APPROVE_AND_CREDIT",
            "marks": 15.00,
            "remarks": "Approved per institutional guidelines"
        }, format="json")
        assert res_valid.status_code == status.HTTP_200_OK

        sub.refresh_from_db()
        self.ledger.refresh_from_db()
        assert sub.status == "APPROVED"
        assert sub.calculated_marks == Decimal("15.00")
        assert self.ledger.total_marks == Decimal("15.00")

    def test_round3_finality_intermediate_states_hold_zero_marks(self, client):
        """
        Round 3 Finality:
        Rounds 1 and 2 function strictly as check-and-forward steps without awarding marks.
        Intermediate states hold 0.00 marks; only Round 3 credits marks.
        """
        # STEP 1: DQC Student Rep submits Academics
        client.force_login(self.dqc_student_rep)
        res_submit = client.post(reverse("submission-list"), {
            "criteriaId": self.crit_item_cat1.id,
            "academicYear": "2025-2026",
            "description": "Semester 1 Result for 24BCA1",
            "proof_url": "https://drive.google.com/file/d/proof_academics",
            "evidence": {
                "totalStudents": 10,
                "grades": {"S": 2, "APlus": 4, "A": 3, "Fail": 1}
            }
        }, format="json")
        assert res_submit.status_code == status.HTTP_201_CREATED
        sub_id = res_submit.data["id"]

        sub = Submission.objects.get(id=sub_id)
        assert sub.status == "DQC_PENDING"
        assert sub.calculated_marks == Decimal("0.00")
        assert sub.marks is None

        # STEP 2: Round 1 (DQC Rep check & forward)
        client.force_login(self.dqc_student_rep)
        res_r1 = client.post(reverse("verification-round1"), {
            "submission_id": sub_id,
            "action": "VERIFY_AND_FORWARD",
            "remarks": "Check & forward by DQC"
        }, format="json")
        assert res_r1.status_code == status.HTTP_200_OK

        sub.refresh_from_db()
        assert sub.status == "TEACHER_PENDING"
        assert sub.calculated_marks == Decimal("0.00")
        assert sub.marks is None

        # STEP 3: Round 2 (Teacher check & forward)
        client.force_login(self.teacher)
        res_r2 = client.post(reverse("verification-round2"), {
            "submission_id": sub_id,
            "action": "VERIFY_AND_FORWARD",
            "remarks": "Check & forward by Teacher"
        }, format="json")
        assert res_r2.status_code == status.HTTP_200_OK

        sub.refresh_from_db()
        assert sub.status == "EVALUATOR_PENDING"
        assert sub.calculated_marks == Decimal("0.00")
        assert sub.marks is None
        assert self.ledger.total_marks == Decimal("0.00")

        # STEP 4: Round 3 (Evaluator Approval & Formula Calculation)
        client.force_login(self.evaluator_cat1)
        res_r3 = client.post(reverse("verification-round3"), {
            "submission_id": sub_id,
            "action": "APPROVE_AND_CREDIT",
            "remarks": "Evaluated per Cat 1 formula"
        }, format="json")
        assert res_r3.status_code == status.HTTP_200_OK

        sub.refresh_from_db()
        self.ledger.refresh_from_db()

        # Only now are marks credited: 10 + 16 + 9 + 4 - 1 = 38.00
        assert sub.status == "APPROVED"
        assert sub.calculated_marks == Decimal("38.00")
        assert self.ledger.total_marks == Decimal("38.00")
