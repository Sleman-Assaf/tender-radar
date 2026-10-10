# newspaper_tenders/urls.py

from django.urls import path

from tenders.permissions import admin_required

from . import views

app_name = 'newspaper_tenders'

urlpatterns = [
    path('', views.NewspaperTenderListView.as_view(), name='list'),
    # Manual "check today's edition" trigger (same idea as Run All Scrapers)
    path('run/', views.trigger_newspaper_check, name='run'),
    # System Dashboard audit view — admins only
    path('non-aec/', admin_required(views.NonAECNewspaperListView.as_view()),
         name='non_aec'),
    # No 'runs/' listing route: the run history lives on the consolidated
    # page at /it/runs/ (tenders:it_runs). The per-edition ACTION endpoints
    # below stay — that page posts to them.
    # Re-run one edition now (retry failed/pending PDFs) — admins only
    path('runs/<int:pk>/retry/', admin_required(views.retry_edition),
         name='retry_edition'),
    # Stop + empty one edition so it can be read from scratch — admins only
    path('runs/<int:pk>/reset/', admin_required(views.reset_edition),
         name='reset_edition'),
]
