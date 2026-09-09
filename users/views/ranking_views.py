import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from users.models import Champion
from users.serializers import ChampionSerializer
from users.permissions import IsAdminOrPublicReadOnly
from users.file_security import validate_file_upload, ALLOWED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE_BYTES
from users.services.ranking_service import RankingService
from .base import record_system_audit_event

logger = logging.getLogger(__name__)


class ClassIndexView(APIView):
    """
    Compute and return the moderated class index M for all classes using authoritative ScoringEngine.
    Delegates calculation and snapshotting to RankingService.
    """
    permission_classes = [IsAdminOrPublicReadOnly]

    def get(self, request):
        year = request.query_params.get('year', None)
        explain = request.query_params.get('explain', '').lower() in ('true', '1', 'yes')

        data = RankingService.get_class_index_data(year=year, explain=explain)
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        """Allow IQAC or Admin to snapshot official class index results for an academic year."""
        user = request.user
        user_role = getattr(user, 'role', None)
        if not (user.is_superuser or user_role in ('admin', 'iqac')):
            return Response({"error": "Only IQAC and Administrators can snapshot official rankings."}, status=status.HTTP_403_FORBIDDEN)

        year = request.data.get('year')
        if not year:
            return Response({"error": "year parameter is required for snapshotting."}, status=status.HTTP_400_BAD_REQUEST)

        force = request.data.get('force', False)
        lock = request.data.get('lock', True)

        try:
            results = RankingService.snapshot_rankings(year=year, force=force, lock=lock)

            record_system_audit_event(
                action='RANKING_PUBLISH' if lock else 'RANKING_CALCULATE',
                object_type='AcademicYear',
                object_id=year,
                actor=user,
                object_repr=f"Official Rankings for Academic Year {year} ({len(results)} classes, locked={lock})",
                old_value=None,
                new_value={'academic_year': year, 'class_count': len(results), 'is_locked': lock},
                reason=f"Rankings {'published and locked' if lock else 'calculated and snapshotted'} by {getattr(user, 'email', '')}",
                request=request
            )

            return Response({
                "message": f"Successfully snapshotted rankings for academic year '{year}'.",
                "count": len(results),
                "is_locked": lock
            }, status=status.HTTP_201_CREATED)
        except PermissionError as pe:
            return Response({"error": str(pe)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            logger.exception("Snapshotting rankings failed")
            return Response({"error": "Failed to snapshot rankings."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChampionListView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        champions = Champion.objects.all()
        serializer = ChampionSerializer(champions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        image = request.FILES.get('image')
        if image:
            is_valid, err, _ = validate_file_upload(image, ALLOWED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE_BYTES)
            if not is_valid:
                return Response({'error': err, 'image': [err]}, status=status.HTTP_400_BAD_REQUEST)
        serializer = ChampionSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ChampionDetailView(APIView):
    permission_classes = [IsAdminOrPublicReadOnly]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def put(self, request, pk):
        try:
            champion = Champion.objects.get(pk=pk)
        except Champion.DoesNotExist:
            return Response({'error': 'Champion not found'}, status=status.HTTP_404_NOT_FOUND)

        image = request.FILES.get('image')
        if image:
            is_valid, err, _ = validate_file_upload(image, ALLOWED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE_BYTES)
            if not is_valid:
                return Response({'error': err, 'image': [err]}, status=status.HTTP_400_BAD_REQUEST)

        serializer = ChampionSerializer(champion, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        try:
            champion = Champion.objects.get(pk=pk)
            champion.delete()
        except Champion.DoesNotExist:
            pass
        return Response({'success': True}, status=status.HTTP_200_OK)
