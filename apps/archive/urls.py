from django.urls import path
from . import views

app_name = "archive"

urlpatterns = [
    path("", views.archive_list, name="list"),
    path("<slug:slug>/<int:pk>/restore/", views.restore_item, name="restore"),
    path("<slug:slug>/<int:pk>/delete/", views.permanent_delete_item, name="permanent_delete"),
]
