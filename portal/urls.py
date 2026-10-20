from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from library import views
from library import app_views, account_views, video_views
from library.authentication import EmailAuthenticationForm

urlpatterns = [
    path('accounts/confirm/start/', account_views.confirmation_start, name='email-start'),
    path('accounts/captcha/', account_views.captcha, name='captcha'),
    path('accounts/confirm/', account_views.confirm, name='email-confirm'),
    path('accounts/confirm/resend/', account_views.resend, name='email-resend'),
    path('profile/', account_views.profile, name='profile'),
    path('accounts/password-reset/', account_views.SafePasswordResetView.as_view(), name='password_reset'),
    path('accounts/password-reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('app/', app_views.shelf, name='app-shelf'),
    path('app/context/', app_views.context, name='app-context'),
    path('shelf/', app_views.web_shelf, name='web-shelf'),
    path('app/login/', app_views.AppLoginView.as_view(), name='app-login'),
    path('app/logout/', app_views.app_logout, name='app-logout'),
    path('app/account/delete/', app_views.account_delete, name='app-account-delete'),
    path('health/', views.health, name='health'),
    path('', views.home, name='home'),
    path('audio-previews/<int:position>/', views.audio_preview, name='audio-preview'),
    path('books/', views.catalog, name='catalog'),
    path('read/<uuid:token>/', views.shared_reader, name='shared-reader'),
    path('books/<slug:slug>/', views.reader, name='reader'),
    path('books/<slug:slug>/video/', video_views.watch, name='book-video'),
    path('books/<slug:slug>/video/status/', video_views.status, name='book-video-status'),
    path('books/<slug:slug>/video/create/', video_views.create, name='book-video-create'),
    path('books/<slug:slug>/video/file/', video_views.file, name='book-video-file'),
    path('books/<slug:slug>/download/<str:format>/', views.export_book, name='book-export'),
    path('pages/<int:pk>/audio/', views.page_audio, name='page-audio'),
    path('books/<slug:slug>/audio/<str:kind>/', views.sheet_audio, name='sheet-audio'),
    path('account/', views.account, name='account'),
    path('order/', views.order_create, name='order'),
    path('order/success/', views.order_success, name='order-success'),
    path('privacy/', views.privacy, name='privacy'),
    path('accounts/register/', account_views.register, name='register'),
    path('accounts/login/', auth_views.LoginView.as_view(authentication_form=EmailAuthenticationForm), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('accounts/password-change/', auth_views.PasswordChangeView.as_view(template_name='registration/password_change.html', success_url='/account/'), name='password_change'),
    path('media-private/<path:path>', views.private_file, name='private-file'),
    path('admin/', admin.site.urls),
]
handler404 = 'library.errors.not_found'
handler500 = 'library.errors.server_error'
