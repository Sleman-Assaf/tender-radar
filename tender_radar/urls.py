# tender_radar/urls.py

from django.views.generic import RedirectView
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views

urlpatterns = [
    # The Django admin panel is deliberately not mounted. It duplicated most of
    # the System Dashboard with a raw, unguarded view of the same models, which
    # is exactly the second administration surface the dashboard exists to
    # replace. `django.contrib.admin` stays in INSTALLED_APPS so tenders/admin.py
    # and the third-party admin registrations still import; there is simply no
    # URL leading to them.

    # Handles /accounts/login/ — Django's default LoginRequiredMixin target
    path('accounts/login/',  auth_views.LoginView.as_view(template_name='auth/login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),

    # Shorthand alias
    path('login/',  auth_views.LoginView.as_view(template_name='auth/login.html')),
    path('logout/', auth_views.LogoutView.as_view()),

    path('', include('tenders.urls')),
    path('bd/', include('bd.urls')),
    # Newspaper Tenders — عطاءات الجريدة (own section, separate from Scrapers)
    path('newspaper-tenders/', include('newspaper_tenders.urls')),
    # News — الأخبار (own section, separate from Scrapers)
    path('news/', include('magazine_tenders.urls')),
    # The section was called Magazine Tenders until 2026-09-06. Old links,
    # bookmarks and anything a user pasted into an e-mail keep working: the
    # remainder of the path is carried across, so /magazine-tenders/non-aec/
    # lands on /news/non-aec/ rather than the section root.
    path('magazine-tenders/', RedirectView.as_view(url='/news/', permanent=True,
                                                     query_string=True)),
    re_path(r'^magazine-tenders/(?P<rest>.*)$',
            RedirectView.as_view(url='/news/%(rest)s', permanent=True,
                                 query_string=True)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
