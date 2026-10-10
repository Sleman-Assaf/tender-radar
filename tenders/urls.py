# tenders/urls.py

from django.urls import include, path
from . import views, manage_views, analytics_views, it_views
from .permissions import admin_required

app_name = 'tenders'

urlpatterns = [
    # ── System Administration Dashboard ────────────────────────────────────────────
    # Single entry point for user, department, role, backup and settings
    # administration. Routes live in tenders/it_urls.py.
    path('it/', include('tenders.it_urls')),

    # ── Teams & Notes ──────────────────────────────────────────────────────────
    path('teams/',                views.my_teams,     name='my_teams'),
    path('teams/create/',         views.team_create,  name='team_create'),
    path('teams/<int:pk>/edit/',  views.team_edit,    name='team_edit'),
    path('teams/<int:pk>/',        views.team_detail,  name='team_detail'),
    path('teams/<int:pk>/delete/',views.team_delete,  name='team_delete'),
    path('tenders/<int:pk>/note/',views.post_note,    name='post_note'),
    path('tenders/<int:pk>/share/',views.share_to_team, name='share_to_team'),
    path('notes/<int:note_pk>/delete/', views.delete_note, name='delete_note'),
    path('notifications/',        views.notifications, name='notifications'),
    # ── Feedback ───────────────────────────────────────────────────────────────
    path('feedback/',             views.submit_feedback, name='submit_feedback'),
    path('feedback/mine/',        views.my_feedback,     name='my_feedback'),
    path('feedback/<int:pk>/response/', views.feedback_response, name='feedback_response'),
    # ── Manage feedback ────────────────────────────────────────────────────────
    path('manage/feedback/',             manage_views.manage_feedback,        name='manage_feedback'),
    path('manage/feedback/<int:pk>/',    manage_views.manage_feedback_detail, name='manage_feedback_detail'),
    path('manage/users/',                 manage_views.manage_users,           name='manage_users'),

    path('profile/',                     views.user_profile,               name='profile'),

    # ── Main app ──────────────────────────────────────────────────────────────
    path('',                             views.DashboardView.as_view(),    name='dashboard'),
    path('tenders/',                     views.TenderListView.as_view(),   name='list'),
    path('tenders/<int:pk>/',            views.TenderDetailView.as_view(), name='detail'),
    # Scraper source operations. Administrative: the page carries Run controls
    # and exposes the scraping configuration, so it is gated like the rest of
    # the System Dashboard rather than left open to every signed-in user.
    path('sources/',                     admin_required(views.SourceListView.as_view()),
                                                                           name='sources'),

    # Actions
    path('tenders/<int:pk>/bookmark/',   views.toggle_bookmark,            name='toggle_bookmark'),
    path('tenders/<int:pk>/delete/',     views.delete_tender,              name='delete_tender'),
    path('tenders/<int:pk>/toggle-aec/', views.toggle_aec,                 name='toggle_aec'),
    # Running a scan is open to every signed-in user: the "Run All Scanners"
    # button sits on the dashboard, which everyone sees, and a scan only fetches
    # public tender listings — it changes no configuration. The views themselves
    # hold the single-run-at-a-time guard, so a reader cannot start a second
    # full run on top of one already in progress.
    path('scraper/run/<int:source_id>/', views.trigger_scraper,            name='trigger_scraper'),
    path('scraper/run-all/',             views.trigger_all_scrapers,       name='trigger_all'),
    path('scraper/status/',              views.scraper_status,             name='scraper_status'),
    # /scrape-logs/ removed — System Dashboard → System Logs → Scraper Runs is
    # the same data, paginated over every row instead of the newest 100.
    # The user guide is for everyone who uses TenderRadar, not just
    # administrators, so it sits outside the /it/ prefix. Signed-in users only —
    # the document is confidential — and the view itself lives in it_views.py
    # beside the other document handlers.
    path('documentation/user-guide/',    it_views.user_guide_download,     name='user_guide'),
    path('countries/<str:code>/favorite/', views.toggle_favorite_country,  name='toggle_favorite_country'),
    path('tenders/<int:pk>/aec-feedback/', views.aec_feedback,               name='aec_feedback'),
    # Was reachable with no login at all.
    path('aec-feedback/report/',         admin_required(views.aec_feedback_report),
                                                                           name='aec_feedback_report'),

    # ── Superuser Management ──────────────────────────────────────────────────
    path('manage/',                      manage_views.manage_hub,          name='manage_hub'),

    # Countries
    path('manage/countries/',            manage_views.country_list,        name='manage_countries'),
    path('manage/countries/add/',        manage_views.country_create,      name='manage_country_add'),
    path('manage/countries/<str:code>/edit/',   manage_views.country_edit,   name='manage_country_edit'),
    path('manage/countries/<str:code>/delete/', manage_views.country_delete, name='manage_country_delete'),
    path('manage/countries/<str:code>/toggle/', manage_views.country_toggle, name='manage_country_toggle'),

    # Sources
    path('manage/sources/',              manage_views.source_list,         name='manage_sources'),
    path('manage/sources/add/',          manage_views.source_create,       name='manage_source_add'),
    path('manage/sources/<int:pk>/edit/',   manage_views.source_edit,      name='manage_source_edit'),
    path('manage/sources/<int:pk>/delete/', manage_views.source_delete,    name='manage_source_delete'),
    path('manage/sources/<int:pk>/toggle/', manage_views.source_toggle,    name='manage_source_toggle'),
    path('manage/sources/<int:pk>/flush/',  manage_views.source_flush,     name='manage_source_flush'),
    path('manage/sources/flush-today/',    manage_views.flush_today,       name='manage_flush_today'),

    # Tendering URLs (scoped to a source)
    path('manage/sources/<int:source_id>/urls/',       manage_views.url_list,   name='manage_source_urls'),
    path('manage/sources/<int:source_id>/urls/add/',   manage_views.url_create, name='manage_url_add'),
    path('manage/urls/<int:pk>/edit/',                 manage_views.url_edit,   name='manage_url_edit'),
    path('manage/urls/<int:pk>/delete/',               manage_views.url_delete, name='manage_url_delete'),
    path('manage/urls/<int:pk>/toggle/',               manage_views.url_toggle, name='manage_url_toggle'),

    # Relevance Keywords
    path('manage/keywords/relevance/',       manage_views.relevance_keyword_list,   name='manage_rel_keywords'),
    path('manage/keywords/relevance/add/',   manage_views.relevance_keyword_create, name='manage_rel_keyword_add'),
    path('manage/keywords/relevance/<int:pk>/edit/',   manage_views.relevance_keyword_edit,   name='manage_rel_keyword_edit'),
    path('manage/keywords/relevance/<int:pk>/delete/', manage_views.relevance_keyword_delete, name='manage_rel_keyword_delete'),
    path('manage/keywords/relevance/<int:pk>/toggle/', manage_views.relevance_keyword_toggle, name='manage_rel_keyword_toggle'),

    # Category Keywords
    path('manage/keywords/category/',       manage_views.category_keyword_list,   name='manage_cat_keywords'),
    path('manage/keywords/category/add/',   manage_views.category_keyword_create, name='manage_cat_keyword_add'),
    path('manage/keywords/category/<int:pk>/edit/',   manage_views.category_keyword_edit,   name='manage_cat_keyword_edit'),
    path('manage/keywords/category/<int:pk>/delete/', manage_views.category_keyword_delete, name='manage_cat_keyword_delete'),
    path('manage/keywords/category/<int:pk>/toggle/', manage_views.category_keyword_toggle, name='manage_cat_keyword_toggle'),
    path('manage/ai-settings/', manage_views.manage_site_settings, name='manage_site_settings'),

    # Integrations — MS Graph e-mail · LDAP login · WhatsApp alerts
    path('manage/integrations/',                 manage_views.manage_integration_settings, name='manage_integrations'),
    path('manage/integrations/token/graph/',     manage_views.integration_get_graph_token, name='integration_get_graph_token'),

    # Daily tender digest
    path('manage/digest/',           manage_views.manage_digest,   name='manage_digest'),
    path('manage/digest/send-now/',  manage_views.digest_send_now, name='digest_send_now'),
    path('manage/digest/test/',      manage_views.digest_send_test, name='digest_send_test'),
    path('manage/integrations/test/graph/',      manage_views.integration_test_graph,      name='integration_test_graph'),
    path('manage/integrations/test/ldap/',       manage_views.integration_test_ldap,       name='integration_test_ldap'),
    path('manage/integrations/test/whatsapp/',   manage_views.integration_test_whatsapp,   name='integration_test_whatsapp'),

    # ── Analytics ──────────────────────────────────────────────────────────────
    path('analytics/',                         analytics_views.analytics_hub,         name='analytics_hub'),
    path('analytics/logins/',                  analytics_views.report_logins,         name='analytics_logins'),
    path('analytics/open-tenders/',            analytics_views.report_open_tenders,   name='analytics_open_tenders'),
    path('analytics/portal-clicks/',           analytics_views.report_portal_clicks,  name='analytics_portal_clicks'),
    path('analytics/evaluation/',              analytics_views.report_evaluation,     name='analytics_evaluation'),
    path('analytics/user-activity/',           analytics_views.report_user_activity,  name='analytics_user_activity'),
    path('analytics/scraper-health/',          analytics_views.report_scraper_health, name='analytics_scraper_health'),
    path('tenders/<int:pk>/portal-click/',     analytics_views.log_portal_click,      name='log_portal_click'),
]
