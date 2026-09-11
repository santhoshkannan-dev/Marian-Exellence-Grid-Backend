from rest_framework import serializers
from .models import (
    Department, Course, AcademicYear, Class, User,
    CriteriaCategory, CriteriaItem, CriteriaRule, CriteriaVersion, Submission,
    AcademicGradeBreakdown, WorkflowAuditTrail, ClassIndexResult,
    Champion, BugReport, SystemAuditLog, VerificationLog,
    TeacherClassAssignment, EvaluatorCategoryAssignment, StaffProfile,
    Category, SubCategory
)


class StaffProfileSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    department_code = serializers.CharField(source='department.code', read_only=True)

    class Meta:
        model = StaffProfile
        fields = ['id', 'user', 'department', 'department_name', 'department_code', 'designation']


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = '__all__'

    def validate_year(self, value):
        import re
        if not re.match(r'^\d{4}-\d{4}$', str(value).strip()):
            raise serializers.ValidationError("Academic year must be in format 'YYYY-YYYY' (e.g. '2025-2026').")
        return str(value).strip()


class CourseSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    department_code = serializers.CharField(source='department.code', read_only=True)

    class Meta:
        model = Course
        fields = [
            'id', 'department', 'department_name', 'department_code',
            'name', 'abbreviation', 'email_code',
            'is_multi_batch', 'duration_years',
            'created_at', 'updated_at'
        ]

    def validate_duration_years(self, value):
        if value < 1 or value > 6:
            raise serializers.ValidationError("Course duration must be between 1 and 6 years.")
        return value


class ClassSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    course_abbreviation = serializers.CharField(source='course.abbreviation', read_only=True)
    course_name = serializers.CharField(source='course.name', read_only=True)
    class_teacher_name = serializers.SerializerMethodField()
    class_teacher_email = serializers.CharField(source='class_teacher.email', read_only=True)
    dqc_member_name = serializers.SerializerMethodField()
    dqc_member_email = serializers.CharField(source='dqc_member.email', read_only=True)

    def get_class_teacher_name(self, obj):
        if obj.class_teacher:
            return obj.class_teacher.get_full_name() or obj.class_teacher.email
        return None

    def get_dqc_member_name(self, obj):
        if obj.dqc_member:
            return obj.dqc_member.get_full_name() or obj.dqc_member.email
        return None

    class Meta:
        model = Class
        fields = [
            'id', 'name', 'class_code', 'batch_year', 'academic_year',
            'department', 'department_name',
            'course', 'course_name', 'course_abbreviation',
            'year_number', 'section', 'batch_start_year',
            'class_teacher', 'class_teacher_name', 'class_teacher_email',
            'dqc_member', 'dqc_member_name', 'dqc_member_email',
            'num_students', 'negative_points',
            'created_at', 'updated_at'
        ]

    def validate_num_students(self, value):
        if value < 0 or value > 1000:
            raise serializers.ValidationError("Number of students must be between 0 and 1000.")
        return value

    def validate_negative_points(self, value):
        if value < 0 or value > 10000:
            raise serializers.ValidationError("Negative points must be between 0 and 10000.")
        return value

    def validate_year_number(self, value):
        if value is not None and (value < 1 or value > 6):
            raise serializers.ValidationError("Year number must be between 1 and 6.")
        return value


class VerificationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationLog
        fields = [
            'id', 'submission', 'verification_level', 'verifier_id',
            'verifier_name', 'action', 'remarks', 'timestamp', 'action_timestamp'
        ]
        read_only_fields = '__all__'


class TeacherClassAssignmentSerializer(serializers.ModelSerializer):
    teacher_email = serializers.CharField(source='teacher.email', read_only=True)
    teacher_name = serializers.CharField(source='teacher.get_full_name', read_only=True)
    class_name = serializers.CharField(source='class_obj.name', read_only=True)
    assigned_by_email = serializers.CharField(source='assigned_by.email', read_only=True)

    class Meta:
        model = TeacherClassAssignment
        fields = [
            'id', 'teacher', 'teacher_email', 'teacher_name',
            'class_obj', 'class_name', 'academic_year',
            'assigned_by', 'assigned_by_email', 'assigned_at', 'created_at', 'is_active'
        ]


class EvaluatorCategoryAssignmentSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.category', read_only=True)
    category_code = serializers.CharField(source='category.code', read_only=True)
    evaluator_email = serializers.SerializerMethodField()
    evaluator_name = serializers.SerializerMethodField()
    assigned_by_email = serializers.CharField(source='assigned_by.email', read_only=True)

    def get_evaluator_email(self, obj):
        if obj.evaluator:
            return obj.evaluator.email
        if obj.member:
            return obj.member.email
        return None

    def get_evaluator_name(self, obj):
        if obj.evaluator:
            return obj.evaluator.get_full_name() or obj.evaluator.username
        if obj.member:
            return obj.member.name or obj.member.email
        return None

    class Meta:
        model = EvaluatorCategoryAssignment
        fields = [
            'id', 'member', 'evaluator', 'evaluator_email', 'evaluator_name',
            'category', 'category_name', 'category_code', 'academic_year',
            'assigned_by', 'assigned_by_email', 'assigned_at', 'created_at'
        ]


class DepartmentSerializer(serializers.ModelSerializer):
    courses = CourseSerializer(many=True, read_only=True)
    classes = ClassSerializer(many=True, read_only=True)

    class Meta:
        model = Department
        fields = [
            'id', 'name', 'code', 'email_prefix', 'level',
            'courses', 'classes',
            'created_at', 'updated_at'
        ]

    def validate_level(self, value):
        if value not in ['UG', 'PG', 'Professional', 'Other']:
            raise serializers.ValidationError("Level must be one of: UG, PG, Professional, Other.")
        return value


class CriteriaVersionSerializer(serializers.ModelSerializer):
    item_count = serializers.IntegerField(source='items.count', read_only=True)

    class Meta:
        model = CriteriaVersion
        fields = ['id', 'academic_year', 'version', 'name', 'created_at', 'published_at', 'is_locked', 'item_count']


class CriteriaRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CriteriaRule
        fields = '__all__'


class CriteriaItemSerializer(serializers.ModelSerializer):
    rules = CriteriaRuleSerializer(many=True, read_only=True)

    class Meta:
        model = CriteriaItem
        fields = ['id', 'category', 'version', 'title', 'type', 'marks', 'rules_json', 'rules', 'created_at', 'updated_at']


class CriteriaCategorySerializer(serializers.ModelSerializer):
    items = CriteriaItemSerializer(many=True, read_only=True)

    class Meta:
        model = CriteriaCategory
        fields = ['id', 'code', 'category', 'access_level', 'evaluators', 'items', 'created_at']


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'code']


class SubCategorySerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_code = serializers.CharField(source='category.code', read_only=True)

    class Meta:
        model = SubCategory
        fields = [
            'id', 'category', 'category_id', 'category_name', 'category_code',
            'subcategory_name', 'default_marks', 'requires_dqc', 'max_per_cycle'
        ]


class UserSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    class_name_display = serializers.CharField(source='class_name.name', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'role', 'department', 'department_name', 'class_name',
            'class_name_display', 'roll_number', 'batch_year',
            'is_student_rep', 'is_staff', 'is_superuser', 'is_active'
        ]
        read_only_fields = ['is_staff', 'is_superuser']


class AcademicGradeBreakdownSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicGradeBreakdown
        fields = ['s_grade_count', 'a_plus_grade_count', 'a_grade_count', 'other_pass_count', 'failed_count', 'class_pass_percentage', 'total_students']


class SubmissionSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    student_id = serializers.IntegerField(source='user.id', read_only=True)
    class_id = serializers.SerializerMethodField()
    class_name = serializers.SerializerMethodField()
    category_id = serializers.SerializerMethodField()
    verification_logs = VerificationLogSerializer(many=True, read_only=True)
    grade_breakdown = AcademicGradeBreakdownSerializer(read_only=True)

    def get_class_id(self, obj):
        return obj.class_obj_id or (obj.user.class_name_id if obj.user else None)

    def get_class_name(self, obj):
        return obj.class_obj.name if obj.class_obj else (obj.user.class_name.name if obj.user and obj.user.class_name else None)

    def get_category_id(self, obj):
        return obj.category_id or (obj.criteria_item.category_id if getattr(obj, 'criteria_item', None) else None)

    class Meta:
        model = Submission
        fields = [
            'id', 'user', 'student_id', 'user_email', 'user_name', 'class_obj', 'class_id', 'class_name',
            'category', 'category_id', 'criteria_id', 'subcategory_id', 'criteria_version',
            'academic_year', 'submission_date', 'submission_type', 'description', 'status',
            'remarks', 'marks', 'calculated_marks', 'is_manual_eval', 'proof', 'proof_url',
            'proof_hash', 'certificate_id', 'event_id', 'start_date', 'end_date', 'evaluator_verified',
            'evidence', 'submission_metadata', 'verified_by_name', 'rep_verified_by_name', 'rep_remarks',
            'teacher_verified_by_name', 'teacher_remarks', 'evaluator_verified_by_name',
            'evaluator_remarks', 'verification_logs', 'grade_breakdown', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'user', 'student_id', 'class_id', 'class_name', 'category_id', 'verified_by_name', 'rep_verified_by_name',
            'teacher_verified_by_name', 'evaluator_verified_by_name',
            'evaluator_verified', 'created_at', 'updated_at'
        ]


class WorkflowAuditTrailSerializer(serializers.ModelSerializer):
    actor_email = serializers.CharField(source='actor.email', read_only=True)

    class Meta:
        model = WorkflowAuditTrail
        fields = [
            'id', 'submission', 'actor', 'actor_email', 'stage',
            'stage_name', 'previous_status', 'new_status', 'comments',
            'ip_address', 'user_agent', 'request_id', 'previous_hash',
            'record_hash', 'created_at'
        ]
        read_only_fields = '__all__'


class ClassIndexResultSerializer(serializers.ModelSerializer):
    class_name_display = serializers.CharField(source='class_name.name', read_only=True)
    academic_year_display = serializers.CharField(source='academic_year.year', read_only=True)

    class Meta:
        model = ClassIndexResult
        fields = ['id', 'class_name', 'class_name_display', 'academic_year', 'academic_year_display', 'academic_score', 'co_curricular_score', 'extra_curricular_score', 'final_index', 'rank', 'updated_at']
        read_only_fields = '__all__'


class ChampionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Champion
        fields = '__all__'
        read_only_fields = ['created_at']

    def validate_rank(self, value):
        if value < 1 or value > 100:
            raise serializers.ValidationError("Rank must be between 1 and 100.")
        return value


class BugReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = BugReport
        fields = '__all__'
        read_only_fields = ['status', 'whatsapp_numbers', 'created_at', 'updated_at']

    def validate_title(self, value):
        clean_val = str(value).strip()
        if len(clean_val) < 3:
            raise serializers.ValidationError("Title must be at least 3 characters long.")
        if len(clean_val) > 255:
            raise serializers.ValidationError("Title cannot exceed 255 characters.")
        return clean_val

    def validate_description(self, value):
        clean_val = str(value).strip()
        if len(clean_val) < 5:
            raise serializers.ValidationError("Description must be at least 5 characters long.")
        if len(clean_val) > 5000:
            raise serializers.ValidationError("Description cannot exceed 5000 characters.")
        return clean_val


class BugReportSafeSerializer(serializers.ModelSerializer):
    """Safe public serializer omitting reporter personal contact details."""
    class Meta:
        model = BugReport
        fields = [
            'id', 'title', 'description', 'bug_type', 'priority',
            'browser_device', 'page_url', 'role_category', 'status',
            'created_at'
        ]
        read_only_fields = [
            'id', 'title', 'description', 'bug_type', 'priority',
            'browser_device', 'page_url', 'role_category', 'status',
            'created_at'
        ]


class SystemAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemAuditLog
        fields = [
            'id', 'actor', 'actor_email', 'actor_role', 'action',
            'object_type', 'object_id', 'object_repr', 'old_value',
            'new_value', 'reason', 'ip_address', 'user_agent',
            'request_id', 'previous_hash', 'record_hash', 'created_at'
        ]
        read_only_fields = [
            'id', 'actor', 'actor_email', 'actor_role', 'action',
            'object_type', 'object_id', 'object_repr', 'old_value',
            'new_value', 'reason', 'ip_address', 'user_agent',
            'request_id', 'previous_hash', 'record_hash', 'created_at'
        ]



