import hashlib
from django.db import models
from django.contrib.auth.models import AbstractUser

class AcademicYear(models.Model):
    year = models.CharField(max_length=20, unique=True) # e.g. "2025-2026"
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

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
        unique_together = [('department', 'email_code')]

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

    def __str__(self):
        return f"Submission {self.id} - {self.user.email} - {self.status}"


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
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=20) # 'count', 'fixed', 'range', 'negative', 'academic_grades'
    marks = models.FloatField(default=0.0)
    rules_json = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.category.category} - {self.title}"


class CriteriaRule(models.Model):
    item = models.ForeignKey(CriteriaItem, on_delete=models.CASCADE, related_name='rules')
    rule_type = models.CharField(max_length=50, default='standard') # e.g. count, range, fixed, negative, multiplier
    maximum_marks = models.FloatField(blank=True, null=True)
    min_count = models.IntegerField(blank=True, null=True)
    max_count = models.IntegerField(blank=True, null=True)
    is_negative = models.BooleanField(default=False)
    multiplier = models.FloatField(default=1.0)
    extra_config = models.JSONField(blank=True, null=True) # Flexible JSON metadata fallback
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Rule for {self.item.title} (Max Marks: {self.maximum_marks})"



class AcademicGradeBreakdown(models.Model):
    submission = models.OneToOneField(Submission, on_delete=models.CASCADE, related_name='grade_breakdown')
    s_grade_count = models.IntegerField(default=0)
    a_plus_grade_count = models.IntegerField(default=0)
    a_grade_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    class_pass_percentage = models.FloatField(default=0.0)
    total_students = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        grade_sum = self.s_grade_count + self.a_plus_grade_count + self.a_grade_count + self.failed_count
        if self.total_students <= 0:
            self.total_students = max(1, grade_sum)
        if grade_sum > self.total_students:
            raise ValueError(f"Sum of grade counts ({grade_sum}) exceeds total students ({self.total_students}).")
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


class ClassIndexResult(models.Model):
    class_name = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='index_results')
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE)
    academic_score = models.FloatField(default=0.0)
    co_curricular_score = models.FloatField(default=0.0)
    extra_curricular_score = models.FloatField(default=0.0)
    final_index = models.FloatField(default=0.0)
    rank = models.IntegerField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

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

    def __str__(self):
        return f"{self.year} - Rank {self.rank}: {self.teamName}"
