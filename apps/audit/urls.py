from django.urls import path

from apps.audit import views


urlpatterns = [
    path('', views.activity_list, name='user_activity'),
]
