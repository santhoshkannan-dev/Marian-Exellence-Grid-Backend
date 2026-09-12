import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

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

User = get_user_model()


@pytest.mark.django_db
class TestAcademicFieldsPositiveValidation:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.ay, _ = AcademicYear.objects.get_or_create(year="2025-2026", defaults={"is_active": True})

        self.dept, _ = Department.objects.get_or_create(
            name="PG Department of Computer Applications",
            defaults={"code": "PGDCA", "level": "PG", "email_prefix": "p"}
        )
        self.cls, _ = Class.objects.get_or_create(
            name="25MCA-A",
            defaults={"class_code": "25MCA-A", "batch_year": 2025, "num_students": 50, "department": self.dept}
        )

        self.dqc_student, _ = User.objects.get_or_create(
            email="dqc.validator@mariancollege.org",
            defaults={"role": "student", "class_name": self.cls, "username": "dqc.validator"}
        )

        grp_dqc, _ = UserGroupModel.objects.get_or_create(
            group_id="grp-dqc-student-rep",
            defaults={"name": "DQC Student Rep Group"}
        )
        UserGroupMember.objects.get_or_create(
            group=grp_dqc,
            email=self.dqc_student.email,
            defaults={"user": self.dqc_student}
        )

        self.cat_acad, _ = CriteriaCategory.objects.get_or_create(
            code="cat-academics",
            defaults={"category": "Academics", "access_level": "dqc_only"}
        )
        self.crit_item, _ = CriteriaItem.objects.get_or_create(
            category=self.cat_acad,
            title="Sem Result (End Semester Examination)",
            defaults={
                "type": "academic_grades",
                "marks": 0.0,
                "access_level": "dqc_only",
                "rules_json": {
                    "passBonusTiers": [
                        {"min": 90.01, "max": 100.0, "marks": 5.0},
                        {"min": 80.01, "max": 90.0, "marks": 4.0},
                        {"min": 70.01, "max": 80.0, "marks": 3.0},
                        {"min": 60.01, "max": 70.0, "marks": 2.0},
                        {"min": 50.01, "max": 60.0, "marks": 1.0},
                        {"min": 0, "max": 50.0, "marks": 0}
                    ]
                }
            }
        )

        Submission.objects.filter(class_obj=self.cls, criteria_id=self.crit_item.id).delete()
        self.client = APIClient()
        self.client.force_authenticate(user=self.dqc_student)

    def test_reject_negative_count_fields(self):
        """Reject negative values like -5 or '-3' in any academic count field."""
        for field in ['count_90_above', 'count_80_90', 'count_70_80', 'count_fail']:
            # Int negative
            payload = {
                "criteriaId": self.crit_item.id,
                "academicYear": "2025-2026",
                "proof_url": "https://drive.google.com/file/d/test_neg",
                "status": "Submitted",
                field: -5
            }
            res = self.client.post(reverse("submission-list"), payload, format="json")
            assert res.status_code == status.HTTP_400_BAD_REQUEST, f"Allowed negative int for {field}"
            assert "cannot be negative" in res.data["error"] or "positive" in res.data["error"]

            # String negative
            payload[field] = "-1"
            res = self.client.post(reverse("submission-list"), payload, format="json")
            assert res.status_code == status.HTTP_400_BAD_REQUEST, f"Allowed negative str for {field}"

    def test_reject_symbols_in_count_fields(self):
        """Reject symbols like '+', '*', '/', 'e', 'E', letters in count fields."""
        invalid_values = ["+5", "5*2", "1e3", "5/2", "abc", "5+2", "2.5"]
        for field in ['count_90_above', 'count_80_90', 'count_70_80', 'count_fail']:
            for invalid_val in invalid_values:
                payload = {
                    "criteriaId": self.crit_item.id,
                    "academicYear": "2025-2026",
                    "proof_url": "https://drive.google.com/file/d/test_symbols",
                    "status": "Submitted",
                    field: invalid_val
                }
                res = self.client.post(reverse("submission-list"), payload, format="json")
                assert res.status_code == status.HTTP_400_BAD_REQUEST, f"Allowed '{invalid_val}' for {field}"
                assert "positive" in res.data["error"] or "whole number" in res.data["error"] or "decimal" in res.data["error"]

    def test_reject_invalid_pass_percentage(self):
        """Reject negative, >100, or symbol-containing pass_percentage values."""
        invalid_percentages = [-5.0, 105.0, "-10", "+85.5", "85*2", "1e2", "85%", "pass"]
        for invalid_val in invalid_percentages:
            payload = {
                "criteriaId": self.crit_item.id,
                "academicYear": "2025-2026",
                "proof_url": "https://drive.google.com/file/d/test_pct",
                "status": "Submitted",
                "pass_percentage": invalid_val
            }
            res = self.client.post(reverse("submission-list"), payload, format="json")
            assert res.status_code == status.HTTP_400_BAD_REQUEST, f"Allowed '{invalid_val}' for pass_percentage"
            assert "pass_percentage" in res.data["error"].lower()

    def test_reject_missing_proof_url_on_submit(self):
        """Reject submission when status is Submitted and proof_url is missing."""
        payload = {
            "criteriaId": self.crit_item.id,
            "academicYear": "2025-2026",
            "count_90_above": 5,
            "status": "Submitted"
        }
        res = self.client.post(reverse("submission-list"), payload, format="json")
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "proof_url is required" in res.data["error"]

    def test_accept_valid_positive_academic_submission(self):
        """Accept valid clean positive counts, percentage, and proof_url."""
        payload = {
            "criteriaId": self.crit_item.id,
            "academicYear": "2025-2026",
            "proof_url": "https://drive.google.com/file/d/valid_academic",
            "count_90_above": 10,
            "count_80_90": 5,
            "count_70_80": 2,
            "count_fail": 1,
            "pass_percentage": 92.5,
            "description": "Semester 4 End Semester Results",
            "status": "Submitted"
        }
        res = self.client.post(reverse("submission-list"), payload, format="json")
        assert res.status_code == status.HTTP_201_CREATED, res.data
        sub_id = res.data["id"]

        sub = Submission.objects.get(id=sub_id)
        assert sub.evidence["count_90_above"] == 10
        assert sub.evidence["count_80_90"] == 5
        assert sub.evidence["count_70_80"] == 2
        assert sub.evidence["count_fail"] == 1
        assert sub.evidence["pass_percentage"] == 92.5

        # Also test updating with negative count fails
        update_res = self.client.put(
            reverse("submission-detail", kwargs={"pk": sub.id}),
            {"count_90_above": -3, "status": "Submitted"},
            format="json"
        )
        assert update_res.status_code == status.HTTP_400_BAD_REQUEST

        # Also test updating with symbol fails
        update_symbol_res = self.client.put(
            reverse("submission-detail", kwargs={"pk": sub.id}),
            {"count_fail": "1+1", "status": "Submitted"},
            format="json"
        )
        assert update_symbol_res.status_code == status.HTTP_400_BAD_REQUEST
