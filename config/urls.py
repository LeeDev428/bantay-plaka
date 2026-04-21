from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.http import HttpResponse
from django.views.static import serve as static_serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('healthz/', lambda request: HttpResponse('ok', content_type='text/plain')),
    path('', include('apps.accounts.urls')),
    path('dashboard/', include('apps.accounts.dashboard_urls')),
    path('residents/', include('apps.residents.urls')),
    path('visitors/', include('apps.visitors.urls')),
    path('logs/', include('apps.logs.urls')),
    path('detection/', include('apps.detection.urls')),
    path('reports/', include('apps.reports.urls')),
    re_path(r'^media/(?P<path>.*)$', static_serve, {'document_root': settings.MEDIA_ROOT}),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) \
  + staticfiles_urlpatterns()
