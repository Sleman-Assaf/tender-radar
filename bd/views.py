# bd/views.py
# Path: TenderRadar/bd/views.py

import json
import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .ajiai_client import extract_cv
from .models import (CVBatch, CVBatchItem, CVDocument, Discipline, Engineer,
                     EngineerStatus, HistoricalProject, ProjectDocument,
                     ProjectSector, ProjectStatus, SECTOR_CATEGORY_MAP)
from django.contrib.auth.decorators import login_required

from .permissions import bd_required, is_bd
from .text_extract import extract_text

logger = logging.getLogger('tenders')

MAX_UPLOAD = 20 * 1024 * 1024  # 20 MB


# ─────────────────────────────────────────────────────────────────────────────
# CV Pool
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def cv_list(request):
    qs = Engineer.objects.all()

    q          = request.GET.get('q', '').strip()
    discipline = request.GET.get('discipline', '').strip()
    status     = request.GET.get('status', '').strip()
    min_years  = request.GET.get('min_years', '').strip()

    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(current_position__icontains=q)
                       | Q(profession__icontains=q) | Q(cv_text__icontains=q)
                       | Q(countries_experience__icontains=q))
    if discipline:
        qs = qs.filter(discipline=discipline)
    if status:
        qs = qs.filter(status=status)
    if min_years.isdigit():
        qs = qs.filter(years_experience__gte=int(min_years))
    if request.GET.get('review') == '1':
        qs = qs.filter(needs_review=True)

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'bd/cv_list.html', {
        'page':        page,
        'total':       paginator.count,
        'review_count': Engineer.objects.filter(needs_review=True).count(),
        'disciplines': Discipline.choices,
        'statuses':    EngineerStatus.choices,
        'f': {'q': q, 'discipline': discipline, 'status': status,
              'min_years': min_years, 'review': request.GET.get('review', '')},
    })


@login_required
def cv_upload(request):
    """Step 1 of the wizard: upload any CV file (DOCX/PDF), extract its text,
    send it to AJiAI for parsing, then show the pre-filled review form."""
    if request.method == 'POST':
        f = request.FILES.get('cv_file')
        if not f:
            messages.error(request, 'Please choose a CV file (.docx or .pdf).')
            return redirect('bd:cv_upload')
        if f.size > MAX_UPLOAD:
            messages.error(request, 'File too large — max 20 MB.')
            return redirect('bd:cv_upload')

        cv_text = extract_text(f)
        if not cv_text:
            messages.error(request,
                'Could not extract any text from this file. If it is a scanned '
                'PDF, please provide a text-based CV.')
            return redirect('bd:cv_upload')

        parsed = extract_cv(cv_text)
        if not parsed:
            messages.warning(request,
                'AJiAI could not parse this CV automatically — please fill in '
                'the fields manually below.')

        # Stash upload + extraction in the session for the review step
        request.session['bd_cv_draft'] = {
            'cv_text':  cv_text[:60000],
            'parsed':   parsed,
            'filename': f.name,
        }
        # Extract the profile photo (if any) before storing
        from .photo_extract import extract_photo
        photo_bytes, photo_ext = extract_photo(f)

        # Persist the raw file temporarily via storage so it survives to save
        from django.core.files.storage import default_storage
        from django.core.files.base import ContentFile
        tmp_path = default_storage.save(f'bd/tmp/{f.name}', f)
        request.session['bd_cv_tmp_file'] = tmp_path
        if photo_bytes:
            photo_tmp = default_storage.save(
                f'bd/tmp/photo_{request.user.pk}.{photo_ext}',
                ContentFile(photo_bytes))
            request.session['bd_cv_tmp_photo'] = photo_tmp
        else:
            request.session.pop('bd_cv_tmp_photo', None)
        return redirect('bd:cv_review')

    return render(request, 'bd/cv_upload.html', {})


def _engineer_from_post(request, engineer=None):
    """Populate an Engineer instance from the review-form POST."""
    e = engineer or Engineer()
    e.full_name        = request.POST.get('full_name', '').strip()
    e.current_position = request.POST.get('current_position', '').strip()
    e.profession       = request.POST.get('profession', '').strip()
    e.nationality      = request.POST.get('nationality', '').strip()
    e.date_of_birth    = request.POST.get('date_of_birth', '').strip()
    e.years_with_firm  = request.POST.get('years_with_firm', '').strip()
    e.memberships      = request.POST.get('memberships', '').strip()
    e.countries_experience = request.POST.get('countries_experience', '').strip()
    e.synopsis         = request.POST.get('synopsis', '').strip()
    e.discipline       = request.POST.get('discipline', 'OTHER')
    e.status           = request.POST.get('status', 'ACTIVE')
    e.notes            = request.POST.get('notes', '').strip()
    years = request.POST.get('years_experience', '').strip()
    e.years_experience = int(years) if years.isdigit() else None

    # JSON sections come from hidden textareas maintained by the form JS
    for field in ('education', 'employment', 'experience',
                  'certifications', 'languages'):
        raw = request.POST.get(f'{field}_json', '[]')
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                data = []
        except (json.JSONDecodeError, ValueError):
            data = []
        setattr(e, f'{field}_json', json.dumps(data, ensure_ascii=False))
    return e


def _parsed_to_initial(parsed: dict, cv_text: str) -> dict:
    """Normalise the AJiAI response into template initial values."""
    memberships = parsed.get('memberships') or []
    if isinstance(memberships, list):
        memberships = '\n'.join(str(m) for m in memberships)
    return {
        'full_name':        parsed.get('full_name', ''),
        'current_position': parsed.get('current_position', ''),
        'profession':       parsed.get('profession', ''),
        'nationality':      parsed.get('nationality', ''),
        'date_of_birth':    str(parsed.get('date_of_birth', '') or ''),
        'years_with_firm':  str(parsed.get('years_with_firm', '') or ''),
        'years_experience': parsed.get('years_experience', '') or '',
        'discipline':       parsed.get('discipline', 'OTHER'),
        'memberships':      memberships,
        'countries_experience': parsed.get('countries_experience', ''),
        'synopsis':         parsed.get('synopsis', ''),
        'education_json':      json.dumps(parsed.get('education') or [],      ensure_ascii=False),
        'employment_json':     json.dumps(parsed.get('employment') or [],     ensure_ascii=False),
        'experience_json':     json.dumps(parsed.get('experience') or [],     ensure_ascii=False),
        'certifications_json': json.dumps(parsed.get('certifications') or [], ensure_ascii=False),
        'languages_json':      json.dumps(parsed.get('languages') or [],      ensure_ascii=False),
        'cv_text':          cv_text,
    }


@login_required
def cv_review(request):
    """Step 2: review/edit the AI-parsed fields, then save to the pool and/or
    download the formatted template CV."""
    draft = request.session.get('bd_cv_draft')
    if not draft:
        messages.error(request, 'No CV upload in progress — start again.')
        return redirect('bd:cv_upload')

    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        e = _engineer_from_post(request)
        if not e.full_name:
            messages.error(request, 'Full name is required.')
        else:
            e.cv_text   = draft.get('cv_text', '')
            e.ai_parsed = bool(draft.get('parsed'))
            e.created_by = request.user

            if action == 'format_only':
                # Format without saving to the pool
                photo_tmp = request.session.get('bd_cv_tmp_photo')
                photo_bytes = None
                if photo_tmp:
                    from django.core.files.storage import default_storage
                    try:
                        with default_storage.open(photo_tmp, 'rb') as ph:
                            photo_bytes = ph.read()
                    except Exception:
                        photo_bytes = None
                try:
                    from .cv_template import generate_cv_docx
                    blob = generate_cv_docx(e, photo_bytes=photo_bytes)
                except RuntimeError as exc:
                    messages.error(request, str(exc))
                    return redirect('bd:cv_review')
                resp = HttpResponse(
                    blob,
                    content_type='application/vnd.openxmlformats-officedocument'
                                 '.wordprocessingml.document')
                fname = f"{e.full_name.replace(' ', '_')}_AJi_CV.docx"
                resp['Content-Disposition'] = f'attachment; filename="{fname}"'
                return resp

            # Save to pool (+ attach photo and the original upload)
            photo_tmp = request.session.pop('bd_cv_tmp_photo', None)
            if photo_tmp:
                from django.core.files.storage import default_storage
                from django.core.files.base import ContentFile
                try:
                    with default_storage.open(photo_tmp, 'rb') as ph:
                        ext = photo_tmp.rsplit('.', 1)[-1]
                        e.photo = ContentFile(
                            ph.read(),
                            name=f"{e.full_name.replace(' ', '_')}_photo.{ext}")
                    default_storage.delete(photo_tmp)
                except Exception as exc:
                    logger.warning(f'[bd] could not attach photo: {exc}')
            e.save()
            tmp_path = request.session.pop('bd_cv_tmp_file', None)
            if tmp_path:
                from django.core.files.storage import default_storage
                try:
                    with default_storage.open(tmp_path, 'rb') as fh:
                        from django.core.files.base import ContentFile
                        CVDocument.objects.create(
                            engineer=e, kind=CVDocument.Kind.ORIGINAL,
                            original_name=draft.get('filename', ''),
                            uploaded_by=request.user,
                            file=ContentFile(fh.read(),
                                             name=draft.get('filename', 'cv.docx')),
                        )
                    default_storage.delete(tmp_path)
                except Exception as exc:
                    logger.warning(f'[bd] could not attach original CV file: {exc}')
            request.session.pop('bd_cv_draft', None)
            messages.success(request, f'CV for "{e.full_name}" saved to the pool.')
            return redirect('bd:cv_detail', pk=e.pk)

    initial = _parsed_to_initial(draft.get('parsed') or {}, draft.get('cv_text', ''))
    return render(request, 'bd/cv_form.html', {
        'initial':     initial,
        'engineer':    None,
        'disciplines': Discipline.choices,
        'statuses':    EngineerStatus.choices,
        'mode':        'review',
        'filename':    draft.get('filename', ''),
    })


@login_required
def cv_detail(request, pk):
    engineer = get_object_or_404(Engineer, pk=pk)
    return render(request, 'bd/cv_detail.html', {'e': engineer})


@login_required
def cv_edit(request, pk):
    engineer = get_object_or_404(Engineer, pk=pk)
    if request.method == 'POST':
        e = _engineer_from_post(request, engineer)
        if not e.full_name:
            messages.error(request, 'Full name is required.')
        else:
            e.save()
            messages.success(request, 'CV updated.')
            return redirect('bd:cv_detail', pk=e.pk)

    initial = {
        'full_name': engineer.full_name, 'current_position': engineer.current_position,
        'profession': engineer.profession, 'nationality': engineer.nationality,
        'date_of_birth': engineer.date_of_birth, 'years_with_firm': engineer.years_with_firm,
        'years_experience': engineer.years_experience or '',
        'discipline': engineer.discipline, 'memberships': engineer.memberships,
        'countries_experience': engineer.countries_experience,
        'synopsis': engineer.synopsis,
        'education_json':      engineer.education_json,
        'employment_json':     engineer.employment_json,
        'experience_json':     engineer.experience_json,
        'certifications_json': engineer.certifications_json,
        'languages_json':      engineer.languages_json,
        'cv_text': engineer.cv_text,
    }
    return render(request, 'bd/cv_form.html', {
        'initial':     initial,
        'engineer':    engineer,
        'disciplines': Discipline.choices,
        'statuses':    EngineerStatus.choices,
        'mode':        'edit',
        'filename':    '',
    })


@login_required
def cv_download(request, pk):
    """Generate and download the AJi-template formatted DOCX; also stores it
    as the engineer's FORMATTED document."""
    engineer = get_object_or_404(Engineer, pk=pk)
    try:
        from .cv_template import generate_cv_docx
        blob = generate_cv_docx(engineer)
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect('bd:cv_detail', pk=pk)

    # Refresh the stored formatted copy
    try:
        from django.core.files.base import ContentFile
        fname = f"{engineer.full_name.replace(' ', '_')}_AJi_CV.docx"
        engineer.documents.filter(kind=CVDocument.Kind.FORMATTED).delete()
        CVDocument.objects.create(
            engineer=engineer, kind=CVDocument.Kind.FORMATTED,
            original_name=fname, uploaded_by=request.user,
            file=ContentFile(blob, name=fname),
        )
    except Exception as exc:
        logger.warning(f'[bd] could not store formatted CV: {exc}')

    resp = HttpResponse(
        blob,
        content_type='application/vnd.openxmlformats-officedocument'
                     '.wordprocessingml.document')
    resp['Content-Disposition'] = (
        f'attachment; filename="{engineer.full_name.replace(" ", "_")}_AJi_CV.docx"')
    return resp


@bd_required
@require_POST
def cv_delete(request, pk):
    engineer = get_object_or_404(Engineer, pk=pk)
    name = engineer.full_name
    engineer.delete()
    messages.success(request, f'CV for "{name}" deleted.')
    return redirect('bd:cv_list')


# ─────────────────────────────────────────────────────────────────────────────
# Historical Projects
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def project_list(request):
    qs = HistoricalProject.objects.all()

    q       = request.GET.get('q', '').strip()
    sector  = request.GET.get('sector', '').strip()
    country = request.GET.get('country', '').strip()
    status  = request.GET.get('status', '').strip()

    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(client__icontains=q)
                       | Q(project_text__icontains=q))
    if sector:
        qs = qs.filter(sector=sector)
    if country:
        qs = qs.filter(country__iexact=country)
    if status:
        qs = qs.filter(status=status)

    countries = (HistoricalProject.objects.exclude(country='')
                 .values_list('country', flat=True).distinct().order_by('country'))

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'bd/project_list.html', {
        'page':      page,
        'total':     paginator.count,
        'sectors':   ProjectSector.choices,
        'statuses':  ProjectStatus.choices,
        'countries': countries,
        'f': {'q': q, 'sector': sector, 'country': country, 'status': status},
    })


def _project_from_post(request, project=None):
    p = project or HistoricalProject()
    for field in ('code', 'name', 'city', 'country', 'client', 'description',
                  'site_area', 'bua', 'partners', 'services', 'staff_months',
                  'contact_person', 'contact_phone', 'contact_email',
                  'contact_address', 'contact_website', 'start_raw', 'end_raw'):
        setattr(p, field, request.POST.get(field, '').strip())
    p.sector   = request.POST.get('sector', 'OTHER')
    p.category = SECTOR_CATEGORY_MAP.get(p.sector, 'OTHER')
    p.status   = request.POST.get('status', 'UNKNOWN')
    for field in ('start_year', 'end_year'):
        val = request.POST.get(field, '').strip()
        setattr(p, field, int(val) if val.isdigit() else None)
    for field in ('project_value', 'design_value', 'spv_value',
                  'pmo_value', 'studies_value', 'others_value'):
        val = request.POST.get(field, '').strip().replace(',', '')
        try:
            setattr(p, field, float(val) if val else None)
        except ValueError:
            setattr(p, field, None)
    return p


@login_required
def project_create(request):
    if request.method == 'POST':
        p = _project_from_post(request)
        if not p.name:
            messages.error(request, 'Project name is required.')
        else:
            p.created_by = request.user
            p.save()
            messages.success(request, f'Project "{p.name}" created.')
            return redirect('bd:project_detail', pk=p.pk)
    return render(request, 'bd/project_form.html', {
        'project': None, 'sectors': ProjectSector.choices,
        'statuses': ProjectStatus.choices,
    })


@login_required
def project_detail(request, pk):
    project = get_object_or_404(HistoricalProject, pk=pk)
    return render(request, 'bd/project_detail.html', {
        'p':         project,
        'doc_types': ProjectDocument.DocType.choices,
    })


@login_required
def project_edit(request, pk):
    project = get_object_or_404(HistoricalProject, pk=pk)
    if request.method == 'POST':
        p = _project_from_post(request, project)
        if not p.name:
            messages.error(request, 'Project name is required.')
        else:
            p.save()
            messages.success(request, 'Project updated.')
            return redirect('bd:project_detail', pk=p.pk)
    return render(request, 'bd/project_form.html', {
        'project': project, 'sectors': ProjectSector.choices,
        'statuses': ProjectStatus.choices,
    })


@bd_required
@require_POST
def project_delete(request, pk):
    project = get_object_or_404(HistoricalProject, pk=pk)
    name = project.name
    project.delete()
    messages.success(request, f'Project "{name}" deleted.')
    return redirect('bd:project_list')


@login_required
@require_POST
def project_doc_upload(request, pk):
    project = get_object_or_404(HistoricalProject, pk=pk)
    f = request.FILES.get('document')
    if not f:
        messages.error(request, 'Please choose a file.')
        return redirect('bd:project_detail', pk=pk)
    if f.size > MAX_UPLOAD:
        messages.error(request, 'File too large — max 20 MB.')
        return redirect('bd:project_detail', pk=pk)

    text = extract_text(f)
    ProjectDocument.objects.create(
        project=project,
        doc_type=request.POST.get('doc_type', 'OTHER'),
        title=request.POST.get('title', '').strip() or f.name,
        original_name=f.name,
        extracted_text=text[:200000],
        uploaded_by=request.user,
        file=f,
    )
    extra = '' if text else ' (no text could be extracted — scanned file?)'
    messages.success(request, f'Document "{f.name}" attached{extra}.')
    return redirect('bd:project_detail', pk=pk)


@bd_required
@require_POST
def project_doc_delete(request, pk, doc_pk):
    doc = get_object_or_404(ProjectDocument, pk=doc_pk, project_id=pk)
    doc.delete()
    messages.success(request, 'Document removed.')
    return redirect('bd:project_detail', pk=pk)


# ─────────────────────────────────────────────────────────────────────────────
# Bulk CV Import
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_CV_EXT = ('.docx', '.pdf', '.txt')


@login_required
def cv_bulk_upload(request):
    """Upload many CVs at once; they queue for AJiAI analysis one by one."""
    if request.method == 'POST':
        files = request.FILES.getlist('cv_files')
        if not files:
            messages.error(request, 'Please choose at least one CV file.')
            return redirect('bd:cv_bulk_upload')

        valid, rejected = [], []
        for f in files:
            name = (f.name or '').lower()
            if not name.endswith(ALLOWED_CV_EXT):
                rejected.append(f'{f.name} (unsupported type)')
            elif f.size > MAX_UPLOAD:
                rejected.append(f'{f.name} (over 20 MB)')
            else:
                valid.append(f)

        if not valid:
            messages.error(request, 'No valid files — ' + '; '.join(rejected))
            return redirect('bd:cv_bulk_upload')

        batch = CVBatch.objects.create(created_by=request.user)
        for f in valid:
            CVBatchItem.objects.create(batch=batch, file=f, original_name=f.name)

        from .tasks import enqueue_batch
        runner = enqueue_batch(batch.pk)
        logger.info(f'[bd.batch] Batch #{batch.pk} queued via {runner} — '
                    f'{len(valid)} files by {request.user.username}')

        note = f' {len(rejected)} file(s) were skipped: {"; ".join(rejected)}.' if rejected else ''
        messages.success(request,
            f'{len(valid)} CV(s) queued for AJiAI analysis.{note}')
        return redirect('bd:cv_batch_detail', pk=batch.pk)

    recent = CVBatch.objects.all()[:10]
    return render(request, 'bd/cv_bulk.html', {'recent': recent})


@login_required
def cv_batch_detail(request, pk):
    batch = get_object_or_404(CVBatch, pk=pk)
    return render(request, 'bd/cv_batch_detail.html', {'batch': batch})


@login_required
def cv_batch_status(request, pk):
    """Polling endpoint for live progress on the batch page."""
    batch = get_object_or_404(CVBatch, pk=pk)
    items = [{
        'id':       i.pk,
        'name':     i.original_name,
        'status':   i.status,
        'error':    i.error,
        'engineer': i.engineer_id,
        'engineer_name': i.engineer.full_name if i.engineer else '',
        'was_update': i.was_update,
    } for i in batch.items.all()]
    return JsonResponse({
        'status':   batch.status,
        'progress': batch.progress_pct,
        'counts':   batch.counts,
        'items':    items,
    })


@login_required
@require_POST
def cv_batch_retry(request, pk):
    """Re-queue the failed items of a batch."""
    batch = get_object_or_404(CVBatch, pk=pk)
    n = batch.items.filter(status=CVBatchItem.Status.FAILED).update(
        status=CVBatchItem.Status.PENDING, error='')
    if n:
        batch.status = CVBatch.Status.QUEUED
        batch.save(update_fields=['status'])
        from .tasks import enqueue_batch
        enqueue_batch(batch.pk)
        messages.success(request, f'{n} failed CV(s) re-queued.')
    else:
        messages.info(request, 'No failed items to retry.')
    return redirect('bd:cv_batch_detail', pk=pk)


@login_required
@require_POST
def cv_mark_reviewed(request, pk):
    engineer = get_object_or_404(Engineer, pk=pk)
    engineer.needs_review = False
    engineer.save(update_fields=['needs_review'])
    messages.success(request, f'CV for "{engineer.full_name}" marked as reviewed.')
    return redirect('bd:cv_detail', pk=pk)


# ─────────────────────────────────────────────────────────────────────────────
# CV Photo management
# ─────────────────────────────────────────────────────────────────────────────

def _reextract_photo(engineer) -> str:
    """Re-extract the photo from the engineer's stored ORIGINAL CV document.
    Returns '' on success or a human-readable reason on failure."""
    from .photo_extract import extract_photo
    original = engineer.documents.filter(kind=CVDocument.Kind.ORIGINAL).first()
    if not original:
        return 'No original CV file is stored for this engineer.'
    try:
        with original.file.open('rb') as fh:
            import io as _io
            buf = _io.BytesIO(fh.read())
            buf.name = original.original_name or original.file.name
            photo_bytes, photo_ext = extract_photo(buf)
    except Exception as exc:
        return f'Could not read the original CV: {exc}'
    if not photo_bytes:
        return 'No photo was found inside the original CV file.'
    if photo_ext in ('emf', 'wmf', 'img'):
        return ('The photo is an EMF/WMF metafile and could not be converted — '
                'install Pillow on the server (pip install Pillow) and retry.')
    from django.core.files.base import ContentFile
    engineer.photo = ContentFile(
        photo_bytes,
        name=f"{engineer.full_name.replace(' ', '_')}_photo.{photo_ext}")
    engineer.save(update_fields=['photo'])
    return ''


@login_required
@require_POST
def cv_photo_update(request, pk):
    """Manual photo upload/replace, re-extract from the original CV, or remove."""
    engineer = get_object_or_404(Engineer, pk=pk)
    action = request.POST.get('action', 'upload')

    if action == 'remove':
        engineer.photo.delete(save=False)
        engineer.photo = None
        engineer.save(update_fields=['photo'])
        messages.success(request, 'Photo removed.')

    elif action == 're_extract':
        err = _reextract_photo(engineer)
        if err:
            messages.error(request, err)
        else:
            messages.success(request, 'Photo re-extracted from the original CV.')

    else:  # upload
        f = request.FILES.get('photo_file')
        if not f:
            messages.error(request, 'Please choose an image file.')
        elif f.size > 10 * 1024 * 1024:
            messages.error(request, 'Image too large — max 10 MB.')
        else:
            engineer.photo = f
            engineer.save(update_fields=['photo'])
            messages.success(request, 'Photo updated.')

    nxt = request.POST.get('next', '')
    if nxt.startswith('/'):
        return redirect(nxt)
    return redirect('bd:cv_detail', pk=pk)


@login_required
@require_POST
def cv_batch_cancel(request, pk):
    """Stop a queued/processing batch. The item currently being analysed
    finishes; everything still pending is skipped."""
    batch = get_object_or_404(CVBatch, pk=pk)
    if batch.status not in (CVBatch.Status.QUEUED, CVBatch.Status.PROCESSING):
        messages.info(request, 'This batch has already finished.')
        return redirect('bd:cv_batch_detail', pk=pk)

    batch.status = CVBatch.Status.CANCELLED
    batch.save(update_fields=['status'])
    # If nothing is mid-analysis, close out the pending items immediately;
    # otherwise the worker loop does it at the next between-items check.
    if not batch.items.filter(status=CVBatchItem.Status.PROCESSING).exists():
        from django.utils import timezone
        batch.items.filter(status=CVBatchItem.Status.PENDING).update(
            status=CVBatchItem.Status.CANCELLED)
        batch.finished_at = timezone.now()
        batch.save(update_fields=['finished_at'])
    logger.info(f'[bd.batch] Batch #{pk} cancelled by {request.user.username}')
    messages.success(request,
        'Batch cancelled — the CV currently being analysed will finish, '
        'the rest are skipped. You can delete the batch and start again.')
    return redirect('bd:cv_batch_detail', pk=pk)


@bd_required
@require_POST
def cv_batch_delete(request, pk):
    """Delete the batch, its items, and their uploaded files. Engineers
    already created from this batch are kept. A running batch is cancelled
    automatically before deletion."""
    batch = get_object_or_404(CVBatch, pk=pk)
    if batch.status in (CVBatch.Status.QUEUED, CVBatch.Status.PROCESSING):
        # Signal the worker loop to stop before we remove the rows
        batch.status = CVBatch.Status.CANCELLED
        batch.save(update_fields=['status'])

    for item in batch.items.all():
        try:
            item.file.delete(save=False)
        except Exception:
            pass
    batch.delete()
    logger.info(f'[bd.batch] Batch #{pk} deleted by {request.user.username}')
    messages.success(request,
        f'Batch #{pk} deleted. Engineers already imported from it were kept — '
        f'you can start a fresh upload.')
    return redirect('bd:cv_bulk_upload')
