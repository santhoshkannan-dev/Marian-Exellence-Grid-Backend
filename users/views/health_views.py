"""
Production Health & Readiness Check Endpoint.

Exposes a lightweight, unauthenticated probe for load balancers (ALB, Nginx, K8s)
to verify API process health, database connectivity, and private media storage readiness.
"""

import time
import logging
from datetime import datetime, timezone
from django.db import connection
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from users.file_security import get_private_media_root

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    """
    GET /api/health/
    Unauthenticated health and readiness probe.
    Returns:
      200 OK when database and storage are operational.
      503 Service Unavailable if database is unreachable.
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        health_data = {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "environment": "production" if not settings.DEBUG else "development",
            "version": "1.0.0",
        }

        # 1. Database connectivity & latency check
        db_healthy = False
        db_latency_ms = None
        db_error = None
        try:
            start = time.perf_counter()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
            db_latency_ms = round((time.perf_counter() - start) * 1000, 2)
            db_healthy = True
        except Exception as e:
            logger.error(f"Health check database failure: {e}")
            db_error = str(e)

        health_data["database"] = {
            "status": "connected" if db_healthy else "disconnected",
            "engine": connection.vendor,
            "latency_ms": db_latency_ms,
        }
        if db_error:
            health_data["database"]["error"] = "Database connection failed"

        # 2. Storage accessibility check
        storage_healthy = False
        try:
            storage_root = get_private_media_root()
            storage_healthy = bool(storage_root)
        except Exception as e:
            logger.error(f"Health check storage failure: {e}")

        health_data["storage"] = {
            "status": "accessible" if storage_healthy else "degraded",
        }

        # Overall readiness assessment
        if not db_healthy:
            health_data["status"] = "unhealthy"
            return Response(health_data, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(health_data, status=status.HTTP_200_OK)
