# tenders/it_forms.py
"""
Forms used by the System Dashboard (tenders/it_views.py).

Kept apart from tenders/forms.py so the administration module stays
self-contained. Every form that can change a role takes the acting user and
narrows the role choices through tenders.permissions.assignable_roles — the
backend, not the template, decides what may be assigned.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import Company, Department, SystemRole, UserProfile, BackupSchedule
from .permissions import assignable_roles

User = get_user_model()


def _user_label(u) -> str:
    full = u.get_full_name()
    return f'{full} ({u.username})' if full else u.username


# ── Users ─────────────────────────────────────────────────────────────────────

class ITUserForm(forms.ModelForm):
    """
    Create / edit a user together with their role, company and department.

    Only the department is stored on the profile — the company is derived from
    it (UserProfile.company), so the two can never contradict. The company
    field here narrows the department choices (a script in the template
    filters live; clean() enforces the same rule server-side).
    """

    role = forms.ChoiceField(
        choices=(), label='System Role',
        help_text='Super Admin = unrestricted, including all administration · '
                  'Regular User = tender modules only.',
    )
    company = forms.ModelChoiceField(
        queryset=Company.objects.none(), required=False,
        empty_label='— No company —', label='Company',
        help_text='Choosing a company narrows the department list below.',
    )
    department = forms.ModelChoiceField(
        queryset=Department.objects.none(), required=False,
        empty_label='— No department —', label='Department',
        help_text='An employee belongs to exactly one department; their '
                  'company follows from it.',
    )
    password1 = forms.CharField(
        label='Password', required=False, widget=forms.PasswordInput,
        help_text='Leave blank when editing to keep the current password.',
    )
    password2 = forms.CharField(
        label='Confirm Password', required=False, widget=forms.PasswordInput,
    )

    class Meta:
        model  = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        widgets = {
            'username':   forms.TextInput(attrs={'placeholder': 'e.g. s.assaf', 'autocomplete': 'off'}),
            'first_name': forms.TextInput(attrs={'placeholder': 'First name'}),
            'last_name':  forms.TextInput(attrs={'placeholder': 'Last name'}),
            'email':      forms.EmailInput(attrs={'placeholder': 'name@ajigroup.com'}),
        }

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields['role'].choices     = assignable_roles(actor)
        self.fields['company'].queryset = Company.objects.filter(is_active=True).order_by('name')
        self.fields['department'].queryset = (Department.objects
                                              .filter(is_active=True)
                                              .select_related('company')
                                              .order_by('company__name', 'name'))
        self.fields['department'].label_from_instance = (
            lambda d: f'{d.name} — {d.company.name}' if d.company_id else d.name)
        self.fields['email'].required   = True
        self.fields['is_active'].label  = 'Active (can sign in)'

        profile = getattr(self.instance, 'profile', None) if self.instance.pk else None
        if profile:
            self.fields['role'].initial       = profile.role
            self.fields['department'].initial = profile.department_id
            if profile.department_id:
                self.fields['company'].initial = profile.department.company_id
        else:
            self.fields['role'].initial = SystemRole.USER
            self.fields['password1'].required = True
            self.fields['password2'].required = True

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        qs = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError('That username is already taken.')
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        qs = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if email and qs.exists():
            raise ValidationError('That e-mail address is already in use.')
        return email

    def department_company_map(self) -> dict:
        """
        {department pk: company pk} for the template's cascade filter.
        Rendered with |json_script, which escapes correctly inside <script>.
        """
        return {str(d.pk): str(d.company_id or '')
                for d in self.fields['department'].queryset}

    def clean(self):
        data = super().clean()

        # Company/department must agree; only the department is stored.
        company, department = data.get('company'), data.get('department')
        if department and company and department.company_id != company.pk:
            self.add_error('department',
                           f'"{department.name}" belongs to '
                           f'{department.company.name if department.company_id else "no company"}, '
                           f'not {company.name}.')
        if company and not department:
            self.add_error('department',
                           f'Select which department of {company.name} — an employee '
                           f'belongs to a company through a department.')

        p1, p2 = data.get('password1'), data.get('password2')
        if p1 or p2:
            if p1 != p2:
                self.add_error('password2', 'The two passwords do not match.')
            else:
                try:
                    validate_password(p1, self.instance)
                except ValidationError as exc:
                    self.add_error('password1', exc)
        elif not self.instance.pk:
            self.add_error('password1', 'A password is required for a new user.')
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get('password1')
        if password:
            user.set_password(password)
        user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.user       = user          # bind, so sync_auth_flags updates `user`
        profile.role       = self.cleaned_data['role']
        profile.department = self.cleaned_data.get('department')
        profile.save(update_fields=['role', 'department'])
        profile.sync_auth_flags()
        return user


class ITPasswordResetForm(forms.Form):
    """Administrative password reset — no old password required."""

    password1 = forms.CharField(label='New Password', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirm New Password', widget=forms.PasswordInput)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        data = super().clean()
        p1, p2 = data.get('password1'), data.get('password2')
        if p1 and p2:
            if p1 != p2:
                self.add_error('password2', 'The two passwords do not match.')
            else:
                try:
                    validate_password(p1, self.user)
                except ValidationError as exc:
                    self.add_error('password1', exc)
        return data

    def save(self):
        self.user.set_password(self.cleaned_data['password1'])
        self.user.save(update_fields=['password'])
        return self.user


# ── Companies & Departments ───────────────────────────────────────────────────

class CompanyForm(forms.ModelForm):
    """
    Create / edit a company. A company always contains at least one department,
    so creating one asks for its first department in the same step — the two
    are saved together in save(). Editing an existing company does not show the
    field; its departments are managed on the Companies / Departments pages.
    """

    first_department = forms.CharField(
        label='First Department', max_length=120,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Engineering'}),
        help_text='Every company contains at least one department — it is '
                  'created together with the company. More can be added later.',
    )

    class Meta:
        model  = Company
        fields = ['name', 'name_ar', 'code', 'description', 'is_active']
        widgets = {
            'name':        forms.TextInput(attrs={'placeholder': 'e.g. AJi Group'}),
            'name_ar':     forms.TextInput(attrs={'placeholder': 'e.g. مجموعة أجي', 'dir': 'rtl'}),
            'code':        forms.TextInput(attrs={'placeholder': 'e.g. AJI', 'style': 'width:140px;'}),
            'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'What this company does'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            del self.fields['first_department']

    def clean_code(self):
        return (self.cleaned_data.get('code') or '').upper().strip()

    def save(self, commit=True):
        company = super().save(commit=commit)
        first = (self.cleaned_data.get('first_department') or '').strip()
        if commit and first and not company.departments.exists():
            Department.objects.create(company=company, name=first)
        return company


class DepartmentForm(forms.ModelForm):
    class Meta:
        model  = Department
        fields = ['company', 'name', 'name_ar', 'code', 'head', 'description', 'is_active']
        widgets = {
            'name':        forms.TextInput(attrs={'placeholder': 'e.g. Engineering'}),
            'name_ar':     forms.TextInput(attrs={'placeholder': 'e.g. الهندسة', 'dir': 'rtl'}),
            'code':        forms.TextInput(attrs={'placeholder': 'e.g. ENG', 'style': 'width:140px;'}),
            'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'What this department does'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Required in the form even though the column tolerates NULL (legacy
        # rows) — every department managed through the UI belongs to a company.
        self.fields['company'].queryset = Company.objects.filter(is_active=True).order_by('name')
        self.fields['company'].required = True
        self.fields['company'].empty_label = '— Select company —'
        self.fields['head'].queryset = User.objects.filter(is_active=True).order_by('username')
        self.fields['head'].empty_label = '— No head assigned —'
        self.fields['head'].label_from_instance = _user_label
        self.fields['head'].required = False

    def clean_code(self):
        return (self.cleaned_data.get('code') or '').upper().strip()

    def clean(self):
        data = super().clean()
        company, name = data.get('company'), (data.get('name') or '').strip()
        if company and name:
            qs = Department.objects.filter(company=company, name__iexact=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error('name', f'"{company.name}" already has a department with this name.')
        return data

    def save(self, commit=True):
        dept = super().save(commit=commit)
        # The head is by definition a member of the department they lead.
        if commit and dept.head_id:
            profile, _ = UserProfile.objects.get_or_create(user=dept.head)
            if profile.department_id != dept.pk:
                profile.department = dept
                profile.save(update_fields=['department'])
        return dept


class DepartmentHeadForm(forms.Form):
    """Change just the head of a department, from its detail page."""

    head = forms.ModelChoiceField(
        queryset=User.objects.none(), required=False,
        empty_label='— No head assigned —', label='Department Head',
    )

    def __init__(self, *args, department=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.department = department
        self.fields['head'].queryset = User.objects.filter(is_active=True).order_by('username')
        self.fields['head'].label_from_instance = _user_label
        if department:
            self.fields['head'].initial = department.head_id


class MoveUserForm(forms.Form):
    """Move one user into a department (or out of all of them)."""

    user = forms.ModelChoiceField(queryset=User.objects.none(), label='User')
    department = forms.ModelChoiceField(
        queryset=Department.objects.none(), required=False,
        empty_label='— No department —', label='Move To',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = User.objects.filter(is_active=True).order_by('username')
        self.fields['user'].label_from_instance = _user_label
        self.fields['department'].queryset = Department.objects.filter(is_active=True)
        self.fields['department'].label_from_instance = lambda d: d.name


# ── Backups ───────────────────────────────────────────────────────────────────

class BackupCreateForm(forms.Form):
    include_database = forms.BooleanField(initial=True, required=False, label='Database')
    include_media    = forms.BooleanField(initial=True, required=False, label='Uploaded files')
    include_config   = forms.BooleanField(initial=True, required=False, label='System configuration')
    include_code     = forms.BooleanField(initial=True, required=False, label='Application code')
    include_docs     = forms.BooleanField(initial=True, required=False, label='Documentation & user guide')
    note             = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={'placeholder': 'Optional label, e.g. "before v2 deploy"'}),
    )

    PARTS = ['include_database', 'include_media', 'include_config',
             'include_code', 'include_docs']

    def clean(self):
        data = super().clean()
        if not any(data.get(part) for part in self.PARTS):
            raise ValidationError('Select at least one thing to back up.')
        return data


class BackupUploadForm(forms.Form):
    """Upload a backup .zip to import into the catalogue for restoring."""

    archive = forms.FileField(
        label='Backup archive (.zip)',
        help_text='A TenderRadar backup archive — created here or on another '
                  'TenderRadar install. It is validated before it can be restored.',
    )

    def clean_archive(self):
        f = self.cleaned_data['archive']
        name = (f.name or '').lower()
        if not name.endswith('.zip'):
            raise ValidationError('Upload a .zip file — this does not look like one.')
        return f


class BackupRestoreForm(forms.Form):
    restore_database = forms.BooleanField(initial=True,  required=False, label='Database')
    restore_media    = forms.BooleanField(initial=True,  required=False, label='Uploaded files')
    restore_config   = forms.BooleanField(initial=False, required=False, label='System configuration')
    restore_code     = forms.BooleanField(initial=False, required=False, label='Application code')
    restore_docs     = forms.BooleanField(initial=False, required=False, label='Documentation & user guide')
    confirm          = forms.CharField(
        label='Type RESTORE to confirm',
        widget=forms.TextInput(attrs={'placeholder': 'RESTORE', 'autocomplete': 'off'}),
    )

    def __init__(self, *args, backup=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Offer only the parts the archive actually contains, so the page
        # cannot promise a restore the engine would have to skip.
        if backup is not None:
            for flag, field in (('includes_database', 'restore_database'),
                                ('includes_media',    'restore_media'),
                                ('includes_config',   'restore_config'),
                                ('includes_code',     'restore_code'),
                                ('includes_docs',     'restore_docs')):
                if not getattr(backup, flag, False):
                    del self.fields[field]

    def clean_confirm(self):
        value = (self.cleaned_data.get('confirm') or '').strip().upper()
        if value != 'RESTORE':
            raise ValidationError('Type RESTORE exactly to confirm this action.')
        return value

    def clean(self):
        data = super().clean()
        parts = ['restore_database', 'restore_media', 'restore_config',
                 'restore_code', 'restore_docs']
        if not any(data.get(part) for part in parts):
            raise ValidationError('Select at least one thing to restore.')
        return data


class BackupScheduleForm(forms.ModelForm):
    class Meta:
        model  = BackupSchedule
        fields = ['enabled', 'frequency', 'hour', 'weekday', 'day_of_month',
                  'includes_database', 'includes_media', 'includes_config',
                  'includes_code', 'includes_docs', 'retention_count']
        widgets = {
            # A clock time is easier to read than a bare 0–23 number.
            'hour':            forms.Select(choices=[(h, f'{h:02d}:00') for h in range(24)]),
            'day_of_month':    forms.NumberInput(attrs={'min': 1, 'max': 28, 'style': 'width:110px;'}),
            'retention_count': forms.NumberInput(attrs={'min': 0, 'max': 200, 'style': 'width:110px;'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['weekday'] = forms.TypedChoiceField(
            label='Weekday', coerce=int, initial=self.instance.weekday,
            choices=[(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'),
                     (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday')],
            help_text='Used when frequency is Weekly.',
        )

    def clean_hour(self):
        hour = self.cleaned_data['hour']
        if not 0 <= hour <= 23:
            raise ValidationError('Hour must be between 0 and 23.')
        return hour

    def clean_day_of_month(self):
        day = self.cleaned_data['day_of_month']
        if not 1 <= day <= 28:
            raise ValidationError('Use 1–28 so the schedule is valid in every month.')
        return day
