from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from django.conf import settings
from django.conf.urls.static import static

def api_root(request):
    endpoints = {
        "admin": "/admin/",
        "google_auth": "/api/auth/google/",
        "profile": "/api/auth/profile/"
    }
    if getattr(settings, 'ENABLE_DEV_BYPASS', False):
        endpoints["bypass_auth"] = "/api/auth/bypass/"
    return JsonResponse({
        "status": "online",
        "message": "Marian Excellence Grid Evaluation API Server is running",
        "endpoints": endpoints,
        "frontend": "http://localhost:3000"
    })

urlpatterns = [
    path('', api_root),
    path('admin/', admin.site.urls),
    path('api/', include('users.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
