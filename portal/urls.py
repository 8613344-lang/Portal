from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from library import views

urlpatterns = [
    path('health/', views.health, name='health'),
    path('', views.home, name='home'),
    path('books/', views.catalog, name='catalog'),
    path('read/<uuid:token>/', views.shared_reader, name='shared-reader'),
    path('books/<slug:slug>/', views.reader, name='reader'),
    path('books/<slug:slug>/download/<str:format>/', views.export_book, name='book-export'),
    path('pages/<int:pk>/audio/', views.page_audio, name='page-audio'),
    path('books/<slug:slug>/audio/<str:kind>/', views.sheet_audio, name='sheet-audio'),
    path('account/', views.account, name='account'),
    path('order/', views.order_create, name='order'),
    path('order/success/', views.order_success, name='order-success'),
    path('privacy/', views.privacy, name='privacy'),
    path('accounts/register/', views.register, name='register'),
    path('accounts/login/', auth_views.LoginView.as_view(), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('accounts/password-change/', auth_views.PasswordChangeView.as_view(template_name='registration/password_change.html', success_url='/account/'), name='password_change'),
    path('media-private/<path:path>', views.private_file, name='private-file'),
    path('admin/', admin.site.urls),
]
handler404 = 'library.errors.not_found'
handler500 = 'library.errors.server_error'
