# magazine_tenders/urls.py

from django.urls import path

from tenders.permissions import admin_required

from . import views

app_name = 'magazine_tenders'

urlpatterns = [
    path('', views.MagazineTenderListView.as_view(), name='list'),
    # Manual "check magazines now" trigger (same idea as Run All Scanners)
    path('run/', views.trigger_magazine_check, name='run'),
    # System Dashboard audit view — admins only
    path('non-aec/', admin_required(views.NonAECMagazineListView.as_view()),
         name='non_aec'),
    # System Dashboard run history — admins only
    # No 'runs/' listing route: see /it/runs/. The retry endpoint stays.
    # Re-run one magazine now — admins only
    path('runs/<int:pk>/retry/', admin_required(views.retry_run), name='retry_run'),
    # Daily News Brief — موجز الأخبار — PDF for the CEO
    path('brief/pdf/', views.news_brief_pdf, name='brief_pdf'),
    path('brief/send/', admin_required(views.news_brief_send), name='brief_send'),
]
