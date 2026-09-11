import logging
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from users.models import CriteriaCategory, CriteriaItem, CriteriaRule, CriteriaVersion
from users.serializers import (
    CriteriaCategorySerializer,
    CriteriaItemSerializer,
    CriteriaVersionSerializer,
    SubCategorySerializer,
)
from users.permissions import IsAdminRole, IsAdminOrReadOnly
from users.audit import record_system_audit_event

logger = logging.getLogger(__name__)


class CriteriaVersionListView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request):
        year = request.query_params.get('year', None)
        qs = CriteriaVersion.objects.all()
        if year:
            qs = qs.filter(academic_year=year)
        serializer = CriteriaVersionSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        academic_year = request.data.get('academic_year')
        if not academic_year:
            return Response({"error": "academic_year is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            latest = CriteriaVersion.objects.select_for_update().filter(academic_year=academic_year).order_by('-version').first()
            next_v = (latest.version + 1) if latest else 1

            name = request.data.get('name', f"{academic_year} v{next_v}")
            new_version = CriteriaVersion.objects.create(
                academic_year=academic_year,
                version=next_v,
                name=name,
                is_locked=False
            )

            clone_from_id = request.data.get('clone_from_version_id')
            if clone_from_id:
                source_items = CriteriaItem.objects.filter(version_id=clone_from_id).prefetch_related('rules')
                for src_item in source_items:
                    new_item = CriteriaItem.objects.create(
                        category=src_item.category,
                        version=new_version,
                        title=src_item.title,
                        type=src_item.type,
                        marks=src_item.marks,
                        rules_json=src_item.rules_json
                    )
                    for src_rule in src_item.rules.all():
                        CriteriaRule.objects.create(
                            item=new_item,
                            rule_type=src_rule.rule_type,
                            maximum_marks=src_rule.maximum_marks,
                            min_count=src_rule.min_count,
                            max_count=src_rule.max_count,
                            is_negative=src_rule.is_negative,
                            multiplier=src_rule.multiplier,
                            extra_config=src_rule.extra_config
                        )

        serializer = CriteriaVersionSerializer(new_version)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CriteriaVersionDetailView(APIView):
    permission_classes = [IsAdminRole]

    def put(self, request, pk):
        try:
            cv = CriteriaVersion.objects.get(pk=pk)
        except CriteriaVersion.DoesNotExist:
            return Response({"error": "Criteria version not found."}, status=status.HTTP_404_NOT_FOUND)

        old_locked = cv.is_locked
        if 'is_locked' in request.data:
            cv.is_locked = bool(request.data['is_locked'])
            if cv.is_locked and not cv.published_at:
                from django.utils import timezone
                cv.published_at = timezone.now()

        if 'name' in request.data:
            cv.name = request.data['name']

        cv.save()

        record_system_audit_event(
            action='CRITERIA_CHANGE',
            object_type='CriteriaVersion',
            object_id=cv.id,
            actor=request.user,
            object_repr=f"CriteriaVersion {cv.name} ({cv.academic_year})",
            old_value={'is_locked': old_locked},
            new_value={'is_locked': cv.is_locked, 'name': cv.name},
            reason=f"Criteria version modified by {getattr(request.user, 'email', '')}",
            request=request
        )

        serializer = CriteriaVersionSerializer(cv)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CriteriaCategoryListView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request):
        categories = CriteriaCategory.objects.prefetch_related('items').all().order_by('id')
        serializer = CriteriaCategorySerializer(categories, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = CriteriaCategorySerializer(data=request.data)
        if serializer.is_valid():
            cat = serializer.save()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaCategory',
                object_id=cat.code or cat.id,
                actor=request.user,
                object_repr=f"CriteriaCategory '{cat.name}' ({cat.code})",
                old_value=None,
                new_value=serializer.data,
                reason=f"Criteria category created by {getattr(request.user, 'email', '')}",
                request=request
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CriteriaCategoryDetailView(APIView):
    permission_classes = [IsAdminRole]

    def put(self, request, pk):
        try:
            if str(pk).isdigit():
                category = CriteriaCategory.objects.get(pk=int(pk))
            else:
                category = CriteriaCategory.objects.get(code=pk)
        except CriteriaCategory.DoesNotExist:
            return Response({"error": "Category not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = CriteriaCategorySerializer(category, data=request.data, partial=True)
        if serializer.is_valid():
            cat = serializer.save()
            if 'evaluators' in request.data:
                from django.db.models import Q
                from users.models import UserGroupModel, UserGroupMember, EvaluatorCategoryAssignment, User, StaffProfile, AcademicYear
                ec_group, _ = UserGroupModel.objects.get_or_create(
                    group_id='grp-evaluation-committee',
                    defaults={'name': 'Evaluation Committee', 'description': 'Evaluator members assigned to review activity submissions'}
                )
                current_emails = [str(e).strip().lower() for e in (cat.evaluators or []) if e and isinstance(e, str)]
                active_ay = AcademicYear.objects.filter(is_active=True).first()
                ay_int = 2025
                if active_ay and active_ay.year:
                    try:
                        ay_int = int(active_ay.year.split('-')[0])
                    except (ValueError, IndexError):
                        pass

                EvaluatorCategoryAssignment.objects.filter(category=cat).exclude(
                    Q(evaluator__email__in=current_emails) | Q(member__email__in=current_emails)
                ).delete()

                for em in current_emails:
                    u = User.objects.filter(email__iexact=em).first()
                    member, _ = UserGroupMember.objects.get_or_create(
                        group=ec_group,
                        email=em,
                        defaults={
                            'user': u,
                            'name': u.get_full_name() if u else em.split('@')[0].capitalize(),
                            'department': u.department if u else None
                        }
                    )
                    if u and not member.user:
                        member.user = u
                        member.save(update_fields=['user'])
                    if u:
                        StaffProfile.objects.update_or_create(
                            user=u,
                            defaults={
                                'department': u.department,
                                'designation': 'Evaluator'
                            }
                        )
                    eval_assignment = EvaluatorCategoryAssignment.objects.filter(
                        category=cat,
                        member=member
                    ).first()

                    if not eval_assignment and u:
                        eval_assignment = EvaluatorCategoryAssignment.objects.filter(
                            category=cat,
                            evaluator=u
                        ).first()

                    if eval_assignment:
                        updated_f = []
                        if eval_assignment.member != member:
                            eval_assignment.member = member
                            updated_f.append('member')
                        if u and eval_assignment.evaluator != u:
                            eval_assignment.evaluator = u
                            updated_f.append('evaluator')
                        if eval_assignment.academic_year != ay_int:
                            eval_assignment.academic_year = ay_int
                            updated_f.append('academic_year')
                        if getattr(request.user, 'is_authenticated', False) and not eval_assignment.assigned_by:
                            eval_assignment.assigned_by = request.user
                            updated_f.append('assigned_by')
                        if updated_f:
                            eval_assignment.save(update_fields=updated_f)
                    else:
                        EvaluatorCategoryAssignment.objects.create(
                            category=cat,
                            member=member,
                            evaluator=u,
                            academic_year=ay_int,
                            assigned_by=request.user if getattr(request.user, 'is_authenticated', False) else None
                        )
                ec_group.sync_json_members()

            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaCategory',
                object_id=category.code or category.id,
                actor=request.user,
                object_repr=f"CriteriaCategory '{category.category}'",
                old_value=None,
                new_value=serializer.data,
                reason=f"Criteria category updated by {getattr(request.user, 'email', '')}",
                request=request
            )
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        try:
            if str(pk).isdigit():
                category = CriteriaCategory.objects.get(pk=int(pk))
            else:
                category = CriteriaCategory.objects.get(code=pk)
            cat_name = category.category
            category.delete()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaCategory',
                object_id=pk,
                actor=request.user,
                object_repr=f"CriteriaCategory '{cat_name}' deleted",
                old_value={'category': cat_name},
                new_value=None,
                reason=f"Criteria category deleted by {getattr(request.user, 'email', '')}",
                request=request
            )
        except CriteriaCategory.DoesNotExist:
            pass
        return Response({"success": True}, status=status.HTTP_200_OK)


class CriteriaItemListView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        serializer = CriteriaItemSerializer(data=request.data)
        if serializer.is_valid():
            item = serializer.save()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaItem',
                object_id=item.id,
                actor=request.user,
                object_repr=f"CriteriaItem #{item.id} '{item.title}'",
                old_value=None,
                new_value=serializer.data,
                reason=f"Criteria item created by {getattr(request.user, 'email', '')}",
                request=request
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CriteriaItemDetailView(APIView):
    permission_classes = [IsAdminRole]

    def put(self, request, pk):
        try:
            item = CriteriaItem.objects.get(pk=pk)
        except CriteriaItem.DoesNotExist:
            return Response({"error": "Item not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = CriteriaItemSerializer(item, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaItem',
                object_id=item.id,
                actor=request.user,
                object_repr=f"CriteriaItem #{item.id} '{item.title}'",
                old_value=None,
                new_value=serializer.data,
                reason=f"Criteria item updated by {getattr(request.user, 'email', '')}",
                request=request
            )
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        try:
            item = CriteriaItem.objects.get(pk=pk)
            title = item.title
            item.delete()
            record_system_audit_event(
                action='CRITERIA_CHANGE',
                object_type='CriteriaItem',
                object_id=pk,
                actor=request.user,
                object_repr=f"CriteriaItem #{pk} '{title}' deleted",
                old_value={'title': title},
                new_value=None,
                reason=f"Criteria item deleted by {getattr(request.user, 'email', '')}",
                request=request
            )
        except CriteriaItem.DoesNotExist:
            pass
        return Response({"success": True}, status=status.HTTP_200_OK)


class SubCategoryListView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request):
        category_id = request.query_params.get('category_id') or request.query_params.get('categoryId')
        category_code = request.query_params.get('category_code') or request.query_params.get('categoryCode')

        from users.models import SubCategory
        qs = SubCategory.objects.select_related('category').all()

        if category_id:
            try:
                cid = int(category_id)
                if 106 <= cid <= 118:
                    cid = cid - 105
                qs = qs.filter(category_id=cid)
            except (ValueError, TypeError):
                pass
        elif category_code:
            qs = qs.filter(category__code__iexact=str(category_code).strip())

        serializer = SubCategorySerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = SubCategorySerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SubCategoryDetailView(APIView):
    permission_classes = [IsAdminOrReadOnly]

    def get(self, request, pk):
        from users.models import SubCategory
        try:
            sub = SubCategory.objects.select_related('category').get(pk=pk)
        except SubCategory.DoesNotExist:
            return Response({"error": "Subcategory not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = SubCategorySerializer(sub)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        from users.models import SubCategory
        try:
            sub = SubCategory.objects.get(pk=pk)
        except SubCategory.DoesNotExist:
            return Response({"error": "Subcategory not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = SubCategorySerializer(sub, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        from users.models import SubCategory
        try:
            sub = SubCategory.objects.get(pk=pk)
            sub.delete()
        except SubCategory.DoesNotExist:
            pass
        return Response({"success": True}, status=status.HTTP_200_OK)
