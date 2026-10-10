# tenders/it_urls.py
"""
System Dashboard routes. Included from tenders/urls.py under the `it/` prefix, so
every name below resolves inside the existing `tenders:` namespace
(e.g. {% url 'tenders:it_dashboard' %}).
"""

from django.urls import path

from . import it_views

urlpatterns = [
    # ── Dashboard ─────────────────────────────────────────────────────────────
    path('',                       it_views.it_dashboard,  name='it_dashboard'),
    path('logs/',                  it_views.system_logs,   name='it_logs'),
    path('logs/notifications/<int:pk>/', it_views.notification_detail,
                                                           name='it_notification_detail'),
    path('settings/',              it_views.it_settings,   name='it_settings'),

    # ── User management ───────────────────────────────────────────────────────
    path('users/',                        it_views.user_list,           name='it_users'),
    path('users/add/',                    it_views.user_create,         name='it_user_add'),
    path('users/<int:pk>/edit/',          it_views.user_edit,           name='it_user_edit'),
    path('users/<int:pk>/password/',      it_views.user_reset_password, name='it_user_password'),
    path('users/<int:pk>/toggle/',        it_views.user_toggle_active,  name='it_user_toggle'),
    path('users/<int:pk>/fb-alerts/',     it_views.user_toggle_fb_alerts, name='it_user_fb_alerts'),
    path('users/<int:pk>/delete/',        it_views.user_delete,         name='it_user_delete'),
    path('users/move/',                   it_views.user_move,           name='it_user_move'),

    # ── Role management ───────────────────────────────────────────────────────
    path('roles/',                        it_views.role_list,           name='it_roles'),
    path('roles/<int:pk>/change/',        it_views.role_change,         name='it_role_change'),

    # ── Company management ────────────────────────────────────────────────────
    path('companies/',                    it_views.company_list,        name='it_companies'),
    path('companies/add/',                it_views.company_create,      name='it_company_add'),
    path('companies/<int:pk>/edit/',      it_views.company_edit,        name='it_company_edit'),
    path('companies/<int:pk>/delete/',    it_views.company_delete,      name='it_company_delete'),

    # ── Department management ─────────────────────────────────────────────────
    path('departments/',                  it_views.department_list,     name='it_departments'),
    path('departments/add/',              it_views.department_create,   name='it_department_add'),
    path('departments/<int:pk>/',         it_views.department_detail,   name='it_department_detail'),
    path('departments/<int:pk>/edit/',    it_views.department_edit,     name='it_department_edit'),
    path('departments/<int:pk>/head/',    it_views.department_set_head, name='it_department_head'),
    path('departments/<int:pk>/delete/',  it_views.department_delete,   name='it_department_delete'),

    # ── Backup & restore ──────────────────────────────────────────────────────
    path('backups/',                      it_views.backup_list,         name='it_backups'),
    path('backups/create/',               it_views.backup_create,       name='it_backup_create'),
    path('backups/upload/',               it_views.backup_upload,       name='it_backup_upload'),
    path('backups/schedule/',             it_views.backup_schedule,     name='it_backup_schedule'),
    path('backups/<int:pk>/download/',    it_views.backup_download,     name='it_backup_download'),
    path('backups/<int:pk>/restore/',     it_views.backup_restore,      name='it_backup_restore'),
    path('backups/<int:pk>/delete/',      it_views.backup_delete,       name='it_backup_delete'),

    # ── Newspaper & Magazine runs (one page for both features) ────────────────
    path('runs/',                         it_views.feature_runs,        name='it_runs'),

    # ── Documentation ─────────────────────────────────────────────────────────
    # Confidential; served by a gated view, never from static/ or media/.
    path('documentation/',                it_views.documentation_download,
                                                                        name='it_documentation'),
    # The user guide is NOT here — it is for every signed-in user, so it hangs
    # off /documentation/user-guide/ in tenders/urls.py rather than behind the
    # administrators-only /it/ prefix.
    path('documentation/add-source/',     it_views.add_source_guide_download,
                                                                        name='it_doc_add_source'),
]
