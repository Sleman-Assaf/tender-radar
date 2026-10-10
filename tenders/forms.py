# tenders/forms.py

from django import forms
from .models import CountryConfig, ScraperSource, TenderingURL, RelevanceKeyword, CategoryKeyword


class CountryForm(forms.ModelForm):
    class Meta:
        model  = CountryConfig
        fields = ['code', 'name_en', 'name_ar', 'flag_emoji', 'currency', 'sort_order', 'is_active']
        widgets = {
            'code':       forms.TextInput(attrs={'placeholder': 'e.g. JO', 'maxlength': 3, 'style': 'text-transform:uppercase;width:80px;'}),
            'name_en':    forms.TextInput(attrs={'placeholder': 'e.g. Jordan'}),
            'name_ar':    forms.TextInput(attrs={'placeholder': 'e.g. الأردن', 'dir': 'rtl'}),
            'flag_emoji': forms.TextInput(attrs={'placeholder': 'optional', 'style': 'width:120px;'}),
            'currency':   forms.TextInput(attrs={'placeholder': 'e.g. JOD', 'style': 'width:90px;'}),
            'sort_order': forms.NumberInput(attrs={'style': 'width:70px;'}),
        }

    def clean_code(self):
        return self.cleaned_data['code'].upper().strip()


class SourceForm(forms.ModelForm):
    class Meta:
        model  = ScraperSource
        fields = ['name', 'name_ar', 'country', 'base_url', 'scraper_class',
                  'description', 'description_ar', 'is_active']
        widgets = {
            'name':          forms.TextInput(attrs={'placeholder': 'e.g. Jordan GTD'}),
            'name_ar':       forms.TextInput(attrs={'placeholder': 'e.g. ديوان المناقصات', 'dir': 'rtl'}),
            'base_url':      forms.URLInput(attrs={'placeholder': 'https://...'}),
            'scraper_class': forms.TextInput(attrs={'placeholder': 'e.g. JordanGTDScraper'}),
            'description':   forms.Textarea(attrs={'rows': 2, 'placeholder': 'Brief description'}),
            'description_ar':forms.Textarea(attrs={'rows': 2, 'placeholder': 'وصف مختصر', 'dir': 'rtl'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['country'].queryset = CountryConfig.objects.filter(is_active=True).order_by('sort_order')
        self.fields['country'].empty_label = '— Select Country —'
        # Clean dropdown labels (no emoji flags — they don't render on Windows)
        self.fields['country'].label_from_instance = lambda obj: f'{obj.name_en} ({obj.code})'


class TenderingURLForm(forms.ModelForm):
    class Meta:
        model  = TenderingURL
        fields = ['source', 'label', 'label_ar', 'url', 'pagination_style',
                  'page_param', 'max_pages', 'extra_params', 'notes', 'is_active', 'sort_order']
        widgets = {
            'label':    forms.TextInput(attrs={'placeholder': 'e.g. Ministry of Health Tenders'}),
            'label_ar': forms.TextInput(attrs={'placeholder': 'مناقصات وزارة الصحة', 'dir': 'rtl'}),
            'url':      forms.URLInput(attrs={'placeholder': 'https://...'}),
            'page_param': forms.TextInput(attrs={'style': 'width:100px;', 'placeholder': 'page'}),
            'max_pages':  forms.NumberInput(attrs={'style': 'width:80px;'}),
            'extra_params': forms.Textarea(attrs={'rows': 2, 'placeholder': '{"status": "open"}'}),
            'notes':    forms.Textarea(attrs={'rows': 2}),
            'sort_order': forms.NumberInput(attrs={'style': 'width:70px;'}),
        }

    def __init__(self, *args, source=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['source'].queryset = ScraperSource.objects.select_related('country').order_by('country', 'name')
        if source:
            self.fields['source'].initial = source
            self.fields['source'].widget.attrs['readonly'] = True


class RelevanceKeywordForm(forms.ModelForm):
    class Meta:
        model  = RelevanceKeyword
        fields = ['keyword', 'language', 'note', 'is_active']
        widgets = {
            'keyword': forms.TextInput(attrs={'placeholder': 'e.g. supervision / إشراف'}),
            'note':    forms.TextInput(attrs={'placeholder': 'Optional note'}),
        }


class CategoryKeywordForm(forms.ModelForm):
    class Meta:
        model  = CategoryKeyword
        fields = ['keyword', 'language', 'category', 'is_active']
        widgets = {
            'keyword': forms.TextInput(attrs={'placeholder': 'e.g. إشراف إنشائي'}),
        }


class UserProfileForm(forms.ModelForm):
    class Meta:
        from .models import UserProfile
        model  = UserProfile
        fields = ['industry', 'countries']
        widgets = {
            'industry':  forms.RadioSelect(),
            'countries': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import CountryConfig
        self.fields['countries'].queryset = CountryConfig.objects.filter(
            is_active=True, is_organization=False
        ).order_by('sort_order', 'name_en')
        self.fields['countries'].required = False
        # Clean checkbox labels: English name + ISO code only (no emoji flags,
        # which don't render on Windows). e.g. "Jordan (JO)".
        self.fields['countries'].label_from_instance = (
            lambda obj: f'{obj.name_en} ({obj.code})'
        )
