"""URLs for the Scraper Factory.

Mounted under /factory/ from tender_radar/urls.py. Nothing here touches the
existing tenders URL configuration.
"""

from django.urls import path

from . import views

app_name = "factory"

urlpatterns = [
    path("", views.factory_home, name="home"),
    path("operations/", views.operations_dashboard, name="operations"),
    path("review/", views.review_queue, name="review_queue"),
    path("review/<int:pk>/resolve/", views.review_resolve, name="review_resolve"),
    path("api/build/", views.api_build, name="api_build"),
    path("api/status/", views.api_status, name="api_status"),
]
