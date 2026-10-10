# tenders/it_views.py
"""
System Administration Dashboard — the single entry point for every administrative
operation in TenderRadar.

Reachable by the Super Admin role only; every view in this module is wrapped
in @admin_required, so an unauthorised request gets HTTP 403 regardless of
what the navigation happens to show.

Modules fronted here:
    Users · Roles · Companies · Departments · Department Heads · Backup & Restore
    Settings (e-mail / MS Graph / AI / general) · System Logs · Statistics

Statistics are rendered inline on the dashboard; there is deliberately no
separate stats endpoint, so every administration URL corresponds to a page
reachable from the dashboard.
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import backup as backup_engine
from .it_forms import (
    BackupCreateForm, BackupRestoreForm, BackupScheduleForm, BackupUploadForm,
    CompanyForm, DepartmentForm, DepartmentHeadForm, ITPasswordResetForm,
    ITUserForm, MoveUserForm,
)
from .models import (
    AdminAuditLog, BackupRestore, BackupSchedule, Company, Department,
    NotificationLog, ScrapeLog, SystemBackup, SystemRole, UserLoginLog,
    UserProfile,
)
from .permissions import (
    assignable_roles, can_assign_role, can_edit_user, get_role,
    is_super_admin, admin_required, super_admin_required,
)

logger = logging.getLogger('tenders')
User   = get_user_model()

RECENT_DAYS = 30


# ── Shared helpers ────────────────────────────────────────────────────────────

def _profile(user) -> UserProfile:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    # Bind the caller's User instance so sync_auth_flags() writes the new flags
    # onto the object the view is already holding, not a second copy of it.
    profile.user = user
    return profile


def _audit(request, action, target='', detail=''):
    AdminAuditLog.record(request, action, target=target, detail=detail)


def _safe_next(request, fallback: str) -> str:
    """
    Honour a `next` form field only when it is a same-site path — never let a
    posted value bounce the admin off to another host.
    """
    nxt = (request.POST.get('next') or '').strip()
    if nxt.startswith('/') and not nxt.startswith('//'):
        return nxt
    return fallback


def _active_super_admin_count(exclude_pk=None) -> int:
    qs = User.objects.filter(is_active=True, is_superuser=True)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.count()


def _guard_last_super_admin(request, user) -> bool:
    """
    Refuse an operation that would leave the system with no active Super Admin.
    Returns True when the caller must stop.
    """
    if is_super_admin(user) and _active_super_admin_count(exclude_pk=user.pk) == 0:
        messages.error(request, 'This is the last active Super Admin — the system must always have one.')
        return True
    return False


def _guard_target(request, target) -> None:
    """
    Raise HTTP 403 when the acting user may not administer `target`.

    This is an authorisation failure, not a validation one, so it answers the
    same way as the route decorators rather than bouncing back with a message.
    """
    if not can_edit_user(request.user, target):
        message = 'Only a Super Admin can modify another Super Admin.'
        _audit(request, AdminAuditLog.Action.ACCESS_DENIED,
               target=getattr(target, 'username', target), detail=message)
        raise PermissionDenied(message)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@admin_required
def it_dashboard(request):
    """System overview, user/department/backup statistics, quick actions."""
    now    = timezone.now()
    recent = now - timezone.timedelta(days=RECENT_DAYS)

    users        = User.objects.select_related('profile', 'profile__department')
    total_users  = users.count()
    active_users = users.filter(is_active=True).count()

    departments = (Department.objects
                   .select_related('head')
                   .annotate(employees=Count('member_profiles', distinct=True))
                   .order_by('-employees', 'name'))
    dept_list = list(departments)

    role_counts = {
        SystemRole.SUPER_ADMIN: users.filter(is_superuser=True, is_active=True).count(),
    }
    role_counts[SystemRole.USER] = max(
        active_users - role_counts[SystemRole.SUPER_ADMIN], 0
    )

    last_backup = (SystemBackup.objects
                   .filter(status=SystemBackup.Status.SUCCESS)
                   .order_by('-created_at').first())
    schedule = BackupSchedule.get()

    ctx = {
        # System overview
        'total_users':        total_users,
        'active_users':       active_users,
        'inactive_users':     total_users - active_users,
        'total_companies':    Company.objects.count(),
        'total_departments':  len(dept_list),
        'department_heads':   sum(1 for d in dept_list if d.head_id),
        'unassigned_users':   users.filter(is_active=True, profile__department__isnull=True).count(),
        'system_status':      _system_status(),
        'last_backup':        last_backup,
        'next_backup_at':     schedule.next_run_at(),
        'schedule':           schedule,

        # User statistics
        'role_counts':        role_counts,
        'departments':        dept_list,
        'max_dept_employees': max([d.employees for d in dept_list], default=0) or 1,
        'recent_users':       users.order_by('-date_joined')[:6],
        'recently_active':    users.filter(last_login__isnull=False).order_by('-last_login')[:6],
        'new_user_count':     users.filter(date_joined__gte=recent).count(),

        # Department statistics
        'empty_departments':  [d for d in dept_list if d.employees == 0],
        'headless_departments': [d for d in dept_list if not d.head_id],

        # Backup status
        'backup_disk':        backup_engine.disk_usage(),
        'restore_history':    (BackupRestore.objects
                               .select_related('backup', 'restored_by')[:5]),
        'backup_count':       SystemBackup.objects.count(),

        # Activity
        'recent_actions':     (AdminAuditLog.objects.select_related('actor')[:8]),
        'recent_days':        RECENT_DAYS,
    }
    return render(request, 'it/dashboard.html', ctx)


def _system_status() -> dict:
    """Cheap health probe shown on the dashboard tile."""
    from django.db import connection

    status = {'database': 'down', 'scheduler': 'unknown', 'ok': False}
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
        status['database'] = 'up'
    except Exception as exc:                                  # pragma: no cover
        logger.warning(f'[it] database probe failed: {exc}')

    last_scrape = ScrapeLog.objects.order_by('-started_at').first()
    if last_scrape:
        age_hours = (timezone.now() - last_scrape.started_at).total_seconds() / 3600
        status['scheduler']      = 'up' if age_hours < 24 else 'stale'
        status['last_scrape_at'] = last_scrape.started_at
    status['ok'] = status['database'] == 'up'
    return status


# ── User management ───────────────────────────────────────────────────────────

@admin_required
def user_list(request):
    """All users, with status / role / department filters and search."""
    status_filter   = request.GET.get('status', '')
    role_filter     = request.GET.get('role', '')
    dept_filter     = request.GET.get('department', '')
    company_filter  = request.GET.get('company', '')
    query           = request.GET.get('q', '').strip()

    qs = (User.objects
          .select_related('profile', 'profile__department', 'profile__department__company')
          .order_by('username'))

    if status_filter == 'active':
        qs = qs.filter(is_active=True)
    elif status_filter == 'inactive':
        qs = qs.filter(is_active=False)

    if role_filter == SystemRole.SUPER_ADMIN:
        qs = qs.filter(is_superuser=True)
    elif role_filter == SystemRole.USER:
        qs = qs.exclude(is_superuser=True)

    if dept_filter == 'none':
        qs = qs.filter(profile__department__isnull=True)
    elif dept_filter:
        qs = qs.filter(profile__department__pk=dept_filter)

    if company_filter:
        qs = qs.filter(profile__department__company__pk=company_filter)

    if query:
        qs = qs.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query)
            | Q(last_name__icontains=query) | Q(email__icontains=query)
        )

    rows = [{
        'user':        u,
        'role':        get_role(u),
        'role_label':  dict(SystemRole.choices).get(get_role(u), '—'),
        'department':  getattr(getattr(u, 'profile', None), 'department', None),
        'company':     getattr(getattr(u, 'profile', None), 'company', None),
        'fb_alerts':   getattr(getattr(u, 'profile', None), 'notify_feedback_email', False),
        'editable':    can_edit_user(request.user, u),
        'is_self':     u.pk == request.user.pk,
    } for u in qs]

    return render(request, 'it/users.html', {
        'rows':          rows,
        'departments':   Department.objects.select_related('company'),
        'companies':     Company.objects.order_by('name'),
        'roles':         SystemRole.choices,
        'status_filter': status_filter,
        'role_filter':   role_filter,
        'dept_filter':   dept_filter,
        'company_filter': company_filter,
        'query':         query,
        'total_count':   User.objects.count(),
        'active_count':  User.objects.filter(is_active=True).count(),
        'inactive_count': User.objects.filter(is_active=False).count(),
    })


@admin_required
def user_create(request):
    form = ITUserForm(request.POST or None, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        _audit(request, AdminAuditLog.Action.USER_CREATE, user.username,
               f'role={form.cleaned_data["role"]} department={form.cleaned_data.get("department") or "—"}')
        messages.success(request, f'User "{user.username}" created.')
        return redirect('tenders:it_users')
    return render(request, 'it/user_form.html', {
        'form': form, 'action': 'Add User', 'target_user': None,
    })


@admin_required
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    _guard_target(request, target)

    old_role = get_role(target)
    form = ITUserForm(request.POST or None, instance=target, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        new_role = form.cleaned_data['role']
        if new_role != old_role:
            if not can_assign_role(request.user, new_role):
                messages.error(request, 'You are not allowed to assign that role.')
                return redirect('tenders:it_user_edit', pk=pk)
            if old_role == SystemRole.SUPER_ADMIN and _guard_last_super_admin(request, target):
                return redirect('tenders:it_user_edit', pk=pk)
        if not form.cleaned_data['is_active'] and target.pk == request.user.pk:
            messages.error(request, 'You cannot deactivate your own account.')
            return redirect('tenders:it_user_edit', pk=pk)

        user = form.save()
        _audit(request, AdminAuditLog.Action.USER_UPDATE, user.username,
               f'role={new_role} department={form.cleaned_data.get("department") or "—"}')
        if new_role != old_role:
            _audit(request, AdminAuditLog.Action.ROLE_CHANGE, user.username,
                   f'{old_role} → {new_role}')
        if user.pk == request.user.pk and form.cleaned_data.get('password1'):
            update_session_auth_hash(request, user)
        messages.success(request, f'User "{user.username}" updated.')
        return redirect('tenders:it_users')

    return render(request, 'it/user_form.html', {
        'form': form, 'action': 'Edit User', 'target_user': target,
    })


@admin_required
def user_reset_password(request, pk):
    target = get_object_or_404(User, pk=pk)
    _guard_target(request, target)

    form = ITPasswordResetForm(request.POST or None, user=target)
    if request.method == 'POST' and form.is_valid():
        form.save()
        _audit(request, AdminAuditLog.Action.PASSWORD_RESET, target.username)
        if target.pk == request.user.pk:
            update_session_auth_hash(request, target)
        messages.success(request, f'Password reset for "{target.username}".')
        return redirect('tenders:it_users')

    return render(request, 'it/password_reset.html', {'form': form, 'target_user': target})


@admin_required
@require_POST
def user_toggle_active(request, pk):
    target = get_object_or_404(User, pk=pk)
    _guard_target(request, target)
    if target.pk == request.user.pk:
        messages.error(request, 'You cannot deactivate your own account.')
        return redirect('tenders:it_users')
    if target.is_active and _guard_last_super_admin(request, target):
        return redirect('tenders:it_users')

    target.is_active = not target.is_active
    target.save(update_fields=['is_active'])
    _audit(request,
           AdminAuditLog.Action.USER_ACTIVATE if target.is_active
           else AdminAuditLog.Action.USER_DEACTIVATE,
           target.username)
    messages.success(
        request,
        f'User "{target.username}" {"activated" if target.is_active else "deactivated"}.'
    )
    return redirect(_safe_next(request, 'tenders:it_users'))


@admin_required
@require_POST
def user_toggle_fb_alerts(request, pk):
    """Enable/disable feedback e-mail alerts for a user (notified whenever
    ANY user submits feedback)."""
    target  = get_object_or_404(User, pk=pk)
    profile = _profile(target)
    profile.notify_feedback_email = not profile.notify_feedback_email
    profile.save(update_fields=['notify_feedback_email'])
    if profile.notify_feedback_email:
        if target.email:
            messages.success(request,
                f'"{target.username}" will now receive an e-mail whenever feedback is submitted.')
        else:
            messages.warning(request,
                f'Alerts enabled for "{target.username}", but they have no e-mail '
                f'address — nothing can be delivered until one is set.')
    else:
        messages.success(request, f'Feedback e-mail alerts disabled for "{target.username}".')
    return redirect(_safe_next(request, 'tenders:it_users'))


@admin_required
@require_POST
def user_delete(request, pk):
    target = get_object_or_404(User, pk=pk)
    _guard_target(request, target)
    if target.pk == request.user.pk:
        messages.error(request, 'You cannot delete your own account.')
        return redirect('tenders:it_users')
    if _guard_last_super_admin(request, target):
        return redirect('tenders:it_users')

    username = target.username
    target.delete()
    _audit(request, AdminAuditLog.Action.USER_DELETE, username)
    messages.success(request, f'User "{username}" deleted.')
    return redirect('tenders:it_users')


# ── Role management ───────────────────────────────────────────────────────────

@admin_required
def role_list(request):
    """User Role Management — every user grouped by effective role (two roles)."""
    users = User.objects.select_related('profile', 'profile__department').order_by('username')
    grouped = {code: [] for code, _ in SystemRole.choices}
    for u in users:
        # The template must not re-derive who may be edited — role_change is
        # guarded by can_edit_user, so the form is offered on exactly the rows
        # that guard would accept.
        u.can_edit = can_edit_user(request.user, u)
        grouped.setdefault(get_role(u) or SystemRole.USER, []).append(u)

    return render(request, 'it/roles.html', {
        'groups': [
            {'code': code, 'label': label, 'users': grouped.get(code, [])}
            for code, label in SystemRole.choices
        ],
        'assignable':  assignable_roles(request.user),
        'is_super':    is_super_admin(request.user),
    })


@admin_required
@require_POST
def role_change(request, pk):
    target   = get_object_or_404(User, pk=pk)
    new_role = request.POST.get('role', '')

    _guard_target(request, target)
    if not can_assign_role(request.user, new_role):
        messages.error(request, 'You are not allowed to assign that role.')
        return redirect('tenders:it_roles')
    if target.pk == request.user.pk and new_role == SystemRole.USER:
        messages.error(request, 'You cannot remove your own administrative access.')
        return redirect('tenders:it_roles')

    old_role = get_role(target)
    if old_role == new_role:
        return redirect('tenders:it_roles')
    if old_role == SystemRole.SUPER_ADMIN and _guard_last_super_admin(request, target):
        return redirect('tenders:it_roles')

    profile = _profile(target)
    profile.role = new_role
    profile.save(update_fields=['role'])
    profile.sync_auth_flags()

    _audit(request, AdminAuditLog.Action.ROLE_CHANGE, target.username, f'{old_role} → {new_role}')
    messages.success(request, f'"{target.username}" is now {dict(SystemRole.choices)[new_role]}.')
    return redirect('tenders:it_roles')


# ── Department management ─────────────────────────────────────────────────────

@admin_required
def department_list(request):
    departments = (Department.objects
                   .select_related('head', 'company')
                   .annotate(employees=Count('member_profiles', distinct=True))
                   .order_by('company__name', 'name'))
    return render(request, 'it/departments.html', {
        'departments':      departments,
        'companies':        Company.objects.order_by('name'),
        'total_employees':  UserProfile.objects.filter(department__isnull=False).count(),
        'unassigned':       UserProfile.objects.filter(department__isnull=True).count(),
        'move_form':        MoveUserForm(),
    })


# ── Company management ────────────────────────────────────────────────────────

@admin_required
def company_list(request):
    """All companies with their departments and employee roll-ups."""
    companies = list(
        Company.objects
        .annotate(dept_count=Count('departments', distinct=True))
        .order_by('name')
    )
    dept_by_company = {}
    for d in (Department.objects.select_related('head')
              .annotate(employees=Count('member_profiles', distinct=True))
              .order_by('name')):
        dept_by_company.setdefault(d.company_id, []).append(d)
    for c in companies:
        c.dept_list = dept_by_company.get(c.pk, [])
        c.employees = sum(d.employees for d in c.dept_list)
    return render(request, 'it/companies.html', {
        'companies':        companies,
        'orphan_departments': dept_by_company.get(None, []),
        'total_employees':  UserProfile.objects.filter(department__isnull=False).count(),
    })


@admin_required
def company_create(request):
    form = CompanyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        company = form.save()
        first = company.departments.first()
        _audit(request, AdminAuditLog.Action.COMPANY_CREATE, company.name,
               f'first department: {first.name if first else "—"}')
        messages.success(
            request,
            f'Company "{company.name}" created with its first department '
            f'"{first.name}".' if first else f'Company "{company.name}" created.'
        )
        return redirect('tenders:it_companies')
    return render(request, 'it/company_form.html', {'form': form, 'action': 'Add Company'})


@admin_required
def company_edit(request, pk):
    company = get_object_or_404(Company, pk=pk)
    form    = CompanyForm(request.POST or None, instance=company)
    if request.method == 'POST' and form.is_valid():
        company = form.save()
        _audit(request, AdminAuditLog.Action.COMPANY_UPDATE, company.name)
        messages.success(request, f'Company "{company.name}" updated.')
        return redirect('tenders:it_companies')
    return render(request, 'it/company_form.html', {
        'form': form, 'action': 'Edit Company', 'company': company,
    })


@admin_required
@require_POST
def company_delete(request, pk):
    company = get_object_or_404(Company, pk=pk)
    # PROTECT on Department.company backs this up at the database level; the
    # check here exists to answer with a message instead of a 500.
    if company.departments.exists():
        messages.error(
            request,
            f'"{company.name}" still has {company.departments.count()} department(s) — '
            f'move or delete them first.'
        )
        return redirect('tenders:it_companies')
    name = company.name
    company.delete()
    _audit(request, AdminAuditLog.Action.COMPANY_DELETE, name)
    messages.success(request, f'Company "{name}" deleted.')
    return redirect('tenders:it_companies')


@admin_required
def department_create(request):
    # "Add one →" on a company card pre-selects that company.
    initial = {}
    if request.method != 'POST' and request.GET.get('company', '').isdigit():
        initial['company'] = request.GET['company']
    form = DepartmentForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        dept = form.save()
        _audit(request, AdminAuditLog.Action.DEPT_CREATE, dept.name,
               f'head={dept.head.username if dept.head else "—"}')
        messages.success(request, f'Department "{dept.name}" created.')
        return redirect('tenders:it_department_detail', pk=dept.pk)
    return render(request, 'it/department_form.html', {'form': form, 'action': 'Add Department'})


@admin_required
def department_edit(request, pk):
    dept     = get_object_or_404(Department, pk=pk)
    old_head = dept.head
    form     = DepartmentForm(request.POST or None, instance=dept)
    if request.method == 'POST' and form.is_valid():
        dept = form.save()
        _audit(request, AdminAuditLog.Action.DEPT_UPDATE, dept.name)
        if dept.head != old_head:
            _audit(request, AdminAuditLog.Action.DEPT_HEAD_CHANGE, dept.name,
                   f'{old_head.username if old_head else "—"} → '
                   f'{dept.head.username if dept.head else "—"}')
        messages.success(request, f'Department "{dept.name}" updated.')
        return redirect('tenders:it_department_detail', pk=dept.pk)
    return render(request, 'it/department_form.html', {
        'form': form, 'action': 'Edit Department', 'dept': dept,
    })


@admin_required
def department_detail(request, pk):
    dept = get_object_or_404(Department.objects.select_related('head'), pk=pk)
    members = (User.objects
               .filter(profile__department=dept)
               .select_related('profile')
               .order_by('username'))
    return render(request, 'it/department_detail.html', {
        'dept':         dept,
        'members':      [{'user': u, 'role_label': dict(SystemRole.choices).get(get_role(u), '—'),
                          'is_head': u.pk == dept.head_id} for u in members],
        'head_form':    DepartmentHeadForm(department=dept),
        'other_depts':  Department.objects.filter(is_active=True).exclude(pk=dept.pk),
        'addable':      (User.objects.filter(is_active=True)
                         .exclude(profile__department=dept).order_by('username')),
    })


@admin_required
@require_POST
def department_set_head(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    form = DepartmentHeadForm(request.POST, department=dept)
    if not form.is_valid():
        messages.error(request, 'Select a valid user.')
        return redirect('tenders:it_department_detail', pk=pk)

    old_head  = dept.head
    new_head  = form.cleaned_data['head']
    dept.head = new_head
    dept.save(update_fields=['head', 'updated_at'])

    if new_head:                      # the head is a member of their department
        profile = _profile(new_head)
        if profile.department_id != dept.pk:
            profile.department = dept
            profile.save(update_fields=['department'])

    _audit(request, AdminAuditLog.Action.DEPT_HEAD_CHANGE, dept.name,
           f'{old_head.username if old_head else "—"} → {new_head.username if new_head else "—"}')
    messages.success(
        request,
        f'{new_head.username} is now head of "{dept.name}".' if new_head
        else f'"{dept.name}" no longer has a head.'
    )
    return redirect('tenders:it_department_detail', pk=pk)


@admin_required
@require_POST
def department_delete(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    name = dept.name
    # SET_NULL on both FKs — members simply become unassigned, nobody is deleted.
    moved = dept.member_profiles.count()
    dept.delete()
    _audit(request, AdminAuditLog.Action.DEPT_DELETE, name, f'{moved} employee(s) unassigned')
    messages.success(
        request,
        f'Department "{name}" deleted.' +
        (f' {moved} employee(s) are now unassigned.' if moved else '')
    )
    return redirect('tenders:it_departments')


@admin_required
@require_POST
def user_move(request):
    """Assign a user to a department, or move them between departments."""
    form = MoveUserForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Select a valid user and department.')
        return redirect(_safe_next(request, 'tenders:it_departments'))

    target  = form.cleaned_data['user']
    dept    = form.cleaned_data['department']
    profile = _profile(target)
    old     = profile.department

    if old and old.head_id == target.pk and (dept is None or dept.pk != old.pk):
        old.head = None                       # a head cannot lead from outside
        old.save(update_fields=['head', 'updated_at'])
        _audit(request, AdminAuditLog.Action.DEPT_HEAD_CHANGE, old.name,
               f'{target.username} → — (moved out of department)')

    profile.department = dept
    profile.save(update_fields=['department'])
    _audit(request, AdminAuditLog.Action.USER_MOVE, target.username,
           f'{old.name if old else "—"} → {dept.name if dept else "—"}')
    messages.success(
        request,
        f'{target.username} moved to "{dept.name}".' if dept
        else f'{target.username} is no longer assigned to a department.'
    )
    return redirect(_safe_next(request, 'tenders:it_departments'))


# ── Backup & restore ──────────────────────────────────────────────────────────

@admin_required
def backup_list(request):
    backups  = SystemBackup.objects.select_related('created_by')
    schedule = BackupSchedule.get()
    return render(request, 'it/backups.html', {
        'backups':       backups[:60],
        'backup_count':  backups.count(),
        'last_success':  backups.filter(status=SystemBackup.Status.SUCCESS).first(),
        'disk':          backup_engine.disk_usage(),
        # Where the archives actually are. Without this the page reports a count
        # and a size but never the folder, so on an unfamiliar machine there is
        # no way to find the files except by guessing.
        'backup_dir':    str(backup_engine.backup_root()),
        'restores':      BackupRestore.objects.select_related('backup', 'restored_by')[:20],
        'create_form':   BackupCreateForm(),
        'upload_form':   BackupUploadForm(),
        'schedule_form': BackupScheduleForm(instance=schedule),
        'schedule':      schedule,
        'next_run_at':   schedule.next_run_at(),
    })


@admin_required
@require_POST
def backup_create(request):
    form = BackupCreateForm(request.POST)
    if not form.is_valid():
        for error in form.non_field_errors():
            messages.error(request, error)
        return redirect('tenders:it_backups')

    record = backup_engine.create_backup(
        request.user,
        include_database=form.cleaned_data['include_database'],
        include_media=form.cleaned_data['include_media'],
        include_config=form.cleaned_data['include_config'],
        include_code=form.cleaned_data['include_code'],
        include_docs=form.cleaned_data['include_docs'],
        note=form.cleaned_data['note'],
    )
    if record.status == SystemBackup.Status.SUCCESS:
        _audit(request, AdminAuditLog.Action.BACKUP_CREATE, record.filename,
               f'{record.size_display} · {record.contents_display}')
        messages.success(request, f'Backup created — {record.filename} ({record.size_display}).')
    else:
        messages.error(request, f'Backup failed: {record.error_message}')
    return redirect('tenders:it_backups')


@admin_required
def backup_download(request, pk):
    record = get_object_or_404(SystemBackup, pk=pk)
    if not record.exists_on_disk:
        raise Http404('This backup is no longer on disk.')
    _audit(request, AdminAuditLog.Action.BACKUP_DOWNLOAD, record.filename)
    return FileResponse(
        open(record.file_path, 'rb'),
        as_attachment=True, filename=record.filename,
        content_type='application/zip',
    )


def _serve_doc(request, filename: str, label: str):
    """
    Hand out a PDF from docs/ to an administrator, and record who took it.

    Served through a view rather than from static/ or media/ on purpose: both of
    those are handed out by WhiteNoise with no authentication, and these
    documents are marked *Confidential — internal use only*. They describe the
    source code, the architecture and how credentials are handled.

    The filename is a fixed argument from the URLconf, never anything the caller
    supplies, so there is no path to traverse out of docs/.
    """
    path = settings.BASE_DIR / 'docs' / filename
    if not path.is_file():
        raise Http404(f'{label} is not installed.')
    _audit(request, AdminAuditLog.Action.DOC_DOWNLOAD, path.name,
           f'{label} · {path.stat().st_size / 1048576:.1f} MB')
    return FileResponse(
        open(path, 'rb'),
        as_attachment=True, filename=path.name,
        content_type='application/pdf',
    )


@admin_required
def documentation_download(request):
    """The full developer documentation — 43 pages."""
    return _serve_doc(request, 'TenderRadar_Developer_Documentation.pdf',
                      'Developer documentation')


@login_required
def user_guide_download(request):
    """
    The user guide — every screen, written for people who use the system daily.

    The one document here that is NOT administrators-only: its stated audience is
    "all TenderRadar users — staff, team leaders and administrators", so every
    signed-in user may download it. Its URL lives outside the /it/ prefix for the
    same reason.

    Still not public, and still recorded: the guide is marked confidential and
    describes internal systems, access levels and business processes, so an
    anonymous visitor is sent to the login page and each download is written to
    the audit trail.
    """
    return _serve_doc(request, 'TenderRadar_User_Guide.pdf', 'User guide')


@admin_required
def add_source_guide_download(request):
    """
    Section 7 on its own — "Adding a new portal with AI help".

    The procedure for adding a new tender source. Split out by
    docs/make_add_source_guide.py so nobody has to download 43 pages to read
    seven; the content is identical to that section of the full document.
    """
    return _serve_doc(request, 'TenderRadar_Adding_A_New_Source.pdf',
                      'Adding a new source')


@admin_required
def backup_restore(request, pk):
    """
    Restore a backup over the live system.

    Every restore is recorded in the audit trail with the account that ran it,
    and a safety copy of the current state is always taken first.
    """
    record = get_object_or_404(SystemBackup, pk=pk)
    form   = BackupRestoreForm(request.POST or None, backup=record)

    if request.method == 'POST' and form.is_valid():
        if not record.exists_on_disk:
            messages.error(request, 'This backup is no longer on disk.')
            return redirect('tenders:it_backups')

        audit = backup_engine.restore_backup(
            record, request.user,
            restore_database=form.cleaned_data['restore_database'],
            restore_media=form.cleaned_data['restore_media'],
            restore_config=form.cleaned_data['restore_config'],
            restore_code=form.cleaned_data.get('restore_code', False),
            restore_docs=form.cleaned_data.get('restore_docs', False),
        )
        _audit(request, AdminAuditLog.Action.BACKUP_RESTORE, record.filename,
               f'status={audit.status}')
        if audit.status == BackupRestore.Status.SUCCESS:
            messages.success(
                request,
                f'Restored {record.filename}. A safety copy of the previous state was '
                f'taken first. Restart the app server so every process picks up the '
                f'restored data' +
                (' — REQUIRED after a code restore: the running process still executes '
                 'the old code until restarted.' if audit.restored_code else '.')
            )
        else:
            messages.error(request, f'Restore failed: {audit.error_message}')
        return redirect('tenders:it_backups')

    return render(request, 'it/backup_restore.html', {'backup': record, 'form': form})


@admin_required
@require_POST
def backup_upload(request):
    """
    Import an uploaded backup .zip into the catalogue so it can be restored.

    The upload itself changes nothing — it is validated (real zip, intact CRCs,
    a TenderRadar manifest, no unsafe paths), stored under BACKUP_ROOT and
    indexed as a SystemBackup row. Restoring it then goes through the same
    restore_backup() path as any other archive, safety copy included.
    """
    form = BackupUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return redirect('tenders:it_backups')

    record, error = backup_engine.import_backup(form.cleaned_data['archive'], request.user)
    if error:
        messages.error(request, f'Upload rejected: {error}')
        return redirect('tenders:it_backups')

    _audit(request, AdminAuditLog.Action.BACKUP_UPLOAD, record.filename,
           f'{record.size_display} · {record.contents_display}')
    messages.success(
        request,
        f'Archive imported as {record.filename} ({record.size_display}, '
        f'{record.contents_display}). Use Restore next to it when ready.'
    )
    return redirect('tenders:it_backups')


@admin_required
@require_POST
def backup_delete(request, pk):
    record   = get_object_or_404(SystemBackup, pk=pk)
    filename = record.filename
    backup_engine.delete_backup(record)
    _audit(request, AdminAuditLog.Action.BACKUP_DELETE, filename)
    messages.success(request, f'Backup "{filename}" deleted.')
    return redirect('tenders:it_backups')


@admin_required
@require_POST
def backup_schedule(request):
    schedule = BackupSchedule.get()
    form     = BackupScheduleForm(request.POST, instance=schedule)
    if form.is_valid():
        form.save()
        _audit(request, AdminAuditLog.Action.SCHEDULE_UPDATE, 'Backup schedule',
               f'enabled={schedule.enabled} {schedule.frequency} @ {schedule.hour:02d}:00')
        messages.success(request, 'Backup schedule saved.')
    else:
        messages.error(request, 'Could not save the schedule — check the values.')
    return redirect('tenders:it_backups')


# ── Settings & logs ───────────────────────────────────────────────────────────

@admin_required
def it_settings(request):
    """
    Settings hub. Each card links to the existing settings screen rather than
    duplicating it, so there is exactly one implementation of each.
    """
    from .models import IntegrationSettings, SiteSettings

    try:
        intg = IntegrationSettings.get()
    except Exception:
        intg = None
    try:
        site = SiteSettings.get()
    except Exception:
        site = None

    return render(request, 'it/settings.html', {'intg': intg, 'site': site})


@admin_required
def notification_detail(request, pk):
    """
    One outgoing notification, message body included.

    The body is rendered inside a sandboxed iframe (no scripts, no form
    submission) because it embeds scraped tender titles — our template, but
    not entirely our text.
    """
    note = get_object_or_404(NotificationLog.objects.select_related('triggered_by'), pk=pk)
    return render(request, 'it/notification_detail.html', {'note': note})


LOGS_PER_PAGE = 50


@admin_required
def system_logs(request):
    """
    Administrative audit trail, sign-in history and scraper runs.

    Paginated rather than truncated. The tabs used to slice the newest 200-300
    rows and stop, which silently hid the rest of the audit trail — the one table
    where "we only kept the recent ones" is least acceptable.
    """
    from django.core.paginator import Paginator

    tab = request.GET.get('tab', 'admin')
    ctx = {'tab': tab}

    if tab == 'logins':
        qs = UserLoginLog.objects.select_related('user')
    elif tab == 'scrapers':
        qs = ScrapeLog.objects.select_related('source')
    elif tab == 'notifications':
        channel = request.GET.get('channel', '')
        status  = request.GET.get('status', '')
        qs = NotificationLog.objects.select_related('triggered_by')
        if channel:
            qs = qs.filter(channel=channel)
        if status:
            qs = qs.filter(status=status)
        ctx['channel_filter'] = channel
        ctx['status_filter']  = status
        ctx['channels'] = NotificationLog.Channel.choices
        ctx['statuses'] = NotificationLog.Status.choices
    else:
        action = request.GET.get('action', '')
        qs = AdminAuditLog.objects.select_related('actor')
        if action:
            qs = qs.filter(action=action)
        ctx['action_filter'] = action
        ctx['actions'] = AdminAuditLog.Action.choices

    # Each model orders by a timestamp, which is not unique — bulk actions share
    # a second. Without a tiebreaker the database is free to order ties however
    # it likes on each query, so a row can appear on two pages or on none. Adding
    # the primary key makes the sequence total, and paging stable.
    qs = qs.order_by(*list(qs.model._meta.ordering or []), '-pk')

    # get_page (not page()) so ?page=abc or ?page=999 lands on a valid page
    # instead of raising — these URLs get shared and bookmarked.
    page_obj = Paginator(qs, LOGS_PER_PAGE).get_page(request.GET.get('page'))
    ctx['page_obj'] = page_obj
    # The templates iterate the tab-specific name; keep those working.
    ctx['logins' if tab == 'logins' else
        'scrapes' if tab == 'scrapers' else
        'notifications' if tab == 'notifications' else 'entries'] = page_obj.object_list

    ctx['counts'] = {
        'admin':         AdminAuditLog.objects.count(),
        'logins':        UserLoginLog.objects.count(),
        'scrapers':      ScrapeLog.objects.count(),
        'notifications': NotificationLog.objects.count(),
    }
    return render(request, 'it/logs.html', ctx)


# ── Newspaper & News runs (one page) ─────────────────────────────────────
# Both features write their own run log — newspaper editions and MagazineRun
# rows. Reviewing them meant visiting two pages, so they are merged here into
# one chronological list.
#
# Each app is imported inside try/except on purpose: both promise that deleting
# the app folder leaves TenderRadar working, and this page has to honour that.
# With an app gone, its rows simply do not appear.

def _newspaper_run_rows():
    """Newspaper editions as unified rows, or [] when the app is absent."""
    try:
        from django.db.models import Count, Q as _Q

        from newspaper_tenders.models import NewspaperEdition
    except Exception:
        return []

    try:
        editions = (NewspaperEdition.objects
                    .annotate(
                        files_total=Count('files', distinct=True),
                        files_done=Count('files', distinct=True,
                                         filter=_Q(files__status='DONE')),
                        files_failed=Count('files', distinct=True,
                                           filter=_Q(files__status='FAILED')),
                        # Al-Rai pages headed إعلانات قضائية, closed unread by
                        # newspaper_tenders/page_gate.py (the note is its marker).
                        files_court=Count('files', distinct=True,
                                          filter=_Q(files__error_message__startswith=
                                                    'Court-notices page')),
                        tenders_total=Count('tender_meta', distinct=True),
                        tenders_aec=Count('tender_meta', distinct=True,
                                          filter=_Q(tender_meta__tender__is_aec=True)),
                    )
                    .order_by('-edition_date')[:200])
    except Exception as exc:                                   # pragma: no cover
        logger.warning(f'[runs] newspaper rows unavailable: {exc}')
        return []

    rows = []
    for e in editions:
        rows.append({
            'kind':        'newspaper',
            'kind_label':  'Newspaper',
            'icon':        'newspaper',
            'source':      e.source_display_en,
            'when':        e.updated_at or e.discovered_at,
            'subtitle':    f'Edition {e.edition_date:%d %b %Y}',
            'status':      e.status,
            'status_label': e.get_status_display(),
            'trigger':     '',
            'found':       e.tenders_total,
            'saved':       e.tenders_total,
            'aec':         e.tenders_aec,
            'detail':      (f'{e.files_done}/{e.files_total} PDFs'
                            + (f' · {e.files_court} court pages skipped' if e.files_court else '')
                            + (f' · {e.files_failed} failed' if e.files_failed else '')),
            # Was hardcoded to '' while the template renders {{ r.error }} and
            # the magazine row passes its own — so a FAILED edition showed the
            # red badge with no cause anywhere on the page.
            'error':       e.error_message or '',
            'link':        e.article_url or '',
            'retry_url':   f'/newspaper-tenders/runs/{e.pk}/retry/',
            'retry_hint':  ('Re-download and process only the failed or pending '
                            'PDFs of this edition'),
            # A newspaper edition needs BOTH actions, because a retry
            # deliberately skips pages already marked DONE (pipeline.py, the
            # `edition.files.exclude(status=DONE)` loop). Re-reading those is
            # the whole point of a rescan: it is how an edition processed
            # before an AI-service improvement picks up what it originally
            # missed (the ad photos, most often). Same endpoint, force=1.
            #
            # Two limits worth knowing before promising more in the UI:
            #  * force resets every file row to PENDING, but the pipeline's
            #    SHA256 gate still skips a page whose file_hash is empty
            #    because it was previously logged as a duplicate of another
            #    page. Those pages are not re-read. (None exist in this
            #    database today, so it is a latent case, not a live one.)
            #  * "never duplicated" holds only while the AI returns the same
            #    title/entity/deadline, since those three fields ARE the
            #    external_id. If a re-read drifts, the row is new rather than
            #    updated, and cross-source dedup cannot catch it because it
            #    excludes the tender's own source.
            'rescan_url':  f'/newspaper-tenders/runs/{e.pk}/retry/',
            # Reset: stop whatever is running, delete every tender this edition
            # produced, and put its pages back to pending so Retry reads them
            # from scratch. The edition row itself survives — deleting it would
            # take the Retry button with it, and for any date but today there
            # is no way to bring an edition back from the UI.
            'reset_url':   f'/newspaper-tenders/runs/{e.pk}/reset/',
            # Named in the confirm dialog, so nobody discards work blind.
            'reset_count': e.tenders_total,
        })
    return rows


def _magazine_run_rows():
    """MagazineRun rows as unified rows, or [] when the app is absent."""
    try:
        from magazine_tenders.models import MagazineRun
    except Exception:
        return []

    try:
        # Only sources still in the registry. An unregistered source leaves
        # its old run rows behind, and a background sweep that finishes after
        # the deregistration can even write a fresh one — which is how a
        # parked source reappeared on this page. Filtering here means the page
        # cannot show a source the feature no longer reads.
        from magazine_tenders import sources
        registered = sources.all_codes()
        if not registered:
            return []
        runs = MagazineRun.objects.filter(magazine__in=registered)[:200]
    except Exception as exc:                                   # pragma: no cover
        logger.warning(f'[runs] magazine rows unavailable: {exc}')
        return []

    rows = []
    for r in runs:
        rows.append({
            'kind':        'news',
            'kind_label':  'News',
            'icon':        'magazine',
            'source':      r.magazine_display_en,
            'when':        r.finished_at or r.started_at,
            'subtitle':    f'{r.started_at:%d %b %Y · %H:%M}',
            'status':      r.status,
            'status_label': r.get_status_display(),
            'trigger':     r.get_trigger_display(),
            'found':       r.found,
            'saved':       r.saved_new,
            'aec':         r.aec,
            'detail':      (f'{r.saved_new} new · {r.saved_updated} updated'
                            + (f' · {r.skipped} skipped' if r.skipped else '')),
            'error':       r.error_message,
            'link':        '',
            # reverse(), not a hardcoded path: this string survived the
            # section being remounted from /magazine-tenders/ to /news/ only
            # because it was caught by hand. A literal here breaks silently —
            # the button posts to a 404 and the toast says "could not start".
            'retry_url':   reverse('magazine_tenders:retry_run', args=[r.pk]),
            'retry_hint':  ('Run this news source again now — the sweep re-reads '
                            'the whole feed'),
            # No rescan button for magazines, and none is missing: a magazine
            # retry already re-fetches the entire feed and re-reads every item,
            # so it IS a full rescan. Only the newspaper pipeline keeps
            # per-page state that a retry skips.
            'rescan_url':  '',
            # No reset either: a magazine run is a feed sweep, not a stored
            # per-edition artefact, so there is nothing to empty out.
            'reset_url':   '',
            'reset_count': 0,
        })
    return rows


@admin_required
def feature_runs(request):
    """
    /it/runs/ — every Newspaper and Magazine run, newest first.

    Filterable by feature with ?kind=newspaper|magazine so a long history stays
    usable; the default shows both interleaved by time, which is what makes the
    single page worth having.
    """
    kind = (request.GET.get('kind') or '').strip().lower()
    # 'magazine' was the value before the section was renamed, and it appeared
    # in the address bar, so it stays valid — someone has it bookmarked.
    if kind == 'magazine':
        kind = 'news'

    # Both sets are always fetched, and the counts come from them BEFORE the
    # filter is applied. Counting the filtered rows instead — which is what
    # this did — meant every tab reported the tab you were already on:
    # clicking NEWSPAPER showed "News (0)" and clicking News showed
    # "NEWSPAPER (0)", so the tabs claimed the other feature had no runs at
    # all. A tab's number has to describe where it LEADS, not where you are.
    newspaper_rows = _newspaper_run_rows()
    magazine_rows  = _magazine_run_rows()

    counts = {
        'newspaper': len(newspaper_rows),
        'news':      len(magazine_rows),
    }
    total = counts['newspaper'] + counts['news']

    rows = []
    if kind != 'news':
        rows += newspaper_rows
    if kind != 'newspaper':
        rows += magazine_rows

    # A run with no timestamp at all must not crash the sort or vanish.
    rows.sort(key=lambda r: (r['when'] is not None, r['when']), reverse=True)

    return render(request, 'it/runs.html', {
        'it_section': 'feature_runs',
        'rows':       rows,
        'kind':       kind,
        'counts':     counts,
        # The whole history, so "All" means all rather than "all of what is
        # currently shown".
        'total':      total,
        'shown':      len(rows),
    })
