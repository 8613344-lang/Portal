from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Create Editor and Manager permission groups without creating passwords/users.'
    def handle(self, *args, **options):
        definitions = {
            'Редакторы книг': ['view_book', 'add_book', 'change_book', 'view_bookpage', 'add_bookpage', 'change_bookpage', 'delete_bookpage', 'view_bookaccess', 'add_bookaccess', 'change_bookaccess', 'delete_bookaccess', 'view_hiddenvideo', 'add_hiddenvideo', 'change_hiddenvideo', 'delete_hiddenvideo', 'view_printedbookexample', 'add_printedbookexample', 'change_printedbookexample', 'delete_printedbookexample'],
            'Менеджеры заказов': ['view_order', 'change_order', 'view_book', 'view_bookaccess', 'add_bookaccess', 'view_notification', 'view_orderfunnelevent'],
        }
        for name, codes in definitions.items():
            group, _ = Group.objects.get_or_create(name=name)
            permissions = list(Permission.objects.filter(content_type__app_label='library', codename__in=codes))
            permissions += list(Permission.objects.filter(content_type__app_label='auth', codename='view_user'))
            group.permissions.set(permissions)
        self.stdout.write('Groups ready. Assign them to active staff users in admin.')
