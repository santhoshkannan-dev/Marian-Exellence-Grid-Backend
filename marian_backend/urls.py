from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from django.conf import settings
from django.conf.urls.static import static

from users.views import HealthCheckView

def api_root(request):
    endpoints = {
        "admin": "/admin/",
        "health": "/api/health/",
        "google_auth": "/api/auth/google/",
        "profile": "/api/auth/profile/"
    }
    return JsonResponse({
        "status": "online",
        "message": "Marian Excellence Grid Evaluation API Server is running",
        "endpoints": endpoints,
        "frontend": "http://localhost:3000"
    })

urlpatterns = [
    path('', api_root),
    path('health/', HealthCheckView.as_view(), name='root-health-check'),
    path('admin/', admin.site.urls),
    path('api/', include('users.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

