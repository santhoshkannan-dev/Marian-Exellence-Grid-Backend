import hashlib
from django.db import models
from django.contrib.auth.models import AbstractUser

DEPARTMENT_LEVEL_CHOICES = [
    ('UG', 'Under-Graduate'),
    ('PG', 'Post-Graduate'),
    ('Professional', 'Professional'),
    ('Other', 'Other'),
]

USER_ROLE_CHOICES = [
    ("student", "Student"),
    ("faculty", "Faculty"),
    ("evaluation", "Evaluation Team"),
    ("iqac", "IQAC"),
    ("admin", "Admin"),
]

SUBMISSION_STATUS_CHOICES = [
    ('Approved', 'Approved'),
    ('Pending', 'Pending'),
    ('Pending Rep Verification', 'Pending Rep Verification'),
    ('Student Rep Verified', 'Student Rep Verified'),
    ('Teacher Verified', 'Teacher Verified'),
    ('Correction Requested', 'Correction Requested'),
    ('Rejected', 'Rejected'),
    ('Draft', 'Draft'),
    ('Submitted', 'Submitted'),
    ('Verified', 'Verified'),
    ('Evaluated', 'Evaluated'),
    ('Locked', 'Locked'),
    ('Correction', 'Correction'),
]

BUG_TYPE_CHOICES = [
    ('UI', 'UI / Layout'),
    ('Function', 'Functionality / Logic'),
    ('Performance', 'Performance / Speed'),
    ('Other', 'Other'),
]

BUG_PRIORITY_CHOICES = [
    ('Low', 'Low'),
    ('Medium', 'Medium'),
    ('High', 'High'),
]

class AcademicYear(models.Model):
    year = models.CharField(max_length=20, unique=True) # e.g. "2025-2026"
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['is_active'],
                condition=models.Q(is_active=True),
                name='unique_active_academic_year'
            ),
            models.CheckConstraint(
                condition=models.Q(year__regex=r'^\d{4}-\d{4}$'),
                name='check_academic_year_format'
            ),
        ]

    def __str__(self):
        return f"{self.year} {'(Active)' if self.is_active else ''}"

class Department(models.Model):
    LEVEL_CHOICES = [
        ('UG', 'Under-Graduate'),
        ('PG', 'Post-Graduate'),
        ('Professional', 'Professional'),
        ('Other', 'Other'),
    ]
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True) # e.g. PGDCA, UGDCA
    # Email prefix: the single character that appears after the batch year in student emails
    # e.g. 'p' for PG (amal.25pmc114), 'u' for UG (santhosh.25ubc214)
    email_prefix = models.CharField(max_length=5, blank=True, default='')  # 'u', 'p'
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='UG')
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(level__in=['UG', 'PG', 'Professional', 'Other']),
                name='check_department_level_valid'
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Course(models.Model):
    """A course offered by a department (e.g. MCA, BCA, BBA).
    Each course carries the 2-char email_code that appears in student emails.
    e.g. 'mc' for MCA (amal.25pmc114), 'bc' for BCA (santhosh.25ubc214)
    """
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='courses')
    name = models.CharField(max_length=150)           # 'Master of Computer Applications'
    abbreviation = models.CharField(max_length=20)    # 'MCA'
    email_code = models.CharField(max_length=10)      # 'mc' — matches chars 3-4 in email code part
    is_multi_batch = models.BooleanField(default=False)  # True = multiple sections A/B/C
    duration_years = models.IntegerField(default=2)   # 2 for MCA, 3 for BCA
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['department', 'email_code'],
                name='unique_department_email_code'
            ),
            models.CheckConstraint(
                condition=models.Q(duration_years__gte=1) & models.Q(duration_years__lte=6),
                name='check_course_duration_years_range'
            ),
        ]

    def __str__(self):
        return f"{self.abbreviation} ({self.department.code})"

class Class(models.Model):
    name = models.CharField(max_length=100, unique=True) # e.g. BCA A, BSc CS B, II MCA
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='classes')
    # Link to the structured Course (null for legacy/manually created classes)
    course = models.ForeignKey('Course', on_delete=models.SET_NULL, null=True, blank=True, related_name='classes')
    year_number = models.IntegerField(null=True, blank=True)   # 1=I, 2=II, 3=III
    section = models.CharField(max_length=5, blank=True, default='')  # 'A', 'B', '' for single-batch
    batch_start_year = models.IntegerField(null=True, blank=True)  # e.g. 2025
    class_teacher = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='advisor_classes')
    dqc_member = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='rep_classes')
    # Mark moderation fields
    num_students = models.IntegerField(default=0)       # N — total students in this class
    negative_points = models.FloatField(default=0.0)    # P — penalty points for this class
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Classes"
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'year_number', 'section'],
                condition=models.Q(course__isnull=False) & ~models.Q(section=''),
                name='unique_course_year_section'
            ),
            models.CheckConstraint(
                condition=models.Q(num_students__gte=0),
                name='check_class_num_students_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(negative_points__gte=0.0),
                name='check_class_negative_points_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(year_number__isnull=True) | (models.Q(year_number__gte=1) & models.Q(year_number__lte=6)),
                name='check_class_year_number_range'
            ),
            models.CheckConstraint(
                condition=models.Q(batch_start_year__isnull=True) | (models.Q(batch_start_year__gte=1990) & models.Q(batch_start_year__lte=2100)),
                name='check_class_batch_start_year_range'
            ),
        ]

    def __str__(self):
        return self.name

class User(AbstractUser):
    ROLE_CHOICES = [
        ("student", "Student"),
        ("faculty", "Faculty"),
        ("evaluation", "Evaluation Team"),
        ("iqac", "IQAC"),
        ("admin", "Admin"),
    ]

    google_id = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    class_name = models.ForeignKey(
        Class,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    # Student-specific parsed fields from email
    roll_number = models.IntegerField(null=True, blank=True)   # e.g. 14 (from 114 → strip series prefix)
    batch_year = models.IntegerField(null=True, blank=True)    # e.g. 2025

    # Use email as the username field for authentication
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=['student', 'faculty', 'evaluation', 'iqac', 'admin']),
                name='check_user_role_valid'
            ),
            models.CheckConstraint(
                condition=models.Q(roll_number__isnull=True) | models.Q(roll_number__gte=0),
                name='check_user_roll_number_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(batch_year__isnull=True) | (models.Q(batch_year__gte=1990) & models.Q(batch_year__lte=2100)),
                name='check_user_batch_year_range'
            ),
        ]

    def __str__(self):
        return f"{self.email} - {self.get_role_display()}"

class Submission(models.Model):
    STATUS_CHOICES = [
        ('Approved', 'Approved'),
        ('Pending', 'Pending'),
        ('Pending Rep Verification', 'Pending Rep Verification'),
        ('Student Rep Verified', 'Student Rep Verified'),
        ('Teacher Verified', 'Teacher Verified'),
        ('Correction Requested', 'Correction Requested'),
        ('Rejected', 'Rejected'),
        ('Draft', 'Draft'),
        ('Submitted', 'Submitted'),
        ('Verified', 'Verified'),
        ('Evaluated', 'Evaluated'),
        ('Locked', 'Locked'),
        ('Correction', 'Correction'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='submissions')
    criteria_id = models.IntegerField()
    criteria_version = models.ForeignKey('CriteriaVersion', on_delete=models.SET_NULL, null=True, blank=True, related_name='submissions')
    academic_year = models.CharField(max_length=50, blank=True, null=True)
    submission_type = models.CharField(max_length=50, blank=True, null=True) # e.g. 'Sem Result', 'SAVE Sem Result'
    description = models.TextField()
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Draft')
    remarks = models.TextField(blank=True, null=True)
    marks = models.IntegerField(blank=True, null=True)
    proof = models.CharField(max_length=255, blank=True, null=True)
    proof_hash = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    certificate_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    event_id = models.CharField(max_length=100, blank=True, null=True)
    start_date = models.CharField(max_length=50, blank=True, null=True)
    end_date = models.CharField(max_length=50, blank=True, null=True)
    evaluator_verified = models.BooleanField(default=False)
    evidence = models.JSONField(blank=True, null=True)
    verified_by_name = models.CharField(max_length=255, blank=True, null=True)
    rep_verified_by_name = models.CharField(max_length=255, blank=True, null=True)
    rep_remarks = models.TextField(blank=True, null=True)
    teacher_verified_by_name = models.CharField(max_length=255, blank=True, null=True)
    teacher_remarks = models.TextField(blank=True, null=True)
    evaluator_verified_by_name = models.CharField(max_length=255, blank=True, null=True)
    evaluator_remarks = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status'], name='idx_sub_user_status'),
            models.Index(fields=['academic_year', 'status'], name='idx_sub_year_status'),
            models.Index(fields=['status', 'academic_year'], name='idx_sub_status_year'),
            models.Index(fields=['criteria_id', 'status'], name='idx_sub_criteria_status'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=[c[0] for c in SUBMISSION_STATUS_CHOICES]),
                name='check_submission_status_valid'
            ),
            models.UniqueConstraint(
                fields=['user', 'certificate_id'],
                condition=models.Q(certificate_id__isnull=False) & ~models.Q(certificate_id='') & ~models.Q(status='Rejected'),
                name='unique_active_user_certificate'
            ),
            models.UniqueConstraint(
                fields=['user', 'proof_hash'],
                condition=models.Q(proof_hash__isnull=False) & ~models.Q(proof_hash='') & ~models.Q(status='Rejected'),
                name='unique_active_user_proof_hash'
            ),
        ]

    def __str__(self):
        return f"Submission {self.id} - {self.user.email} - {self.status}"


class CriteriaVersion(models.Model):
    academic_year = models.CharField(max_length=20)  # e.g. '2025-2026'
    version = models.IntegerField(default=1)
    name = models.CharField(max_length=100, blank=True, default='')  # e.g. '2025-2026 Official v1'
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    is_locked = models.BooleanField(default=False)

    class Meta:
        ordering = ['academic_year', '-version']
        constraints = [
            models.UniqueConstraint(
                fields=['academic_year', 'version'],
                name='unique_academic_year_version'
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name='check_criteria_version_positive'
            ),
        ]

    def __str__(self):
        status = "Locked" if self.is_locked else "Active"
        return f"{self.academic_year} v{self.version} ({status})"


class CriteriaCategory(models.Model):
    code = models.CharField(max_length=50, unique=True) # e.g. 'cat-academics'
    category = models.CharField(max_length=100)
    access_level = models.CharField(max_length=20, default='all_students') # 'all_students', 'student_rep_only'
    evaluators = models.JSONField(default=list, blank=True) # list of evaluator emails
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.category


class CriteriaItem(models.Model):
    category = models.ForeignKey(CriteriaCategory, on_delete=models.CASCADE, related_name='items')
    version = models.ForeignKey(CriteriaVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name='items')
    title = models.CharField(max_length=255)
    access_level = models.CharField(max_length=20, default='all_students')
    type = models.CharField(max_length=20) # 'count', 'fixed', 'range', 'negative', 'academic_grades', 'date'
    marks = models.FloatField(default=0.0)
    rules_json = models.JSONField(blank=True, null=True) # Deprecated flexible metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(type__in=['count', 'fixed', 'range', 'negative', 'academic_grades', 'date']),
                name='check_criteria_item_type_valid'
            ),
        ]

    def get_authoritative_rule(self):
        """Retrieve the primary authoritative CriteriaRule for this item."""
        rule = self.rules.first()
        if not rule:
            rule, _ = CriteriaRule.objects.get_or_create(
                item=self,
                defaults={
                    'rule_type': self.type,
                    'maximum_marks': self.marks,
                    'min_count': 1 if self.type == 'count' else None,
                    'is_negative': self.type == 'negative',
                    'extra_config': self.rules_json
                }
            )
        return rule

    def __str__(self):
        return f"{self.category.category} - {self.title}"


class CriteriaRule(models.Model):
    item = models.ForeignKey(CriteriaItem, on_delete=models.CASCADE, related_name='rules')
    rule_type = models.CharField(max_length=50, default='standard') # e.g. count, range, fixed, negative, multiplier, date
    maximum_marks = models.FloatField(blank=True, null=True)
    min_count = models.IntegerField(blank=True, null=True)
    max_count = models.IntegerField(blank=True, null=True)
    is_negative = models.BooleanField(default=False)
    multiplier = models.FloatField(default=1.0)
    extra_config = models.JSONField(blank=True, null=True) # Flexible JSON metadata fallback
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(multiplier__gte=0.0),
                name='check_criteria_rule_multiplier_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(min_count__isnull=True) | models.Q(min_count__gte=0),
                name='check_criteria_rule_min_count_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(max_count__isnull=True) | models.Q(max_count__gte=0),
                name='check_criteria_rule_max_count_non_negative'
            ),
        ]

    def __str__(self):
        return f"Rule for {self.item.title} (Max Marks: {self.maximum_marks})"



class AcademicGradeBreakdown(models.Model):
    submission = models.OneToOneField(Submission, on_delete=models.CASCADE, related_name='grade_breakdown')
    s_grade_count = models.IntegerField(default=0)
    a_plus_grade_count = models.IntegerField(default=0)
    a_grade_count = models.IntegerField(default=0)
    other_pass_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    class_pass_percentage = models.FloatField(default=0.0)
    total_students = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(s_grade_count__gte=0) &
                    models.Q(a_plus_grade_count__gte=0) &
                    models.Q(a_grade_count__gte=0) &
                    models.Q(other_pass_count__gte=0) &
                    models.Q(failed_count__gte=0)
                ),
                name='check_grade_counts_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(total_students__gte=0),
                name='check_grade_total_students_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(class_pass_percentage__gte=0.0) & models.Q(class_pass_percentage__lte=100.0),
                name='check_grade_pass_percentage_range'
            ),
        ]

    def save(self, *args, **kwargs):
        honors_sum = self.s_grade_count + self.a_plus_grade_count + self.a_grade_count
        passed = max(0, self.total_students - self.failed_count)

        # Automatically account for other passing students (B/C/D/Pass) if not explicitly set
        if self.other_pass_count <= 0 and passed > honors_sum:
            self.other_pass_count = passed - honors_sum

        total_accounted = honors_sum + self.other_pass_count + self.failed_count
        if self.total_students <= 0:
            self.total_students = max(1, total_accounted)

        if total_accounted != self.total_students:
            raise ValueError(
                f"Sum of grade counts ({total_accounted} accounted: {self.s_grade_count} S + "
                f"{self.a_plus_grade_count} A+ + {self.a_grade_count} A + "
                f"{self.other_pass_count} Other Passing + {self.failed_count} Fail) "
                f"must strictly equal total students ({self.total_students})."
            )

        passed = max(0, self.total_students - self.failed_count)
        self.class_pass_percentage = round((passed / float(self.total_students)) * 100.0, 2)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Grade Breakdown for Submission #{self.submission_id}"


class WorkflowAuditTrail(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name='audit_logs')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    stage = models.IntegerField() # 1 to 7
    stage_name = models.CharField(max_length=100)
    previous_status = models.CharField(max_length=50)
    new_status = models.CharField(max_length=50)
    comments = models.TextField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    request_id = models.CharField(max_length=100, null=True, blank=True)
    previous_hash = models.CharField(max_length=64, null=True, blank=True)
    record_hash = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['submission', 'created_at'], name='idx_audit_sub_created'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stage__gte=1) & models.Q(stage__lte=7),
                name='check_audit_stage_range'
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("WorkflowAuditTrail records are immutable and cannot be updated.")
        last_record = WorkflowAuditTrail.objects.filter(submission=self.submission).order_by('-id').first()
        prev_h = last_record.record_hash if (last_record and last_record.record_hash) else ("0" * 64)
        self.previous_hash = prev_h
        data_to_hash = f"{prev_h}:{self.submission_id}:{self.actor_id}:{self.previous_status}:{self.new_status}:{self.comments}"
        self.record_hash = hashlib.sha256(data_to_hash.encode('utf-8')).hexdigest()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("WorkflowAuditTrail records are immutable and cannot be deleted.")

    def __str__(self):
        return f"Audit Log #{self.id} - Sub #{self.submission_id} Stage {self.stage}"


class SystemAuditLog(models.Model):
    """
    Immutable, cryptographically chained institutional audit trail.
    Records every sensitive mutation across the platform:
    - Submission creation, updates, verification, rejection, evaluation, score & lock changes
    - Criteria changes (versions, categories, items, rules)
    - Ranking calculation and official publication
    - Administrative configuration and user privilege mutations
    """
    ACTION_CHOICES = [
        ('SUBMISSION_CREATE', 'Submission Created'),
        ('SUBMISSION_UPDATE', 'Submission Updated'),
        ('SUBMISSION_VERIFY', 'Submission Verified'),
        ('SUBMISSION_REJECT', 'Submission Rejected'),
        ('SUBMISSION_EVALUATE', 'Submission Evaluated'),
        ('SCORE_CHANGE', 'Score Modified'),
        ('SUBMISSION_LOCK', 'Submission Locked'),
        ('SUBMISSION_UNLOCK', 'Submission Unlocked'),
        ('CRITERIA_CHANGE', 'Criteria Modified'),
        ('RANKING_CALCULATE', 'Ranking Calculated'),
        ('RANKING_PUBLISH', 'Ranking Published / Locked'),
        ('ADMIN_SETTING_CHANGE', 'System Setting Modified'),
        ('USER_ROLE_CHANGE', 'User Role Modified'),
        ('USER_GROUP_CHANGE', 'User Group Modified'),
        ('EVIDENCE_UPLOAD', 'Evidence Uploaded'),
        ('EVIDENCE_DELETE', 'Evidence Deleted'),
        ('CLASS_CHANGE', 'Class Modified'),
    ]

    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='system_audit_logs')
    actor_email = models.CharField(max_length=255, blank=True, default='')
    actor_role = models.CharField(max_length=50, blank=True, default='')
    action = models.CharField(max_length=50, db_index=True)
    object_type = models.CharField(max_length=50, db_index=True)
    object_id = models.CharField(max_length=100, db_index=True)
    object_repr = models.CharField(max_length=255, blank=True, default='')
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    request_id = models.CharField(max_length=100, null=True, blank=True)
    previous_hash = models.CharField(max_length=64, null=True, blank=True)
    record_hash = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['object_type', 'object_id', 'created_at'], name='idx_audit_obj_created'),
            models.Index(fields=['action', 'created_at'], name='idx_audit_act_created'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("SystemAuditLog records are strictly immutable and cannot be updated.")

        if self.actor:
            if not self.actor_email:
                self.actor_email = getattr(self.actor, 'email', '') or ''
            if not self.actor_role:
                self.actor_role = getattr(self.actor, 'role', '') or ''

        last_record = SystemAuditLog.objects.order_by('-id').first()
        prev_h = last_record.record_hash if (last_record and last_record.record_hash) else ("0" * 64)
        self.previous_hash = prev_h

        raw_payload = f"{prev_h}:{self.actor_id}:{self.action}:{self.object_type}:{self.object_id}:{self.reason}"
        self.record_hash = hashlib.sha256(raw_payload.encode('utf-8')).hexdigest()

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("SystemAuditLog records are strictly immutable and cannot be deleted.")

    def __str__(self):
        return f"[{self.created_at}] {self.action} on {self.object_type}#{self.object_id} by {self.actor_email or 'System'}"


class ClassIndexResult(models.Model):
    class_name = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='index_results')
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT)
    academic_score = models.FloatField(default=0.0)
    co_curricular_score = models.FloatField(default=0.0)
    extra_curricular_score = models.FloatField(default=0.0)
    final_index = models.FloatField(default=0.0)
    rank = models.IntegerField(blank=True, null=True)
    scoring_version = models.CharField(max_length=50, default='v1.0-authoritative')
    is_locked = models.BooleanField(default=False)
    snapshot_data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['academic_year', 'is_locked'], name='idx_cir_year_locked'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['class_name', 'academic_year'],
                name='unique_class_academic_year_result'
            ),
            models.CheckConstraint(
                condition=models.Q(rank__isnull=True) | models.Q(rank__gte=1),
                name='check_class_index_rank_positive'
            ),
            models.CheckConstraint(
                condition=models.Q(final_index__gte=0.0),
                name='check_class_index_final_non_negative'
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(academic_score__gte=0.0) &
                    models.Q(co_curricular_score__gte=0.0) &
                    models.Q(extra_curricular_score__gte=0.0)
                ),
                name='check_class_index_scores_non_negative'
            ),
        ]

    def __str__(self):
        return f"{self.class_name.name} ({self.academic_year.year}) Index: {self.final_index}"


class SystemSetting(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.key}: {self.value}"


class UserGroupModel(models.Model):
    group_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, null=True)
    members = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.group_id})"

class Champion(models.Model):
    year = models.CharField(max_length=20)
    category = models.CharField(max_length=20, default='UG')
    rank = models.IntegerField()
    rankLabel = models.CharField(max_length=50)
    teamName = models.CharField(max_length=100)
    eventName = models.CharField(max_length=255, blank=True, null=True)
    score = models.CharField(max_length=20)
    institution = models.CharField(max_length=100, blank=True, null=True)
    image = models.ImageField(upload_to='champions/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year', 'rank']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rank__gte=1),
                name='check_champion_rank_positive'
            ),
            models.UniqueConstraint(
                fields=['year', 'category', 'rank', 'teamName'],
                name='unique_year_category_rank_team'
            ),
        ]

    def __str__(self):
        return f"{self.year} - Rank {self.rank}: {self.teamName}"


class BugReport(models.Model):
    BUG_TYPE_CHOICES = [
        ('UI', 'UI / Layout'),
        ('Function', 'Functionality / Logic'),
        ('Performance', 'Performance / Speed'),
        ('Other', 'Other'),
    ]

    PRIORITY_CHOICES = [
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High'),
    ]

    title = models.CharField(max_length=255)
    description = models.TextField()
    bug_type = models.CharField(max_length=50, choices=BUG_TYPE_CHOICES, default='UI')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='Medium')
    browser_device = models.CharField(max_length=255, blank=True, default='')
    page_url = models.CharField(max_length=255, blank=True, default='')
    role_category = models.CharField(max_length=100, default='Student') # e.g. Student / Rep, Teacher / Evaluator, General
    reporter_name = models.CharField(max_length=150, blank=True, default='')
    reporter_email = models.CharField(max_length=150, blank=True, default='')
    whatsapp_numbers = models.CharField(max_length=255, blank=True, default='') # Target numbers notified
    screenshot = models.ImageField(upload_to='bug_reports/', blank=True, null=True)
    status = models.CharField(max_length=20, default='Open') # Open, In Progress, Resolved
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(bug_type__in=['UI', 'Function', 'Performance', 'Other']),
                name='check_bug_type_valid'
            ),
            models.CheckConstraint(
                condition=models.Q(priority__in=['Low', 'Medium', 'High']),
                name='check_bug_priority_valid'
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=['Open', 'In Progress', 'Resolved']),
                name='check_bug_status_valid'
            ),
        ]

    def __str__(self):
        return f"[{self.priority}] {self.title} ({self.bug_type}) - {self.status}"
