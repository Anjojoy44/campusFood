from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('register/', views.register, name='register'),
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/', views.profile, name='profile'),
    path('change-password/', views.change_password, name='change_password'),
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='canteen/password_reset.html',
        email_template_name='canteen/password_reset_email.html',
        success_url='done/'
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='canteen/password_reset_done.html'
    ), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='canteen/password_reset_confirm.html',
        success_url='/reset/done/'
    ), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='canteen/password_reset_complete.html'
    ), name='password_reset_complete'),
    path('menu/', views.menu_catalog, name='menu_catalog'),
    path('menu/order/', views.place_order, name='place_order'),
    path('menu/order/<int:menu_id>/', views.place_order, name='place_order_for_menu'),
    path('my-orders/', views.my_orders, name='my_orders'),
    path('my-orders/<int:order_id>/cancel/', views.cancel_order, name='cancel_order'),
    path('my-orders/by-date/', views.my_orders_by_date, name='my_orders_by_date'),
    path('my-orders/by-month/', views.my_orders_by_month, name='my_orders_by_month'),
    path('my-orders/report/<str:period>/<str:report_format>/', views.download_my_order_report, name='download_my_order_report'),
    path('notifications/', views.notifications, name='notifications'),
    path('notifications/orders/<int:order_id>/confirm-delivery/', views.confirm_order_delivery, name='confirm_order_delivery'),
    path('feedback/', views.submit_feedback, name='submit_feedback'),
    path('users/', views.admin_users, name='admin_users'),
    path('users/<int:user_id>/edit/', views.admin_user_edit, name='admin_user_edit'),
    path('users/<int:user_id>/delete/', views.admin_user_delete, name='admin_user_delete'),
    path('system-health/', views.system_health, name='admin_system_health'),
    path('add-admin/', views.add_admin, name='admin_add_admin'),
    path('add-admin/', views.add_admin, name='add_admin'),
    path('staff/menu/', views.menu_management, name='staff_menu'),
    path('staff/menu/<int:item_id>/edit/', views.menu_management, name='staff_menu_edit'),
    path('staff/menu/<int:item_id>/delete/', views.delete_menu_item, name='staff_menu_delete'),
    path('staff/offers/', views.offer_management, name='staff_offers'),
    path('staff/orders/', views.order_management, name='staff_orders'),
    path('staff/orders/by-date/', views.orders_by_date, name='staff_orders_by_date'),
    path('staff/orders/by-month/', views.orders_by_month, name='staff_orders_by_month'),
    path('staff/orders/report/<str:period>/<str:report_format>/', views.download_staff_order_report, name='download_staff_order_report'),
    path('staff/feedback/', views.feedback_management, name='staff_feedback'),
]