"""
Modular Views Package for Marian Best Class.
Exports all view classes and domain helper functions for 100% backward compatibility with
existing URLs, tests, and management commands.
"""

from users.scoring_engine import calculate_submission_score
from users.services.user_service import UserService
from users.services.submission_service import SubmissionService
from users.services.ranking_service import RankingService

from .base import (
    get_client_ip,
    create_audit_entry,
    record_system_audit_event,
    determine_role_from_email,
    parse_name_from_email,
    parse_email_code,
    get_active_year_start,
    get_year_roman,
    parse_student_email,
    allocate_student_from_email,
    get_tokens_for_user,
    is_user_student_rep,
    check_duplicate_submission,
    get_online_courses_item_ids,
    get_upsc_psc_item_ids,
    get_criteria_allowed_bounds,
    VALID_STATE_TRANSITIONS,
    UNEDITABLE_BY_STUDENT_STATES,
)

from .auth_views import (
    GoogleLoginView,
    LogoutView,
    CustomTokenRefreshView,
    DevBypassLoginView,
    UserProfileView,
    UserManagementView,
)

from .academic_views import (
    AcademicYearListView,
    DepartmentListView,
    DepartmentDetailView,
    CourseListView,
    CourseDetailView,
    ClassListView,
    ClassDetailView,
)

from .criteria_views import (
    CriteriaVersionListView,
    CriteriaVersionDetailView,
    CriteriaCategoryListView,
    CriteriaCategoryDetailView,
    CriteriaItemListView,
    CriteriaItemDetailView,
)

from .submission_views import (
    SubmissionListView,
    SubmissionDetailView,
)

from .evidence_views import (
    SubmissionEvidenceView,
)

from .ranking_views import (
    ClassIndexView,
    ChampionListView,
    ChampionDetailView,
)

from .system_views import (
    SystemSettingView,
    UserGroupListView,
    UserGroupDetailView,
    UserGroupMemberActionView,
    BugReportView,
    SystemAuditLogView,
    SubmissionAuditTrailView,
)

from .health_views import (
    HealthCheckView,
)

__all__ = [
    # Health Check
    "HealthCheckView",
    # Domain helper functions & constants
    "get_client_ip",
    "create_audit_entry",
    "record_system_audit_event",
    "determine_role_from_email",
    "parse_name_from_email",
    "parse_email_code",
    "get_active_year_start",
    "get_year_roman",
    "parse_student_email",
    "allocate_student_from_email",
    "get_tokens_for_user",
    "is_user_student_rep",
    "check_duplicate_submission",
    "get_online_courses_item_ids",
    "get_upsc_psc_item_ids",
    "get_criteria_allowed_bounds",
    "calculate_submission_score",
    "VALID_STATE_TRANSITIONS",
    "UNEDITABLE_BY_STUDENT_STATES",
    # Auth Views
    "GoogleLoginView",
    "LogoutView",
    "CustomTokenRefreshView",
    "DevBypassLoginView",
    "UserProfileView",
    "UserManagementView",
    # Academic Views
    "AcademicYearListView",
    "DepartmentListView",
    "DepartmentDetailView",
    "CourseListView",
    "CourseDetailView",
    "ClassListView",
    "ClassDetailView",
    # Criteria Views
    "CriteriaVersionListView",
    "CriteriaVersionDetailView",
    "CriteriaCategoryListView",
    "CriteriaCategoryDetailView",
    "CriteriaItemListView",
    "CriteriaItemDetailView",
    # Submission Views
    "SubmissionListView",
    "SubmissionDetailView",
    # Evidence Views
    "SubmissionEvidenceView",
    # Ranking Views
    "ClassIndexView",
    "ChampionListView",
    "ChampionDetailView",
    # System Views
    "SystemSettingView",
    "UserGroupListView",
    "UserGroupDetailView",
    "BugReportView",
    "SystemAuditLogView",
    "SubmissionAuditTrailView",
]
