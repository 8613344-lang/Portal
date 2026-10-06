from django.contrib import admin
from django.utils import timezone
from .models import Book, BookPage, BookAccess, Order, NotificationRecipient, Notification

admin.site.site_header = 'Книги рядом · управление'
admin.site.site_title = 'Книги рядом'
admin.site.index_title = 'Книги, заказы и доступы'

class PageInline(admin.StackedInline):
    model = BookPage
    extra = 0
    fields = ['position', 'title', 'text', 'illustration', 'audio']
    show_change_link = True

class AccessInline(admin.TabularInline):
    model = BookAccess
    extra = 0
    autocomplete_fields = ['user']

@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ['title', 'status', 'visibility', 'created_at']
    list_filter = ['status', 'visibility']
    search_fields = ['title', 'slug', 'child_name']
    prepopulated_fields = {'slug': ['title']}
    inlines = [PageInline, AccessInline]
    view_on_site = False

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
