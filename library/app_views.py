"""Book-only screens used by the Android client; ordinary account permissions."""
from urllib.parse import urlencode
from django.contrib.auth import logout
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from .images import image_url
from django.core.paginator import Paginator
from django.db.models import OuterRef, Subquery
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_http_methods
from .models import Book, BookPage, NotificationRecipient
from .authentication import EmailAuthenticationForm
from .app_forms import DeleteAccountForm
from django.http import JsonResponse


def device_mode(request):
    return 'tablet' if request.GET.get('device') == 'tablet' else 'phone'


@never_cache
@require_http_methods(['GET'])
def context(request):
    return JsonResponse({'account': str(request.user.pk) if request.user.is_authenticated else 'guest',
                         'label': request.user.email if request.user.is_authenticated else 'Гость'})


@never_cache
def shelf(request):
    device = device_mode(request)
    count = 24 if device == 'tablet' else 12
    mine, everyone = shelf_pages(request, count, {'app': '1', 'device': device})
    return render(request, 'library/app_shelf.html', {'device': device, 'personal_books': mine, 'public_books': everyone})


def shelf_pages(request, count, reader_params):
    """The same published, permission-limited shelves for app and website."""
    first_image = BookPage.objects.filter(book=OuterRef('pk')).exclude(illustration='').order_by('position').values('illustration')[:1]
    books = Book.objects.filter(status='published').select_related('cover_page').annotate(first_image=Subquery(first_image))
    personal = books.filter(grants__user=request.user).distinct() if request.user.is_authenticated else books.none()
    public = books.filter(visibility='public').exclude(pk__in=personal.values('pk'))
    mine = Paginator(personal, count).get_page(request.GET.get('mine'))
    everyone = Paginator(public, count).get_page(request.GET.get('public'))
    for page in (mine, everyone):
        for book in page:
            image = book.cover_page.illustration if book.cover_page_id else book.cover
            name = image.name if image else book.first_image
            book.app_cover = image_url(name, 640) if name else ''
            book.app_cover_small = image_url(name, 320) if name else ''
            book.app_reader = reverse('reader', args=[book.slug]) + '?' + urlencode(reader_params)
    return mine, everyone


@never_cache
def web_shelf(request):
    mine, everyone = shelf_pages(request, 24, {'shelf': '1'})
    return render(request, 'library/web_shelf.html', {'personal_books': mine, 'public_books': everyone})


class AppLoginView(LoginView):
    authentication_form = EmailAuthenticationForm
    template_name = 'registration/app_login.html'
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        return super().get_context_data(device=device_mode(self.request), **kwargs)

    def get_success_url(self):
        return reverse('app-shelf') + '?' + urlencode({'device': device_mode(self.request)})


@require_POST
def app_logout(request):
    logout(request)
    return redirect(reverse('app-shelf') + '?' + urlencode({'device': device_mode(request)}))


@never_cache
@require_http_methods(['GET', 'POST'])
@login_required(login_url='app-login')
def account_delete(request):
    device = device_mode(request)
    context = {'device': device}
    if request.user.is_staff or request.user.is_superuser:
        return render(request, 'registration/app_account_delete.html', {**context, 'blocked': True}, status=403)
    if request.method == 'POST':
        with transaction.atomic():
            # Recheck role and password under the same lock as deletion.
            user = get_user_model().objects.select_for_update().filter(pk=request.user.pk).first()
            if user is not None and (user.is_staff or user.is_superuser):
                return render(request, 'registration/app_account_delete.html', {**context, 'blocked': True}, status=403)
            form = DeleteAccountForm(user=user, data=request.POST)
            if user is None or form.is_valid():
                if user is not None:
                    # Preserve notification history if this reader used to be an employee.
                    NotificationRecipient.objects.filter(employee=user).update(employee=None, active=False)
                    user.delete()
                logout(request)
                return redirect(reverse('app-shelf') + '?' + urlencode({'device': device, 'account_deleted': '1'}))
    else:
        form = DeleteAccountForm(user=request.user)
    return render(request, 'registration/app_account_delete.html', {**context, 'form': form})
