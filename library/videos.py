"""Durable, private MP4 cache. Rendering runs only in the video worker."""
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
import uuid
import wave
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db.models import Q, F, FileField
from django.utils import timezone

from .exports import ExportError, book_pdf
from .models import BookVideo
from .sheets import book_sheets, reader_spread

RENDERER_VERSION = 2
WIDTH, HEIGHT = 1280, 720


def fingerprint(book, delay=15):
    def snapshot(instance):
        values = {}
        for field in instance._meta.concrete_fields:
            if field.name in ('created_at', 'status', 'visibility'):
                continue
            value = getattr(instance, field.attname)
            if isinstance(field, FileField):
                values[field.name] = value.name
                if value:
                    try:
                        values[field.name + ':size'] = value.size
                        values[field.name + ':mtime'] = value.storage.get_modified_time(value.name).isoformat()
                    except (OSError, NotImplementedError):
                        values[field.name + ':missing'] = True
            else:
                values[field.name] = value
        return values
    data = [RENDERER_VERSION, settings.SITE_URL, delay, snapshot(book),
            [snapshot(page) for page in book.pages.order_by('position')]]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def current_video(book):
    video = BookVideo.objects.filter(book=book).first()
    if not video:
        return None, False
    valid = video.fingerprint == fingerprint(book, video.delay)
    ready = valid and video.state == 'ready' and bool(video.file) and video.file.storage.exists(video.file.name)
    return video, ready


def queue_video(book, delay):
    value = fingerprint(book, delay)
    video, created = BookVideo.objects.get_or_create(book=book, defaults={'fingerprint': value, 'delay': delay})
    if not created:
        same = video.fingerprint == value
        if same and (video.state in ('queued', 'rendering') or
                     (video.state == 'ready' and video.file and video.file.storage.exists(video.file.name))):
            return video
        # Revoke the old lease; the old worker can no longer publish its result.
        BookVideo.objects.filter(pk=video.pk).update(fingerprint=value, delay=delay, state='queued',
            progress=0, total=0, error='', lease_token=None, lease_until=None, updated_at=timezone.now())
        video.refresh_from_db()
    return video


def render_groups(book, pages):
    sheets = book_sheets(book, pages)
    offsets, cursor = {}, 0
    for sheet in sheets:
        count = 1
        if (sheet.kind == 'story' and not sheet.pdf_full_page and book.pdf_layout == 'alternating'
                and sheet.illustration and sheet.text.strip()):
            count = 2
        offsets[sheet.pk] = list(range(cursor, cursor + count))
        cursor += count
    groups, number = [], 1
    while number:
        spread = reader_spread(book, sheets, number)
        groups.append(([index for sheet in spread['pages'] for index in offsets[sheet.pk]],
                       [sheet.audio for sheet in spread['pages'] if sheet.audio]))
        number = spread['next']
    return groups, cursor


def render_mp4(book, delay, root, progress):
    import imageio_ffmpeg
    import pypdfium2 as pdfium
    from PIL import Image

    pages = list(book.pages.all())
    groups, leaf_count = render_groups(book, pages)
    if len(groups) > 500:
        raise ExportError('Для одного видео допускается не больше 500 разворотов.')
    if sum(delay for _, recordings in groups if not recordings) > 7200:
        raise ExportError('С выбранным временем видео длиннее двух часов. Уменьшите время без озвучки.')
    progress(0, len(groups))
    pdf_path = root / 'book.pdf'
    with book_pdf(book, pages) as source, pdf_path.open('wb') as target:
        shutil.copyfileobj(source, target)
    deadline = time.monotonic() + 1800
    executable = imageio_ffmpeg.get_ffmpeg_exe()

    def run(arguments):
        progress(None, len(groups))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ExportError('Сборка видео превысила 30 минут. Уменьшите книгу.')
        try:
            subprocess.run([executable, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                            '-threads', '1', *arguments], cwd=root, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=min(remaining, 240),
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError) as error:
            raise ExportError('Не удалось создать видео. Проверьте изображения и аудиофайлы книги.') from error

    document = pdfium.PdfDocument(str(pdf_path))
    segments, total_duration = [], 0.0
    try:
        if len(document) != leaf_count:
            raise ExportError('Не удалось сопоставить листы PDF и озвучку.')
        for index, (leaves, recordings) in enumerate(groups):
            frame = Image.new('RGB', (WIDTH, HEIGHT), '#242721')
            cell_width = (WIDTH - 24 * (len(leaves) + 1)) // len(leaves)
            for position, leaf_index in enumerate(leaves):
                page = document[leaf_index]
                bitmap = None
                try:
                    scale = min(cell_width / page.get_width(), (HEIGHT - 32) / page.get_height())
                    bitmap = page.render(scale=scale)
                    picture = bitmap.to_pil().convert('RGB')
                    x = 24 + position * (cell_width + 24) + (cell_width - picture.width) // 2
                    frame.paste(picture, (x, (HEIGHT - picture.height) // 2))
                    picture.close()
                finally:
                    if bitmap is not None: bitmap.close()
                    page.close()
            frame_path = root / f'frame-{index}.png'
            frame.save(frame_path)
            frame.close()
            audio_parts, duration = [], 0.0
            for recording_index, recording in enumerate(recordings):
                source = root / f'source-{index}-{recording_index}{Path(recording.name).suffix.lower()}'
                with recording.open('rb') as stream, source.open('wb') as target:
                    shutil.copyfileobj(stream, target)
                normalized = root / f'audio-{index}-{recording_index}.wav'
                run(['-protocol_whitelist', 'file,pipe', '-f', 'mp3' if source.suffix.lower() == '.mp3' else 'wav', '-i', source.name, '-vn', '-map_metadata', '-1',
                     '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', '-t', '7201',
                     '-fs', '700000000', normalized.name])
                with wave.open(str(normalized), 'rb') as audio:
                    duration += audio.getnframes() / audio.getframerate()
                audio_parts.append(normalized)
                source.unlink()
            if not audio_parts:
                duration = delay
                audio_input = ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono']
            elif len(audio_parts) == 1:
                audio_input = ['-i', audio_parts[0].name]
            else:
                manifest = root / f'audio-{index}.txt'
                manifest.write_text('\n'.join(f"file '{part.name}'" for part in audio_parts), encoding='ascii')
                audio_input = ['-f', 'concat', '-safe', '1', '-protocol_whitelist', 'file,pipe', '-i', manifest.name]
            if duration <= 0:
                raise ExportError('Одна из записей страницы пуста.')
            total_duration += duration
            if total_duration > 7200:
                raise ExportError('Длительность одного видео ограничена двумя часами.')
            segment = root / f'part-{index}.mp4'
            run(['-loop', '1', '-framerate', '24', '-i', frame_path.name, *audio_input,
                 '-t', f'{duration:.6f}', '-map', '0:v:0', '-map', '1:a:0',
                 '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast', '-tune', 'stillimage',
                 '-crf', '23', '-pix_fmt', 'yuv420p', '-r', '24',
                 '-c:a', 'aac', '-b:a', '128k', '-ac', '1', '-ar', '48000',
                 '-map_metadata', '-1', segment.name])
            segments.append(segment)
            frame_path.unlink()
            for part in audio_parts: part.unlink()
            progress(index + 1, len(groups))
        manifest = root / 'segments.txt'
        manifest.write_text('\n'.join(f"file '{part.name}'" for part in segments), encoding='ascii')
        result = root / 'book.mp4'
        run(['-f', 'concat', '-safe', '1', '-protocol_whitelist', 'file,pipe', '-i', manifest.name,
             '-c', 'copy', '-map_metadata', '-1', '-movflags', '+faststart', result.name])
        return result
    finally:
        document.close()


def process_one():
    now = timezone.now()
    eligible = Q(state='queued') | Q(state='rendering', lease_until__lt=now)
    for pk in BookVideo.objects.filter(eligible).order_by('updated_at').values_list('pk', flat=True)[:10]:
        token = uuid.uuid4()
        if not BookVideo.objects.filter(eligible, pk=pk).update(state='rendering', lease_token=token,
                lease_until=now + timedelta(minutes=10), error=''):
            continue
        job = BookVideo.objects.select_related('book__cover_page', 'book__ending_page').filter(pk=pk, lease_token=token, state='rendering').first()
        if job is None:
            return True

        def progress(done, total):
            update = {'total': total, 'lease_until': timezone.now() + timedelta(minutes=10)}
            if done is not None: update['progress'] = done
            if not BookVideo.objects.filter(pk=pk, lease_token=token, state='rendering').update(**update):
                raise ExportError('Создание этой версии видео отменено.')

        new_name = None
        try:
            if fingerprint(job.book, job.delay) != job.fingerprint:
                raise ExportError('Книга изменилась. Создайте видео заново.')
            with tempfile.TemporaryDirectory(prefix='portal-video-') as directory:
                result = render_mp4(job.book, job.delay, Path(directory), progress)
                job.book.refresh_from_db()
                if fingerprint(job.book, job.delay) != job.fingerprint:
                    raise ExportError('Книга изменилась во время сборки. Создайте видео заново.')
                storage = job.file.storage
                with result.open('rb') as source:
                    new_name = storage.save(job.file.field.generate_filename(job, 'book.mp4'), File(source))
                changed = BookVideo.objects.filter(pk=pk, lease_token=token, state='rendering').update(
                    file=new_name, state='ready', progress=F('total'), error='',
                    lease_token=None, lease_until=None, updated_at=timezone.now())
                if not changed:
                    storage.delete(new_name)
                elif job.file and job.file.name != new_name:
                    storage.delete(job.file.name)
        except Exception as error:
            if new_name:
                # Never remove a file which has already been published successfully.
                if not BookVideo.objects.filter(pk=pk, file=new_name, state='ready').exists():
                    job.file.storage.delete(new_name)
            message = str(error) if isinstance(error, ExportError) else 'Не удалось подготовить видео. Проверьте файлы книги и повторите.'
            BookVideo.objects.filter(pk=pk, lease_token=token).update(
                state='failed', error=message[:300], lease_token=None, lease_until=None, updated_at=timezone.now())
        return True
    return False
