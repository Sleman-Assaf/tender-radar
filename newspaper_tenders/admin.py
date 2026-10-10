# newspaper_tenders/admin.py
# Registered for completeness (the Django admin is not URL-mounted in this
# project — the System Dashboard is the admin surface — but registrations keep
# model metadata consistent with the rest of the codebase).

from django.contrib import admin

from .models import NewspaperEdition, NewspaperEditionFile, NewspaperTenderMeta


@admin.register(NewspaperEdition)
class NewspaperEditionAdmin(admin.ModelAdmin):
    list_display  = ('edition_date', 'source', 'status', 'pdf_count',
                     'discovered_at', 'processed_at')
    list_filter   = ('status', 'source')
    ordering      = ('-edition_date',)


@admin.register(NewspaperEditionFile)
class NewspaperEditionFileAdmin(admin.ModelAdmin):
    list_display  = ('edition', 'pdf_url', 'status', 'file_size',
                     'tenders_found', 'processed_at')
    list_filter   = ('status',)


@admin.register(NewspaperTenderMeta)
class NewspaperTenderMetaAdmin(admin.ModelAdmin):
    list_display  = ('tender', 'edition', 'page_number', 'ai_confidence')
    list_select_related = ('tender', 'edition')
