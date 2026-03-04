from django.urls import re_path
from apps.logs.consumers import LogConsumer

websocket_urlpatterns = [
    re_path(r'^ws/logs/$', LogConsumer.as_asgi()),
]
