from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
import uuid
from django import forms as django_forms
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm
from .models import Book, BookPage, BookAccess, Order, NotificationRecipient, Notification
from .models import UserAgreement, HomepagePromotion, AudioPreview, BookVideo, HomepageItem, PageVisit
from .forms import BookAdminForm

admin.site.site_header = 'Книги рядом · управление'
admin.site.site_title = 'Книги рядом'
admin.site.index_title = 'Книги, заказы и настройки сайта'


class EmailUserChangeForm(UserChangeForm):
    def clean_email(self):
        value = self.cleaned_data.get('email','').strip().lower()
        if value and User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk).exists():
            raise django_forms.ValidationError('Этот email уже используется другим пользователем.')
        return value


admin.site.unregister(User)


@admin.register(User)
class PortalUserAdmin(UserAdmin):
    form = EmailUserChangeForm
    list_display = ['email','first_name','username','is_active','is_staff']
    search_fields = ['email','first_name','username']

class PageInline(admin.StackedInline):
    model = BookPage
    extra = 0
    fields = ['position', 'title', 'text', 'illustration', 'audio', 'pdf_full_page']
    show_change_link = True

class AccessInline(admin.TabularInline):
    model = BookAccess
    extra = 0
    autocomplete_fields = ['user']

@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    readonly_fields = ['viewing_link', 'video_viewing_link', 'video_status']
    actions = ['rotate_viewing_links', 'rotate_video_links']

    def get_search_results(self, request, queryset, search_term):
        queryset, distinct = super().get_search_results(request, queryset, search_term)
        if request.GET.get('model_name') == 'homepageitem' and request.GET.get('field_name') == 'book':
            queryset = queryset.filter(status='published', visibility='public')
        return queryset, distinct

    @admin.display(description='Ссылка «Начать просмотр» и QR')
    def viewing_link(self, obj):
        if not obj or not obj.pk: return 'Ссылка появится после сохранения книги.'
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', obj.share_url, obj.share_url)

    @admin.display(description='Ссылка на видео без регистрации и QR')
    def video_viewing_link(self, obj):
        if not obj or not obj.pk: return 'Ссылка появится после сохранения книги.'
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', obj.video_share_url, obj.video_share_url)

    @admin.display(description='Видео MP4')
    def video_status(self, obj):
        if not obj or not obj.pk: return 'Сохраните книгу, чтобы создать видео.'
        from django.urls import reverse
        video = BookVideo.objects.filter(book=obj).first()
        label = video.get_state_display() if video else 'Ещё не создано'
        return format_html('{} · <a href="{}">Открыть просмотр и сборку видео</a>',
                           label, reverse('book-video', args=[obj.slug]))

    @admin.action(description='Создать новые ссылки просмотра (отозвать старые)', permissions=['change'])
    def rotate_viewing_links(self, request, queryset):
        for book in queryset:
            book.share_token = uuid.uuid4()
            book.save(update_fields=['share_token'])
        self.message_user(request, 'Созданы новые ссылки. Старые ссылки и QR-коды больше не работают; выгрузите PDF заново.')

    @admin.action(description='Создать новые ссылки видео (отозвать старые)', permissions=['change'])
    def rotate_video_links(self, request, queryset):
        for book in queryset:
            book.video_share_token = uuid.uuid4()
            book.save(update_fields=['video_share_token'])
        self.message_user(request, 'Созданы новые ссылки видео. Старые ссылки и QR видео отозваны; пересоздайте видео и выгрузите PDF заново.')

    form = BookAdminForm
    list_display = ['title', 'status', 'visibility', 'created_at']
    list_filter = ['status', 'visibility', 'pdf_layout', 'background_theme']
    search_fields = ['title', 'slug', 'child_name']
    prepopulated_fields = {'slug': ['title']}
    inlines = [PageInline, AccessInline]
    view_on_site = False
    fieldsets = [('Книга', {'fields': ('title', 'slug', 'description', 'child_name', 'age_label')}),
                 ('Обложка', {'fields': ('cover', 'cover_page', 'cover_full_page', 'cover_audio')}),
                 ('Последняя страница', {'fields': ('ending_image', 'ending_page', 'ending_full_page', 'ending_audio', 'ending_show_qr', 'ending_show_video_qr', 'ending_show_text', 'ending_title', 'ending_text', 'ending_panel_color', 'ending_panel_transparency', 'ending_text_color', 'ending_qr_color'), 'description': 'Один QR: текст слева, не выше кода. Два QR: текст под обоими кодами. Заголовок входит в текстовый блок.'}),
                 ('Оформление и PDF', {'fields': ('pdf_layout', 'background_theme', 'background_image', 'video_status')}),
                 ('Импорт из PDF', {'fields': ('source_pdf',), 'description': 'При создании книги загрузите PDF: все листы автоматически станут страницами. Без текстового слоя листы сохраняются картинками.'}),
                 ('Публикация и доступ', {'fields': ('status', 'visibility', 'viewing_link', 'video_viewing_link'), 'description': 'Обе ссылки работают без регистрации при любом статусе книги. Ссылка видео разрешает только просмотр и скачивание MP4. Для отзыва выберите действие создания новых ссылок книги или видео в списке книг.'})]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        class AuthorizedBookForm(form):
            can_import_pdf = request.user.has_perm('library.add_bookpage')
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                for field in ('cover_page', 'ending_page'):
                    if field in self.fields:
                        self.fields[field].queryset = BookPage.objects.filter(book=obj) if obj else BookPage.objects.none()
        return AuthorizedBookForm

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        prepared = form.cleaned_data.get('source_pdf')
        if prepared:
            from .pdf_import import save_pdf_pages
            try:
                count = save_pdf_pages(form.instance, prepared)
                self.message_user(request, f'PDF импортирован: {count} страниц. Проверьте книгу перед публикацией.')
            finally:
                prepared.close()

@admin.register(BookPage)
class PageAdmin(admin.ModelAdmin):
    list_display = ['book', 'position', 'title']
    list_filter = ['book__status']
    search_fields = ['title', 'book__title']
    autocomplete_fields = ['book']

@admin.register(BookAccess)
class AccessAdmin(admin.ModelAdmin):
    list_display = ['book', 'user', 'created_at']
    autocomplete_fields = ['book', 'user']
    search_fields = ['book__title', 'user__username']

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'parent_name', 'child_name', 'status', 'manager', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['parent_name', 'email', 'child_name', 'id']
    autocomplete_fields = ['book', 'user', 'manager']
    readonly_fields = ['id', 'submission_key', 'created_at', 'consent']
    actions = ['grant_book_access']
    @admin.action(description='Выдать заказчикам доступ к готовым книгам')
    def grant_book_access(self, request, queryset):
        if not request.user.has_perm('library.add_bookaccess'):
            self.message_user(request, 'Нет права выдачи доступа.', level='ERROR')
            return
        count = 0
        for order in queryset.filter(user__isnull=False, book__isnull=False):
            _, created = BookAccess.objects.get_or_create(user=order.user, book=order.book)
            count += int(created)
        self.message_user(request, f'Выдано доступов: {count}. Книги также должны быть опубликованы.')

@admin.register(NotificationRecipient)
class RecipientAdmin(admin.ModelAdmin):
    list_display = ['name', 'channel', 'destination', 'employee', 'active']
    list_filter = ['channel', 'active']
    autocomplete_fields = ['employee']

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['order', 'recipient', 'channel', 'state', 'attempts', 'next_attempt', 'sent_at']
    list_filter = ['state', 'channel']
    readonly_fields = ['order', 'recipient', 'destination', 'channel', 'state', 'attempts', 'next_attempt', 'last_error', 'sent_at', 'lease_token']
    actions = ['retry_failed']
    def has_add_permission(self, request): return False
    @admin.action(description='Повторить неудачные уведомления')
    def retry_failed(self, request, queryset):
        count = queryset.filter(state='failed').update(state='pending', attempts=0, next_attempt=timezone.now(), last_error='', lease_token=None)
        self.message_user(request, f'Возвращено в очередь: {count}.')


@admin.register(UserAgreement)
class UserAgreementAdmin(admin.ModelAdmin):
    list_display = ['title', 'updated_at']
    fields = ['title', 'content', 'public_page', 'updated_at']
    readonly_fields = ['public_page', 'updated_at']
    actions = None

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not UserAgreement.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Страница на сайте')
    def public_page(self, obj):
        from django.urls import reverse
        url = reverse('privacy')
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, url)

    def view_on_site(self, obj):
        return obj.get_absolute_url()


@admin.register(HomepagePromotion)
class HomepagePromotionAdmin(admin.ModelAdmin):
    list_display = ['title', 'updated_at']
    fields = ['eyebrow', 'title', 'text', 'updated_at']
    readonly_fields = ['updated_at']
    actions = None

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not HomepagePromotion.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def view_on_site(self, obj):
        return obj.get_absolute_url()


@admin.register(AudioPreview)
class AudioPreviewAdmin(admin.ModelAdmin):
    list_display = ['position', 'title', 'enabled', 'has_recording']
    fields = ['position', 'title', 'audio', 'enabled', 'listening_link']
    readonly_fields = ['position', 'listening_link']
    actions = None
    view_on_site = False

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(boolean=True, description='Запись загружена')
    def has_recording(self, obj):
        return bool(obj.audio)

    @admin.display(description='Публичный аудиопример')
    def listening_link(self, obj):
        if obj and obj.audio and obj.enabled:
            return format_html('<a href="{}" target="_blank" rel="noopener">Послушать запись</a>', obj.get_absolute_url())
        return 'Загрузите запись и включите вариант, чтобы он стал доступен на главной.'

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'audio':
            field.widget.attrs['accept'] = '.mp3,.wav,audio/mpeg,audio/wav'
        return field


@admin.register(HomepageItem)
class HomepageItemAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'kind', 'position', 'enabled', 'book']
    list_editable = ['position', 'enabled']
    list_filter = ['kind', 'enabled']
    search_fields = ['title', 'book__title']
    autocomplete_fields = ['book']
    fieldsets = [
        ('Показ на главной', {'fields': ('kind', 'enabled', 'position'),
            'description': 'Раздел «А какая история будет у вас?». Порядок задаётся числом. Отключённые карточки не показываются.'}),
        ('Книга', {'fields': ('book',), 'description': 'Только опубликованные публичные книги. Название, описание и обложка берутся из книги. Если доступ изменится, карточка скроется автоматически.'}),
        ('Идея книги', {'fields': ('title', 'description', 'image', 'default_image', 'theme'),
            'description': 'Для типа «Идея книги». Загрузите обложку или выберите готовую иллюстрацию. Кнопка ведёт к заказу.'}),
    ]


@admin.register(PageVisit)
class PageVisitAdmin(admin.ModelAdmin):
    list_display = ['occurred_at', 'kind', 'path', 'book', 'book_page', 'visitor_user', 'ip_address', 'city', 'country', 'browser', 'browser_version', 'device', 'is_bot']
    list_filter = ['kind', 'is_bot', 'browser', 'device', 'occurred_at']
    search_fields = ['path', 'ip_address', 'city', 'region', 'country', 'user__email', 'book__title', 'user_agent']
    list_select_related = ['book', 'user']
    date_hierarchy = 'occurred_at'
    change_list_template = 'admin/library/pagevisit/change_list.html'
    change_form_template = 'admin/library/pagevisit/change_form.html'
    readonly_fields = [field.name for field in PageVisit._meta.fields]
    actions = None

    @admin.display(description='Пользователь', ordering='user__email')
    def visitor_user(self, obj):
        return (obj.user.email or obj.user.first_name or obj.user.username) if obj.user_id else 'Гость'

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False

    def get_urls(self):
        from django.urls import path
        return [path('dashboard/', self.admin_site.admin_view(self.dashboard), name='library_pagevisit_dashboard'),
                path('export.csv', self.admin_site.admin_view(self.export_csv), name='library_pagevisit_csv')] + super().get_urls()

    def export_csv(self, request):
        from django.core.exceptions import PermissionDenied
        from django.contrib.admin.views.main import IncorrectLookupParameters
        from django.http import HttpResponse, HttpResponseNotAllowed
        from .statistics_export import visits_csv
        if not self.has_view_permission(request): raise PermissionDenied
        if request.method != 'GET': return HttpResponseNotAllowed(['GET'])
        try:
            visits = self.get_changelist_instance(request).queryset
        except (IncorrectLookupParameters, ValueError):
            return HttpResponse('Некорректные фильтры статистики.', status=400)
        return visits_csv(visits)

    def dashboard(self, request):
        from datetime import timedelta
        from django.core.exceptions import PermissionDenied
        from django.db.models import Count, Q
        from django.db.models.functions import TruncDate
        from django.template.response import TemplateResponse
        if not self.has_view_permission(request): raise PermissionDenied
        days = request.GET.get('days', '30')
        days = int(days) if days in ('7', '30', '90') else 30
        include_bots = request.GET.get('bots') == '1'
        visits = PageVisit.objects.filter(occurred_at__gte=timezone.now()-timedelta(days=days))
        if not include_bots: visits = visits.filter(is_bot=False)
        totals = visits.aggregate(total=Count('pk'), openings=Count('pk', filter=Q(kind='book_open')),
            turns=Count('pk', filter=Q(kind='book_page')), videos=Count('pk', filter=Q(kind='video')), users=Count('user', distinct=True))
        daily = list(visits.annotate(day=TruncDate('occurred_at')).values('day').annotate(total=Count('pk'), visitors=Count('visitor_key', distinct=True)).order_by('day'))
        peak = max((row['total'] for row in daily), default=1)
        for row in daily: row['percent'] = round(row['total']/peak*100)
        books = visits.filter(book__isnull=False).values('book_id', 'book__title').annotate(
            openings=Count('pk', filter=Q(kind='book_open')), turns=Count('pk', filter=Q(kind='book_page')),
            videos=Count('pk', filter=Q(kind='video'))).order_by('-openings', '-turns')[:20]
        pages = visits.values('path').annotate(total=Count('pk')).order_by('-total')[:20]
        cities = visits.exclude(city='').values('city', 'region', 'country').annotate(total=Count('pk')).order_by('-total')[:20]
        from urllib.parse import urlencode
        from django.urls import reverse
        from .geolocation import available
        export_filters = {'occurred_at__gte':(timezone.now()-timedelta(days=days)).isoformat()}
        if not include_bots: export_filters['is_bot__exact'] = '0'
        return TemplateResponse(request, 'admin/library/pagevisit/dashboard.html', {
            **self.admin_site.each_context(request), 'title': 'Посещения сайта и просмотры книг',
            'opts': self.model._meta, 'days': days, 'include_bots': include_bots,
            'totals': totals, 'daily': daily, 'books': books, 'pages': pages, 'cities':cities,
            'geoip_available':available(),
            'export_url':reverse('admin:library_pagevisit_csv')+'?'+urlencode(export_filters),
        })
