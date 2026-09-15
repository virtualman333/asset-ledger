from django.urls import path

from . import views

app_name = "users"

urlpatterns = [
    path("register/", views.register_view, name="register"),
    path("token/", views.token_view, name="token"),
    path("token/refresh/", views.token_refresh_view, name="token-refresh"),
    path("me/", views.me_view, name="me"),
]
