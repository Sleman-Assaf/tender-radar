# bd/urls.py
# Path: TenderRadar/bd/urls.py

from django.urls import path

from . import views

app_name = 'bd'

urlpatterns = [
    # ── CV Pool ──────────────────────────────────────────────────────────────
    path('cvs/',                    views.cv_list,     name='cv_list'),
    path('cvs/upload/',             views.cv_upload,   name='cv_upload'),
    path('cvs/bulk/',               views.cv_bulk_upload, name='cv_bulk_upload'),
    path('cvs/batches/<int:pk>/',        views.cv_batch_detail, name='cv_batch_detail'),
    path('cvs/batches/<int:pk>/status/', views.cv_batch_status, name='cv_batch_status'),
    path('cvs/batches/<int:pk>/retry/',  views.cv_batch_retry,  name='cv_batch_retry'),
    path('cvs/batches/<int:pk>/cancel/', views.cv_batch_cancel, name='cv_batch_cancel'),
    path('cvs/batches/<int:pk>/delete/', views.cv_batch_delete, name='cv_batch_delete'),
    path('cvs/<int:pk>/reviewed/',  views.cv_mark_reviewed, name='cv_mark_reviewed'),
    path('cvs/<int:pk>/photo/',     views.cv_photo_update,  name='cv_photo_update'),
    path('cvs/review/',             views.cv_review,   name='cv_review'),
    path('cvs/<int:pk>/',           views.cv_detail,   name='cv_detail'),
    path('cvs/<int:pk>/edit/',      views.cv_edit,     name='cv_edit'),
    path('cvs/<int:pk>/download/',  views.cv_download, name='cv_download'),
    path('cvs/<int:pk>/delete/',    views.cv_delete,   name='cv_delete'),

    # ── Historical Projects ──────────────────────────────────────────────────
    path('projects/',                    views.project_list,   name='project_list'),
    path('projects/new/',                views.project_create, name='project_create'),
    path('projects/<int:pk>/',           views.project_detail, name='project_detail'),
    path('projects/<int:pk>/edit/',      views.project_edit,   name='project_edit'),
    path('projects/<int:pk>/delete/',    views.project_delete, name='project_delete'),
    path('projects/<int:pk>/docs/',      views.project_doc_upload, name='project_doc_upload'),
    path('projects/<int:pk>/docs/<int:doc_pk>/delete/',
         views.project_doc_delete, name='project_doc_delete'),
]
