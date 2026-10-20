from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
import uuid
from django import forms as django_forms
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm
from .models import Book, BookPage, BookAccess, Order, NotificationRecipient, Notification
from .models import UserAgreement, HomepagePromotion, AudioPreview, BookVideo
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
