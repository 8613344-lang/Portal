import hmac
import secrets
import logging
from datetime import timedelta
from io import BytesIO
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.contrib.auth.views import PasswordResetView
from django.core.mail import send_mail
from django.db import transaction, IntegrityError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from .models import EmailConfirmation
from .forms import RegistrationForm


@never_cache
def captcha(request):
    from PIL import Image, ImageDraw, ImageFont
    code = ''.join(secrets.choice('23456789ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(6))
    request.session['captcha'] = [salted_hmac('captcha', code).hexdigest(), timezone.now().timestamp()]
    image = Image.new('RGB', (240, 80), '#f4ecd8')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(settings.BASE_DIR/'assets/fonts/DejaVuSerif.ttf'), 32)
    for i, c in enumerate(code): draw.text((12+i*36, secrets.randbelow(18)+10), c, font=font, fill='#254c3f')
    for _ in range(12): draw.line([(secrets.randbelow(240), secrets.randbelow(80)), (secrets.randbelow(240), secrets.randbelow(80))], fill='#bfae8b', width=1)
    data = BytesIO(); image.save(data,'PNG')
    return HttpResponse(data.getvalue(), content_type='image/png')


def issue_code(contact):
    code = str(secrets.randbelow(1000000)).zfill(6)
    contact.code_hash = salted_hmac('email-confirmation', f'{contact.user_id}:{contact.user.email.strip().lower()}:{code}').hexdigest()
    contact.expires_at = timezone.now()+timedelta(minutes=10)
    contact.sent_at = timezone.now()
    contact.attempts = 0
    contact.save()
    try:
        send_mail('Подтверждение email — Qicko', f'Код подтверждения: {code}\nДействует 10 минут. Если вы не регистрировались, проигнорируйте письмо.', settings.DEFAULT_FROM_EMAIL,[contact.user.email])
        return True
    except Exception as exc:
        logging.getLogger(__name__).warning('Confirmation delivery failed: %s', type(exc).__name__)
        return False


@never_cache
def register(request):
    if request.user.is_authenticated: return redirect('profile')
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST':
        valid = form.is_valid()
        challenge = request.session.pop('captcha', None)
        value = request.POST.get('captcha','').strip().upper()
        if not challenge or timezone.now().timestamp()-challenge[1] > 300 or not hmac.compare_digest(challenge[0],salted_hmac('captcha',value).hexdigest()):
            form.add_error('captcha','Код неверен или устарел. Введите код с новой картинки.')
            valid = False
        if valid:
            try:
                with transaction.atomic():
                    user = User.objects.create(username='u_'+secrets.token_hex(16),first_name=form.cleaned_data['name'],email=form.cleaned_data['email'],password=make_password(form.cleaned_data['password1']),is_active=False)
                    contact = EmailConfirmation.objects.create(user=user)
            except IntegrityError:
                form.add_error('email','Этот email уже используется.')
            else:
                request.session['pending_email_user'] = user.pk
                if not issue_code(contact): messages.error(request,'Не удалось отправить письмо. Проверьте email и повторите отправку.')
                return redirect('email-confirm')
    return render(request,'registration/register.html',{'form':form})


class ConfirmationForm(forms.Form):
    code = forms.RegexField(r'^\d{6}$',label='Код из письма',widget=forms.TextInput(attrs={'inputmode':'numeric','autocomplete':'one-time-code','maxlength':6}))


class ConfirmationStartForm(forms.Form):
    email = forms.EmailField(label='Email', max_length=254)


@never_cache
def confirmation_start(request):
    form = ConfirmationStartForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            contact = EmailConfirmation.objects.select_for_update().select_related('user').filter(user__email__iexact=form.cleaned_data['email'],verified_at__isnull=True).first()
            if contact:
                request.session['pending_email_user'] = contact.user_id
                if not contact.sent_at or contact.sent_at < timezone.now()-timedelta(seconds=60):
                    issue_code(contact)
                return redirect('email-confirm')
        messages.info(request,'Если аккаунт ожидает подтверждения, письмо будет отправлено. Для активного аккаунта используйте вход или восстановление пароля.')
    return render(request,'registration/email_start.html',{'form':form})


@never_cache
def confirm(request):
    form = ConfirmationForm(request.POST or None)
    with transaction.atomic():
        contact = EmailConfirmation.objects.select_for_update().select_related('user').filter(user_id=request.session.get('pending_email_user'),verified_at__isnull=True).first()
        if not contact: return redirect('register')
        if request.method == 'POST' and form.is_valid():
            if not contact.expires_at or contact.expires_at < timezone.now() or contact.attempts >= 5:
                form.add_error('code','Код истёк или исчерпаны попытки. Запросите новый код.')
            else:
                contact.attempts += 1
                digest = salted_hmac('email-confirmation',f'{contact.user_id}:{contact.user.email.strip().lower()}:{form.cleaned_data["code"]}').hexdigest()
                if hmac.compare_digest(contact.code_hash,digest):
                    contact.verified_at = timezone.now(); contact.code_hash = ''
                    contact.save()
                    contact.user.is_active = True; contact.user.save(update_fields=['is_active'])
                    login(request,contact.user,backend='library.authentication.EmailBackend')
                    request.session.pop('pending_email_user',None)
                    return redirect('profile')
                contact.save(update_fields=['attempts'])
                form.add_error('code','Неверный код.')
    return render(request,'registration/email_confirm.html',{'form':form,'email':contact.user.email})


@never_cache
@require_POST
def resend(request):
    with transaction.atomic():
        contact = EmailConfirmation.objects.select_for_update().select_related('user').filter(user_id=request.session.get('pending_email_user'),verified_at__isnull=True).first()
        if contact:
            if contact.sent_at and contact.sent_at > timezone.now()-timedelta(seconds=60):
                messages.error(request,'Следующее письмо можно запросить через 60 секунд.')
            elif not issue_code(contact): messages.error(request,'Не удалось отправить письмо. Попробуйте позже.')
    return redirect('email-confirm')


class ProfileForm(forms.Form):
    name = forms.CharField(label='Имя',max_length=150)


@never_cache
@login_required
def profile(request):
    form = ProfileForm(request.POST or None,initial={'name':request.user.first_name or request.user.username})
    if request.method == 'POST' and form.is_valid():
        request.user.first_name = form.cleaned_data['name']; request.user.save(update_fields=['first_name'])
        messages.success(request,'Профиль сохранён.')
        return redirect('profile')
    return render(request,'registration/profile.html',{'form':form})


class SafePasswordResetView(PasswordResetView):
    def form_valid(self, form):
        try: return super().form_valid(form)
        except Exception as exc:
            logging.getLogger(__name__).warning('Password reset delivery failed: %s',type(exc).__name__)
            return redirect(self.success_url)
