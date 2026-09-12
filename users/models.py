import hashlib
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import AbstractUser, UserManager
from django.utils import timezone

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
    ("admin", "Admin"),
    ("STUDENT", "Student (Uppercase)"),
    ("STAFF", "Staff (Uppercase)"),
    ("ADMIN", "Admin (Uppercase)"),
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
    ('DQC_PENDING', 'DQC Pending'),
    ('TEACHER_PENDING', 'Teacher Pending'),
    ('EVALUATOR_PENDING', 'Evaluator Pending'),
    ('APPROVED', 'Approved (Canonical)'),
    ('SENT_BACK', 'Sent Back'),
    ('REJECTED', 'Rejected (Canonical)'),
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
    batch_year = models.IntegerField(null=True, blank=True)        # Canonical alias
    class_code = models.CharField(max_length=50, blank=True, default='', db_index=True)
    academic_year = models.CharField(max_length=50, blank=True, default='')
    class_teacher = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='advisor_classes')
    dqc_member = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='rep_classes')
    # Mark moderation fields
    num_students = models.IntegerField(default=0)       # N — total students in this class
    negative_points = models.FloatField(default=0.0)    # P — penalty points for this class
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def department_name(self):
        return self.department.name if self.department else ''

    @property
    def course_name(self):
        return self.course.name if self.course else ''

    def save(self, *args, **kwargs):
        if self.batch_start_year and not self.batch_year:
            self.batch_year = self.batch_start_year
        elif self.batch_year and not self.batch_start_year:
            self.batch_start_year = self.batch_year
        super().save(*args, **kwargs)

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

class CustomUserManager(UserManager):
    def create_user(self, username=None, email=None, password=None, **extra_fields):
        class_id_kwarg = extra_fields.pop('class_id', None)
        assigned_class_id_kwarg = extra_fields.pop('assigned_class_id', None)
        is_class_teacher_kwarg = extra_fields.pop('is_class_teacher', False)
        is_evaluator_kwarg = extra_fields.pop('is_evaluator', False)
        evaluator_category_id_kwarg = extra_fields.pop('evaluator_category_id', None)

        role = extra_fields.get('role', 'student')
        is_student_rep = (role == 'STUDENT_REP' or extra_fields.pop('is_student_rep', False))
        if role == 'STUDENT_REP' or role == 'STUDENT':
            extra_fields['role'] = 'student'
        elif role in ('FACULTY', 'STAFF', 'faculty', 'staff'):
            extra_fields['role'] = 'faculty'
        elif role in ('ADMIN', 'admin'):
            extra_fields['role'] = 'admin'
        elif role in ('EVALUATION', 'evaluation'):
            extra_fields['role'] = 'evaluation'

        if not email and username and '@' in username:
            email = username
        if not username and email:
            username = email

        user = super().create_user(username=username, email=email, password=password, **extra_fields)

        target_class_id = class_id_kwarg or assigned_class_id_kwarg
        target_class = None
        if target_class_id:
            if isinstance(target_class_id, Class):
                target_class = target_class_id
            else:
                target_class = Class.objects.filter(
                    models.Q(name=str(target_class_id)) |
                    models.Q(id__exact=int(target_class_id) if str(target_class_id).isdigit() else -1)
                ).first()
                if not target_class:
                    dept, _ = Department.objects.get_or_create(
                        code='CS',
                        defaults={'name': 'Computer Science', 'level': 'UG'}
                    )
                    course, _ = Course.objects.get_or_create(
                        department=dept,
                        abbreviation='BCA',
                        defaults={'name': 'Bachelor of Computer Applications', 'email_code': 'bc'}
                    )
                    target_class = Class.objects.create(
                        name=str(target_class_id),
                        department=dept,
                        course=course,
                        section='A',
                        year_number=1,
                        academic_year='2025-2026'
                    )
            if class_id_kwarg:
                user.class_name = target_class
                user.save(update_fields=['class_name'])

        if is_student_rep and target_class:
            grp, _ = UserGroupModel.objects.get_or_create(
                group_id='grp-student-reps',
                defaults={'name': 'Student Representatives'}
            )
            UserGroupMember.objects.get_or_create(
                group=grp,
                email=user.email,
                defaults={'assigned_class': target_class, 'user': user}
            )
            grp_dqc, _ = UserGroupModel.objects.get_or_create(
                group_id='grp-dqc-student-rep',
                defaults={'name': 'DQC Student Rep Group'}
            )
            UserGroupMember.objects.get_or_create(
                group=grp_dqc,
                email=user.email,
                defaults={'assigned_class': target_class, 'user': user}
            )
            if not target_class.dqc_member:
                target_class.dqc_member = user
                target_class.save(update_fields=['dqc_member'])

        if is_class_teacher_kwarg and target_class:
            if not target_class.class_teacher:
                target_class.class_teacher = user
                target_class.save(update_fields=['class_teacher'])
            if not TeacherClassAssignment.objects.filter(class_obj=target_class).exists():
                TeacherClassAssignment.objects.get_or_create(
                    teacher=user,
                    class_obj=target_class,
                    academic_year=2025,
                    defaults={'is_active': True}
                )
            grp_ct, _ = UserGroupModel.objects.get_or_create(
                group_id='grp-class-teachers',
                defaults={'name': 'Class Teachers Council'}
            )
            UserGroupMember.objects.get_or_create(
                group=grp_ct,
                email=user.email,
                defaults={'assigned_class': target_class, 'user': user}
            )

        if is_evaluator_kwarg:
            grp_ec, _ = UserGroupModel.objects.get_or_create(
                group_id='grp-evaluation-committee',
                defaults={'name': 'Evaluation Committee'}
            )
            UserGroupMember.objects.get_or_create(
                group=grp_ec,
                email=user.email,
                defaults={'user': user}
            )
            if evaluator_category_id_kwarg:
                cat_id = int(evaluator_category_id_kwarg)
                crit_cat = CriteriaCategory.objects.filter(id=cat_id).first()
                if not crit_cat:
                    c_obj = Category.objects.filter(id=cat_id).first()
                    code = c_obj.code if c_obj else f"cat-{cat_id}"
                    name = c_obj.name if c_obj else f"Category {cat_id}"
                    crit_cat, _ = CriteriaCategory.objects.get_or_create(
                        code=code,
                        defaults={'category': name, 'evaluators': [user.email]}
                    )
                if user.email not in (crit_cat.evaluators or []):
                    evals = list(crit_cat.evaluators or [])
                    evals.append(user.email)
                    crit_cat.evaluators = evals
                    crit_cat.save(update_fields=['evaluators'])
                EvaluatorCategoryAssignment.objects.get_or_create(
                    category=crit_cat,
                    evaluator=user,
                    defaults={'academic_year': 2025}
                )

        return user

class User(AbstractUser):
    objects = CustomUserManager()
    ROLE_CHOICES = USER_ROLE_CHOICES

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

    @property
    def full_name(self):
        fn = f"{self.first_name} {self.last_name}".strip()
        return fn or self.username or self.email

    @property
    def class_id(self):
        return self.class_name_id

    @property
    def user_groups(self):
        from users.services.user_service import UserService
        return UserService.get_user_group_names(self)

    def save(self, *args, **kwargs):
        if not self.username and self.email:
            self.username = self.email
        # Normalize uppercase roles to canonical lowercase
        if self.role in ('STUDENT', 'student'):
            self.role = 'student'
        elif self.role in ('STAFF', 'FACULTY', 'faculty', 'staff'):
            self.role = 'faculty'
        elif self.role in ('ADMIN', 'admin'):
            self.role = 'admin'
        elif self.role in ('EVALUATION', 'evaluation'):
            self.role = 'evaluation'
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=['student', 'faculty', 'evaluation', 'admin', 'STUDENT', 'STAFF', 'ADMIN']),
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

class SubmissionManager(models.Manager):
    def create(self, **kwargs):
        cat = kwargs.get('category')
        if cat is not None:
            from users.models import CriteriaCategory
            if not isinstance(cat, CriteriaCategory):
                crit = getattr(cat, 'criteria_category', None)
                if not crit:
                    crit = CriteriaCategory.objects.filter(code=getattr(cat, 'code', '')).first() or \
                           CriteriaCategory.objects.filter(category__iexact=getattr(cat, 'name', '')).first()
                if crit:
                    kwargs['category'] = crit
                else:
                    kwargs['category'] = None
        return super().create(**kwargs)


class Submission(models.Model):
    objects = SubmissionManager()
    STATUS_CHOICES = SUBMISSION_STATUS_CHOICES

    def __init__(self, *args, **kwargs):
        cat = kwargs.get('category')
        if cat is not None:
            from users.models import CriteriaCategory
            if not isinstance(cat, CriteriaCategory):
                crit = getattr(cat, 'criteria_category', None)
                if not crit:
                    crit = CriteriaCategory.objects.filter(code=getattr(cat, 'code', '')).first() or \
                           CriteriaCategory.objects.filter(category__iexact=getattr(cat, 'name', '')).first()
                if crit:
                    kwargs['category'] = crit
                else:
                    kwargs['category'] = None
        super().__init__(*args, **kwargs)

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='submissions')
    class_obj = models.ForeignKey(Class, on_delete=models.SET_NULL, null=True, blank=True, related_name='submissions')
    category = models.ForeignKey('CriteriaCategory', on_delete=models.SET_NULL, null=True, blank=True, related_name='submissions')
    criteria_id = models.IntegerField()
    subcategory_id = models.IntegerField(null=True, blank=True)
    criteria_version = models.ForeignKey('CriteriaVersion', on_delete=models.SET_NULL, null=True, blank=True, related_name='submissions')
    academic_year = models.CharField(max_length=50, blank=True, null=True)
    submission_type = models.CharField(max_length=50, blank=True, null=True) # e.g. 'Sem Result', 'SAVE Sem Result'
    description = models.TextField()
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Draft')
    remarks = models.TextField(blank=True, null=True)
    marks = models.IntegerField(blank=True, null=True)
    calculated_marks = models.FloatField(null=True, blank=True)
    is_manual_eval = models.BooleanField(default=False)
    proof = models.CharField(max_length=500, blank=True, null=True)
    proof_url = models.CharField(max_length=500, blank=True, null=True)
    proof_hash = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    certificate_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    event_id = models.CharField(max_length=100, blank=True, null=True)
    start_date = models.CharField(max_length=50, blank=True, null=True)
    end_date = models.CharField(max_length=50, blank=True, null=True)
    submission_date = models.DateTimeField(null=True, blank=True)
    evaluator_verified = models.BooleanField(default=False)
    evidence = models.JSONField(blank=True, null=True)
    submission_metadata = models.JSONField(blank=True, null=True)
    verified_by_name = models.CharField(max_length=255, blank=True, null=True)
    rep_verified_by_name = models.CharField(max_length=255, blank=True, null=True)
    rep_remarks = models.TextField(blank=True, null=True)
    teacher_verified_by_name = models.CharField(max_length=255, blank=True, null=True)
    teacher_remarks = models.TextField(blank=True, null=True)
    evaluator_verified_by_name = models.CharField(max_length=255, blank=True, null=True)
    evaluator_remarks = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def student_id(self):
        return self.user_id

    @property
    def class_id(self):
        return self.class_obj_id or (self.user.class_name_id if self.user else None)

    @property
    def metadata(self):
        meta = self.submission_metadata or self.evidence or {}
        if not isinstance(meta, dict):
            return {}
        meta_dict = dict(meta)
        if 'grades' in meta_dict and isinstance(meta_dict['grades'], dict):
            g = meta_dict['grades']
            if 'count_90_above' not in meta_dict:
                meta_dict['count_90_above'] = g.get('count_90_above', g.get('count90Above', g.get('90_above', g.get('S', g.get('s_grade_count', 0)))))
            if 'count_80_90' not in meta_dict:
                meta_dict['count_80_90'] = g.get('count_80_90', g.get('count80to90', g.get('80_90', g.get('APlus', g.get('a_plus_grade_count', 0)))))
            if 'count_70_80' not in meta_dict:
                meta_dict['count_70_80'] = g.get('count_70_80', g.get('count70to80', g.get('70_80', g.get('A', g.get('a_grade_count', 0)))))
            if 'count_fail' not in meta_dict:
                meta_dict['count_fail'] = g.get('count_fail', g.get('failCount', g.get('failed_count', g.get('Fail', 0))))
            if 'pass_percentage' not in meta_dict:
                tot = int(meta_dict.get('totalStudents', meta_dict.get('total_students', 0)) or 0)
                if tot > 0:
                    fails = int(meta_dict.get('count_fail', 0) or 0)
                    meta_dict['pass_percentage'] = round(((tot - fails) / float(tot)) * 100.0, 2)
        return meta_dict

    @property
    def subcategory(self):
        if self.subcategory_id:
            sub = SubCategory.objects.filter(id=self.subcategory_id).first()
            if sub:
                return sub
        c_item = None
        if getattr(self, 'criteria_id', None):
            c_item = CriteriaItem.objects.filter(pk=self.criteria_id).select_related('category').first()
        return SubCategory.find_subcategory(
            subcategory_id=getattr(self, 'subcategory_id', None),
            category_id=getattr(self.category, 'id', None) if self.category else None,
            category_code=getattr(self.category, 'code', None) if self.category else None,
            criteria_item=c_item,
            evidence=getattr(self, 'evidence', None)
        )

    def save(self, *args, **kwargs):
        from django.utils import timezone
        if not self.submission_date:
            self.submission_date = self.created_at or timezone.now()

        if not self.class_obj and self.user and self.user.class_name:
            self.class_obj = self.user.class_name

        c_item = None
        if self.criteria_id:
            c_item = CriteriaItem.objects.filter(pk=self.criteria_id).select_related('category').first()

        if not self.category and c_item and c_item.category:
            self.category = c_item.category
            if not self.is_manual_eval:
                self.is_manual_eval = getattr(c_item.category, 'is_manual_eval', False) or getattr(c_item, 'is_manual_eval', False)

        # Dynamic Subcategory Lookup & Mark Calculation Engine:
        # For all categories containing subcategories, the calculated marks are strictly
        # driven by subcategories.default_marks.
        subcat = None
        if self.subcategory_id:
            subcat = SubCategory.objects.filter(id=self.subcategory_id).first()

        if not subcat:
            subcat = SubCategory.find_subcategory(
                subcategory_id=self.subcategory_id,
                category_id=getattr(self.category, 'id', None),
                category_code=getattr(self.category, 'code', None),
                criteria_item=c_item,
                evidence=self.evidence
            )
            if subcat:
                self.subcategory_id = subcat.id

        if self.proof and not self.proof_url:
            self.proof_url = self.proof
        elif self.proof_url and not self.proof:
            self.proof = self.proof_url

        if self.evidence and not self.submission_metadata:
            self.submission_metadata = self.evidence
        elif self.submission_metadata and not self.evidence:
            self.evidence = self.submission_metadata

        # Round 3 Finality Guard:
        # Intermediate verification rounds (DQC_PENDING, TEACHER_PENDING, EVALUATOR_PENDING,
        # SENT_BACK, Draft, etc.) strictly function as check-and-forward steps and never credit marks.
        # ONLY Round 3 Evaluator Approval (APPROVED, Evaluated, Locked) credits marks to the record and ledger.
        if self.status in ('Approved', 'APPROVED', 'Evaluated', 'Locked', 'Submitted'):
            if subcat:
                count_val = 1
                ev = self.evidence if isinstance(self.evidence, dict) else {}
                if (c_item and c_item.type == 'count') or 'count' in ev:
                    try:
                        count_val = max(1, int(ev.get('count', 1)))
                    except (ValueError, TypeError):
                        count_val = 1
                calculated_val = float(subcat.default_marks) * count_val
                if self.calculated_marks is None or self.calculated_marks == 0.0:
                    self.calculated_marks = calculated_val
                if self.marks is None or not self.is_manual_eval:
                    self.marks = int(round(self.calculated_marks))
            elif self.calculated_marks is not None and self.marks is None:
                self.marks = int(round(self.calculated_marks))
            elif self.marks is not None and self.calculated_marks is None:
                self.calculated_marks = float(self.marks)
        else:
            self.calculated_marks = 0.0
            self.marks = None

        super().save(*args, **kwargs)

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


class CategoryManager(models.Manager):
    def create(self, **kwargs):
        if 'id' in kwargs and self.filter(id=kwargs['id']).exists():
            obj = self.get(id=kwargs['id'])
            for k, v in kwargs.items():
                setattr(obj, k, v)
            obj.save()
            return obj
        return super().create(**kwargs)


class Category(models.Model):
    objects = CategoryManager()
    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = 'categories'
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'

    def save(self, *args, **kwargs):
        if not self.code:
            import re
            slug = re.sub(r'[^a-z0-9]+', '-', (self.name or '').lower()).strip('-')
            self.code = f"cat-{slug}" if not slug.startswith('cat-') else slug
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.id} - {self.name} ({self.code})"


class SubCategoryManager(models.Manager):
    def create(self, **kwargs):
        if 'id' in kwargs and self.filter(id=kwargs['id']).exists():
            obj = self.get(id=kwargs['id'])
            for k, v in kwargs.items():
                setattr(obj, k, v)
            obj.save()
            return obj
        return super().create(**kwargs)


class SubCategory(models.Model):
    objects = SubCategoryManager()
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='subcategories', db_column='category_id')
    subcategory_name = models.CharField(max_length=255)
    default_marks = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    requires_dqc = models.BooleanField(default=False)
    max_per_cycle = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'subcategories'
        verbose_name = 'Subcategory'
        verbose_name_plural = 'Subcategories'
        ordering = ['category_id', 'id']

    def __str__(self):
        return f"{self.subcategory_name} ({self.default_marks} marks)"

    @classmethod
    def find_subcategory(cls, subcategory_id=None, category_id=None, category_code=None, criteria_item=None, evidence=None):
        """
        Authoritative resolver that finds the exact SubCategory record.
        Handles direct subcategory_id, criteria_item, and evidence payloads.
        """
        # 1. Direct ID lookup
        if subcategory_id:
            try:
                sub = cls.objects.filter(pk=int(subcategory_id)).first()
                if sub:
                    return sub
            except (ValueError, TypeError):
                pass

        # 2. Resolve Category Number (1-12)
        code_map = {
            'cat-academics': 1,
            'cat-online-courses': 2,
            'cat-competitive-exams': 3,
            'cat-internships': 4,
            'cat-scholarships': 5,
            'cat-research': 6,
            'cat-startups': 7,
            'cat-prizes': 8,
            'cat-programs-organized': 9,
            'cat-leadership': 10,
            'cat-leaderships': 10,
            'cat-social-responsibility': 11,
            'cat-social-responsibilities': 11,
            'cat-career-advancement': 12,
        }

        target_cat_num = None

        # A. Resolve by canonical category code
        raw_code = (category_code or '').strip().lower()
        if not raw_code and criteria_item and getattr(criteria_item, 'category', None):
            raw_code = (getattr(criteria_item.category, 'code', '') or '').strip().lower()

        if raw_code and raw_code in code_map:
            target_cat_num = code_map[raw_code]

        # B. Resolve via CriteriaCategory database lookup if category_id passed
        if not target_cat_num and category_id is not None:
            try:
                crit_c = CriteriaCategory.objects.filter(pk=int(category_id)).first()
                if crit_c and crit_c.code and crit_c.code.strip().lower() in code_map:
                    target_cat_num = code_map[crit_c.code.strip().lower()]
            except Exception:
                pass

        # C. Resolve via direct Category table ID
        if not target_cat_num and category_id is not None:
            try:
                cid_int = int(category_id)
                if 106 <= cid_int <= 118:
                    target_cat_num = cid_int - 105
                elif 1 <= cid_int <= 12 and Category.objects.filter(id=cid_int).exists():
                    target_cat_num = cid_int
            except (ValueError, TypeError):
                pass

        # D. Fallback via category display name
        if not target_cat_num and criteria_item and getattr(criteria_item, 'category', None):
            cat_display = (getattr(criteria_item.category, 'category', '') or '').strip().lower()
            for code_sub, num in [
                ('academic', 1), ('online course', 2), ('competitive', 3),
                ('internship', 4), ('scholarship', 5), ('research', 6),
                ('startup', 7), ('prize', 8), ('program', 9),
                ('leadership', 10), ('social', 11), ('career', 12)
            ]:
                if code_sub in cat_display:
                    target_cat_num = num
                    break

        if not target_cat_num:
            return None

        # Categories without subcategories (Cat 1: Academics, Cat 12: Career Advancement)
        if target_cat_num in (1, 12):
            return None

        # Extract submitted subItem text
        ev = evidence if isinstance(evidence, dict) else {}
        sub_name = (
            ev.get('subItem') or
            ev.get('researchSubItem') or
            ev.get('prizesSubItem') or
            ev.get('subcategory_name') or
            ev.get('sub_item') or
            (criteria_item.title if criteria_item else '') or
            ''
        )
        sub_name_str = str(sub_name).strip()
        item_title_str = str(getattr(criteria_item, 'title', '') or '').strip()

        qs = cls.objects.filter(category_id=target_cat_num)

        # A. Exact match by sub_name or item_title
        if sub_name_str:
            matched = qs.filter(subcategory_name__iexact=sub_name_str).first()
            if matched:
                return matched
        if item_title_str:
            matched = qs.filter(subcategory_name__iexact=item_title_str).first()
            if matched:
                return matched

        # B. Category 8: Prizes special disambiguation (Marian vs Outside, Individual vs Group)
        if target_cat_num == 8:
            is_outside = 'outside' in item_title_str.lower() or 'outside' in sub_name_str.lower()
            scope = 'Outside' if is_outside else 'Marian'
            scope_qs = qs.filter(subcategory_name__icontains=scope)

            is_group = 'group' in sub_name_str.lower() or 'group' in item_title_str.lower()
            is_part = 'participat' in sub_name_str.lower() or 'participat' in item_title_str.lower()

            for cand in scope_qs:
                c_name = cand.subcategory_name.lower()
                if is_part and 'participat' in c_name:
                    if is_group and 'group' in c_name:
                        return cand
                    if not is_group and 'individual' in c_name:
                        return cand
                elif not is_part:
                    if '1st' in sub_name_str.lower() and '1st' in c_name and ((is_group and 'group' in c_name) or (not is_group and 'individual' in c_name)):
                        return cand
                    if '2nd' in sub_name_str.lower() and '2nd' in c_name and ((is_group and 'group' in c_name) or (not is_group and 'individual' in c_name)):
                        return cand
                    if '3rd' in sub_name_str.lower() and '3rd' in c_name and ((is_group and 'group' in c_name) or (not is_group and 'individual' in c_name)):
                        return cand

        # C. Category 3: Competitive Exams matching
        if target_cat_num == 3:
            combined = f"{sub_name_str} {item_title_str}".lower()
            if 'jrf' in combined:
                return qs.filter(subcategory_name__icontains='JRF').first()
            if 'net' in combined:
                return qs.filter(subcategory_name__icontains='NET').first()
            if 'upsc' in combined or 'psc' in combined or 'participation' in combined:
                return qs.filter(subcategory_name__icontains='Participation').first()
            if 'ielts' in combined or 'language' in combined or 'other' in combined:
                return qs.filter(subcategory_name__icontains='Any Other').first()

        # D. Category 2: Online Courses matching
        if target_cat_num == 2:
            combined = f"{sub_name_str} {item_title_str}".lower()
            if 'swayam' in combined or 'nptel' in combined:
                return qs.filter(subcategory_name__icontains='Swayam').first()
            if 'mooc' in combined:
                return qs.filter(subcategory_name__icontains='MOOC').first()

        # E. Substring matching
        for cand in qs:
            c_name = cand.subcategory_name.lower()
            if sub_name_str and (c_name in sub_name_str.lower() or sub_name_str.lower() in c_name):
                return cand
            if item_title_str and (c_name in item_title_str.lower() or item_title_str.lower() in c_name):
                return cand

        return None


class CriteriaCategory(models.Model):
    code = models.CharField(max_length=50, unique=True) # e.g. 'cat-academics'
    category = models.CharField(max_length=100)
    access_level = models.CharField(max_length=20, default='all_students') # 'all_students', 'dqc_only', 'student_rep_only'
    is_manual_eval = models.BooleanField(default=False)
    evaluators = models.JSONField(default=list, blank=True) # list of evaluator emails
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.category


class CriteriaItem(models.Model):
    category = models.ForeignKey(CriteriaCategory, on_delete=models.CASCADE, related_name='items')
    version = models.ForeignKey(CriteriaVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name='items')
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=20) # 'count', 'fixed', 'range', 'negative', 'academic_grades', 'date'
    marks = models.FloatField(default=0.0)
    access_level = models.CharField(max_length=50, default='all_students') # 'all_students', 'dqc_only'
    is_manual_eval = models.BooleanField(default=False)
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

    def get_access_level(self) -> str:
        """
        Return the effective access_level for this CriteriaItem.

        Priority:
          1. Item's own access_level if it is non-empty — always authoritative.
             (Migration 0030 explicitly sets this on every row; trust it unconditionally.)
          2. Parent CriteriaCategory.access_level as fallback when item field is empty.
          3. 'all_students' as the final default.

        Access level values: 'all_students', 'dqc_only', 'student_rep_only'.
        """
        item_level = (self.access_level or '').strip()
        if item_level:
            # Item has an explicit access level — always authoritative regardless of value.
            return item_level
        # Item field is empty — defer to parent category.
        cat_level = ''
        if self.category_id:
            cat_level = (getattr(self.category, 'access_level', '') or '').strip()
        return cat_level if cat_level else 'all_students'


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

class ClassLedger(models.Model):
    class_id = models.CharField(max_length=100, unique=True)
    total_marks = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'class_ledgers'
        verbose_name = 'Class Ledger'
        verbose_name_plural = 'Class Ledgers'

    def __str__(self):
        return f"ClassLedger({self.class_id}: {self.total_marks})"


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

    @property
    def group_name(self):
        return self.name

    def __str__(self):
        return f"{self.name} ({self.group_id})"

    def sync_json_members(self):
        self.members = list(self.memberships.values_list('email', flat=True))
        self.save(update_fields=['members'])


class UserGroupMember(models.Model):
    group = models.ForeignKey(UserGroupModel, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='group_memberships')
    email = models.EmailField(max_length=255)
    name = models.CharField(max_length=255, blank=True, default='')
    department = models.ForeignKey('Department', on_delete=models.SET_NULL, null=True, blank=True)
    assigned_class = models.ForeignKey('Class', on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_group_members')
    badge = models.CharField(max_length=50, blank=True, default='') # 'DQC member', 'Student Rep'
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['group', 'email'], name='unique_group_member_email')
        ]
        ordering = ['id']

    def __str__(self):
        return f"{self.email} ({self.group.name})"


class StaffProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_profile', db_column='user_id')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='staff_profiles', db_column='department_id')
    designation = models.CharField(max_length=100, blank=True, default='')

    class Meta:
        db_table = 'staff_profiles'
        verbose_name = 'Staff Profile'
        verbose_name_plural = 'Staff Profiles'

    def __str__(self):
        return f"{self.user.email} - {self.designation}"


class TeacherClassAssignment(models.Model):
    teacher = models.ForeignKey(User, on_delete=models.CASCADE, related_name='teacher_class_assignments', db_column='teacher_id')
    class_obj = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='teacher_assignments', db_column='class_id')
    academic_year = models.IntegerField(default=2025)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_teacher_classes', db_column='assigned_by')
    created_at = models.DateTimeField(default=timezone.now)
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'teacher_class_assignments'
        constraints = [
            models.UniqueConstraint(fields=['teacher', 'academic_year'], name='unique_single_teacher_per_year'),
            models.UniqueConstraint(fields=['class_obj'], name='unique_single_class_teacher'),
        ]
        ordering = ['-assigned_at']

    def __str__(self):
        return f"{self.teacher.email} -> {self.class_obj.name} ({self.academic_year})"


class EvaluatorCategoryAssignment(models.Model):
    member = models.ForeignKey(UserGroupMember, on_delete=models.CASCADE, null=True, blank=True, related_name='category_assignments')
    evaluator = models.ForeignKey('User', on_delete=models.CASCADE, null=True, blank=True, related_name='evaluator_category_assignments', db_column='evaluator_id')
    category = models.ForeignKey('CriteriaCategory', on_delete=models.CASCADE, related_name='evaluator_assignments', db_column='category_id')
    academic_year = models.IntegerField(default=2025)
    assigned_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_evaluator_categories', db_column='assigned_by')
    created_at = models.DateTimeField(default=timezone.now)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'evaluator_category_assignments'
        constraints = [
            models.UniqueConstraint(fields=['evaluator', 'category', 'academic_year'], condition=models.Q(evaluator__isnull=False), name='unique_evaluator_category'),
            models.UniqueConstraint(fields=['member', 'category'], condition=models.Q(member__isnull=False), name='unique_member_category_assignment'),
        ]
        ordering = ['id']

    def __str__(self):
        actor_email = self.evaluator.email if self.evaluator else (self.member.email if self.member else 'Unknown')
        return f"{actor_email} -> {self.category.category} ({self.academic_year})"


class VerificationLog(models.Model):
    VERIFICATION_LEVEL_CHOICES = [
        ('DQC', 'DQC'),
        ('CLASS_TEACHER', 'Class Teacher'),
        ('EVALUATOR', 'Evaluator'),
    ]
    ACTION_CHOICES = [
        ('VERIFY_AND_FORWARD', 'Verify and Forward'),
        ('SEND_BACK', 'Send Back'),
        ('REJECT', 'Reject'),
        ('APPROVE_AND_CREDIT', 'Approve and Credit'),
    ]

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name='verification_logs')
    verification_level = models.CharField(max_length=50, choices=VERIFICATION_LEVEL_CHOICES)
    verifier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, db_column='verifier_id', related_name='verification_logs')
    verifier_name = models.CharField(max_length=255, blank=True, default='')
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    remarks = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)
    action_timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'verification_logs'
        ordering = ['action_timestamp', 'timestamp']

    def __str__(self):
        return f"VerificationLog #{self.id} - Sub #{self.submission_id} [{self.verification_level}] {self.action}"

    @classmethod
    def log_action(cls, submission_id, verification_level, verifier_id, verifier_name, action, remarks=None, action_timestamp=None):
        ts = action_timestamp or timezone.now()
        return cls.objects.create(
            submission_id=submission_id,
            verification_level=verification_level,
            verifier_id=verifier_id,
            verifier_name=verifier_name,
            action=action,
            remarks=remarks or '',
            action_timestamp=ts
        )


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
