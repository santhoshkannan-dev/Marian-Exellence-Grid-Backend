from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import ValidationError
from .models import (
    User, Department, Class, AcademicYear,
    CriteriaCategory, CriteriaItem, Submission,
    AcademicGradeBreakdown, WorkflowAuditTrail, ClassIndexResult
)

# -------------------------------------------------------------------
# Custom Class Admin Form with Exclusivity & Role Validation
# -------------------------------------------------------------------
class ClassAdminForm(forms.ModelForm):
    class Meta:
        model = Class
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limit faculty choices to faculty/teacher roles
        self.fields['class_teacher'].queryset = User.objects.filter(role__in=['faculty', 'teacher'])
        # Limit student rep choices to student role
        self.fields['dqc_member'].queryset = User.objects.filter(role='student')

    def clean(self):
        cleaned_data = super().clean()
        class_teacher = cleaned_data.get('class_teacher')
        dqc_member = cleaned_data.get('dqc_member')
        instance = self.instance

        # 1. Faculty Exclusivity Validation
        if class_teacher:
            existing = Class.objects.filter(class_teacher=class_teacher).exclude(pk=instance.pk).first()
            if existing:
                raise ValidationError(
                    f"Faculty '{class_teacher.get_full_name() or class_teacher.email}' is already assigned as Class Advisor to '{existing.name}'."
                )

        # 2. Student Representative Exclusivity & Class Match Validation
        if dqc_member:
            existing = Class.objects.filter(dqc_member=dqc_member).exclude(pk=instance.pk).first()
            if existing:
                raise ValidationError(
                    f"Student '{dqc_member.get_full_name() or dqc_member.email}' is already assigned as DQC Representative to '{existing.name}'."
                )

            if dqc_member.class_name and instance.name and dqc_member.class_name.name != instance.name:
                raise ValidationError(
                    f"Student '{dqc_member.get_full_name() or dqc_member.email}' belongs to '{dqc_member.class_name.name}' and cannot be assigned to '{instance.name}'."
                )

        return cleaned_data


# -------------------------------------------------------------------
# Registered Model Admins
# -------------------------------------------------------------------

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'role', 'department', 'class_name', 'is_student_rep_display', 'is_staff')
    list_filter = ('role', 'department', 'class_name', 'is_staff', 'is_active')
    search_fields = ('email', 'first_name', 'last_name', 'username')
    ordering = ('email',)

    fieldsets = (
        (None, {'fields': ('email', 'username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'google_id')}),
        ('Institutional Roles & Allocations', {'fields': ('role', 'department', 'class_name')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )

    @admin.display(description='Student Rep?', boolean=True)
    def is_student_rep_display(self, obj):
        return obj.rep_classes.exists()


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'created_at', 'updated_at')
    search_fields = ('name', 'code')


@admin.register(Class)
class ClassAdmin(admin.ModelAdmin):
    form = ClassAdminForm
    list_display = ('name', 'department', 'class_teacher', 'dqc_member', 'created_at')
    list_filter = ('department',)
    search_fields = ('name', 'department__name', 'class_teacher__email', 'dqc_member__email')


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ('year', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('year',)


class CriteriaItemInline(admin.TabularInline):
    model = CriteriaItem
    extra = 1


@admin.register(CriteriaCategory)
class CriteriaCategoryAdmin(admin.ModelAdmin):
    list_display = ('code', 'category', 'access_level', 'created_at')
    list_filter = ('access_level',)
    search_fields = ('code', 'category')
    inlines = [CriteriaItemInline]


@admin.register(CriteriaItem)
class CriteriaItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'type', 'marks', 'created_at')
    list_filter = ('type', 'category')
    search_fields = ('title', 'category__category')


class AcademicGradeBreakdownInline(admin.StackedInline):
    model = AcademicGradeBreakdown
    can_delete = False
    max_num = 1


class WorkflowAuditTrailInline(admin.TabularInline):
    model = WorkflowAuditTrail
    extra = 0
    readonly_fields = ('actor', 'stage', 'stage_name', 'previous_status', 'new_status', 'comments', 'created_at')


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'criteria_id', 'submission_type', 'academic_year', 'status', 'marks', 'created_at')
    list_filter = ('status', 'submission_type', 'academic_year')
    search_fields = ('user__email', 'description', 'proof', 'event_id')
    inlines = [AcademicGradeBreakdownInline, WorkflowAuditTrailInline]


@admin.register(AcademicGradeBreakdown)
class AcademicGradeBreakdownAdmin(admin.ModelAdmin):
    list_display = ('submission', 's_grade_count', 'a_plus_grade_count', 'a_grade_count', 'failed_count', 'class_pass_percentage', 'total_students')


@admin.register(WorkflowAuditTrail)
class WorkflowAuditTrailAdmin(admin.ModelAdmin):
    list_display = ('id', 'submission', 'actor', 'stage', 'stage_name', 'previous_status', 'new_status', 'created_at')
    list_filter = ('stage', 'new_status')
    search_fields = ('submission__id', 'actor__email', 'comments')


@admin.register(ClassIndexResult)
class ClassIndexResultAdmin(admin.ModelAdmin):
    list_display = ('class_name', 'academic_year', 'academic_score', 'co_curricular_score', 'extra_curricular_score', 'final_index', 'rank', 'updated_at')
    list_filter = ('academic_year', 'class_name__department')
    search_fields = ('class_name__name', 'academic_year__year')
