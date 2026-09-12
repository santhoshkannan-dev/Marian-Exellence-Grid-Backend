import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from users.models import (
    AcademicYear,
    CriteriaCategory,
    CriteriaItem,
    Submission,
    Class,
    Department,
    UserGroupModel,
    UserGroupMember,
)
from users.scoring_engine import calculate_submission_score

User = get_user_model()


@pytest.mark.django_db
class TestAcademicsWorkflow:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        # Academic Year
        self.ay, _ = AcademicYear.objects.get_or_create(year="2025-2026", defaults={"is_active": True})

        # Department & Class
        self.dept, _ = Department.objects.get_or_create(
            name="PG Department of Computer Applications",
            defaults={"code": "PGDCA", "level": "PG", "email_prefix": "p"}
        )
        self.cls, _ = Class.objects.get_or_create(
            name="25MCA-A",
            defaults={"class_code": "25MCA-A", "batch_year": 2025, "num_students": 50, "department": self.dept}
        )

        # Users
        self.dqc_student, _ = User.objects.get_or_create(
            email="amal.25pmc114@mariancollege.org",
            defaults={"role": "student", "class_name": self.cls, "username": "amal.25pmc114"}
        )
        self.normal_student, _ = User.objects.get_or_create(
            email="normal.student.25pmc001@mariancollege.org",
            defaults={"role": "student", "class_name": self.cls, "username": "normal.student.25pmc001"}
        )

        # Ensure DQC group contains amal
        grp_dqc, _ = UserGroupModel.objects.get_or_create(
            group_id="grp-dqc-student-rep",
            defaults={"name": "DQC Student Rep Group"}
        )
        UserGroupMember.objects.get_or_create(
            group=grp_dqc,
            email=self.dqc_student.email,
            defaults={"user": self.dqc_student}
        )

        # Academics Category & Item
        self.cat_academics, _ = CriteriaCategory.objects.get_or_create(
            code="cat-academics",
            defaults={"category": "Academics", "access_level": "dqc_only"}
        )
        self.crit_sem_result, _ = CriteriaItem.objects.get_or_create(
            category=self.cat_academics,
            title="Sem Result (End Semester Examination)",
            defaults={
                "type": "academic_grades",
                "marks": 0.0,
                "access_level": "dqc_only",
                "rules_json": {
                    "90_above": 5.0,
                    "80_90": 4.0,
                    "70_80": 3.0,
                    "fail": -1.0,
                    "max_per_cycle": 1
                }
            }
        )

        # Clean any prior submissions for this class in this academic year
        Submission.objects.filter(class_obj=self.cls, criteria_id=self.crit_sem_result.id).delete()

    def test_academics_formula_calculation(self):
        """
        Verify exact scoring formula:
        Marks = (count_90_above * 5) + (count_80_90 * 4) + (count_70_80 * 3) + PassBonus - count_fail
        PassBonus:
          100-90.01% -> 5
          90-80.01% -> 4
          80-70.01% -> 3
          70-60.01% -> 2
          60-50.01% -> 1
          <=50% -> 0
        """
        evidence_tier5 = {
            "count_90_above": 10,
            "count_80_90": 5,
            "count_70_80": 2,
            "count_fail": 1,
            "pass_percentage": 92.5
        }
        # (10 * 5) + (5 * 4) + (2 * 3) + 5 - 1 = 50 + 20 + 6 + 5 - 1 = 80.0
        score = calculate_submission_score(self.crit_sem_result, evidence_tier5)
        assert score == 80.0

        # Tier 4: 85% -> bonus 4
        ev_tier4 = dict(evidence_tier5, pass_percentage=85.0)
        assert calculate_submission_score(self.crit_sem_result, ev_tier4) == 79.0

        # Tier 3: 75% -> bonus 3
        ev_tier3 = dict(evidence_tier5, pass_percentage=75.0)
        assert calculate_submission_score(self.crit_sem_result, ev_tier3) == 78.0

        # Tier 2: 65% -> bonus 2
        ev_tier2 = dict(evidence_tier5, pass_percentage=65.0)
        assert calculate_submission_score(self.crit_sem_result, ev_tier2) == 77.0

        # Tier 1: 55% -> bonus 1
        ev_tier1 = dict(evidence_tier5, pass_percentage=55.0)
        assert calculate_submission_score(self.crit_sem_result, ev_tier1) == 76.0

        # Tier 0: 45% -> bonus 0
        ev_tier0 = dict(evidence_tier5, pass_percentage=45.0)
        assert calculate_submission_score(self.crit_sem_result, ev_tier0) == 75.0

        # Edge case: Exactly 50.0% -> bonus 0
        ev_edge_50 = dict(evidence_tier5, pass_percentage=50.0)
        assert calculate_submission_score(self.crit_sem_result, ev_edge_50) == 75.0

        # Edge case: 50.01% -> bonus 1
        ev_edge_5001 = dict(evidence_tier5, pass_percentage=50.01)
        assert calculate_submission_score(self.crit_sem_result, ev_edge_5001) == 76.0

    def test_dqc_successful_submission_workflow(self, client):
        """
        DQC student successfully submits Academics with auto-calculated score.
        """
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.dqc_student)

        payload = {
            "criteriaId": self.crit_sem_result.id,
            "academicYear": "2025-2026",
            "proof_url": "https://drive.google.com/file/d/academics_result_sheet",
            "count_90_above": 10,
            "count_80_90": 5,
            "count_70_80": 2,
            "count_fail": 1,
            "pass_percentage": 92.5,
            "status": "Submitted"
        }

        res = client.post(reverse("submission-list"), payload, format="json")
        assert res.status_code == status.HTTP_201_CREATED, res.data
        sub_id = res.data["id"]
        sub = Submission.objects.get(id=sub_id)

        # Expected score: 80.0
        assert sub.calculated_marks == 80.0
        assert sub.proof == "https://drive.google.com/file/d/academics_result_sheet"
        # Automatic description fallback
        assert "Sem Result" in sub.description

    def test_optional_fields_allowed_empty(self, client):
        """
        User requirement: All fields except proof_url are optional.
        Submission with only proof_url should succeed.
        """
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.dqc_student)

        # Clear prior submissions to avoid cycle limit
        Submission.objects.filter(class_obj=self.cls, criteria_id=self.crit_sem_result.id).delete()

        payload = {
            "criteriaId": self.crit_sem_result.id,
            "academicYear": "2025-2026",
            "proof_url": "https://drive.google.com/file/d/only_proof_url",
            "status": "Submitted"
        }

        res = client.post(reverse("submission-list"), payload, format="json")
        assert res.status_code == status.HTTP_201_CREATED, res.data
        sub_id = res.data["id"]
        sub = Submission.objects.get(id=sub_id)
        assert sub.calculated_marks == 0.0
        assert sub.proof == "https://drive.google.com/file/d/only_proof_url"
        assert "Sem Result" in sub.description

    def test_non_dqc_student_rejected(self, client):
        """
        Non-DQC student attempting to submit Academics must be rejected with 403 Forbidden.
        """
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.normal_student)

        payload = {
            "criteriaId": self.crit_sem_result.id,
            "academicYear": "2025-2026",
            "proof_url": "https://drive.google.com/file/d/non_dqc_attempt",
            "count_90_above": 5,
            "status": "Submitted"
        }

        res = client.post(reverse("submission-list"), payload, format="json")
        assert res.status_code == status.HTTP_403_FORBIDDEN
        assert "DQC members only" in str(res.data.get("error", ""))

    def test_cycle_limit_max_one_per_cycle(self, client):
        """
        Category 1 (Academics) is limited to Max 1/cycle per class.
        Subsequent submission in the same cycle must be rejected with 400 Bad Request.
        """
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.dqc_student)

        # Clear prior submissions
        Submission.objects.filter(class_obj=self.cls, criteria_id=self.crit_sem_result.id).delete()

        # 1st Submission -> OK
        payload = {
            "criteriaId": self.crit_sem_result.id,
            "academicYear": "2025-2026",
            "proof_url": "https://drive.google.com/file/d/first_submission",
            "count_90_above": 5,
            "status": "Submitted"
        }
        res1 = client.post(reverse("submission-list"), payload, format="json")
        assert res1.status_code == status.HTTP_201_CREATED

        # 2nd Submission -> Rejected
        payload2 = {
            "criteriaId": self.crit_sem_result.id,
            "academicYear": "2025-2026",
            "proof_url": "https://drive.google.com/file/d/second_submission",
            "count_90_above": 3,
            "status": "Submitted"
        }
        res2 = client.post(reverse("submission-list"), payload2, format="json")
        assert res2.status_code == status.HTTP_400_BAD_REQUEST
        assert "Limit reached" in str(res2.data.get("error", ""))
