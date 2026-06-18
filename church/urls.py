from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("my-schedule/", views.my_schedule, name="my_schedule"),
    path("rosters/", views.rosters, name="rosters"),
    path("calendar/", views.calendar, name="calendar"),
    path("more/", views.more, name="more"),
    path("sunday-plan/<int:pk>/", views.sunday_plan_detail, name="sunday_plan_detail"),
    path("assignment/<int:pk>/", views.assignment_detail, name="assignment_detail"),
    path("sunday-duty/<int:pk>/", views.sunday_duty_detail, name="sunday_duty_detail"),
    path("notifications/<int:pk>/dismiss/", views.dismiss_notification, name="dismiss_notification"),
    path("push/subscribe/", views.save_push_subscription, name="save_push_subscription"),
    path("push/unsubscribe/", views.remove_push_subscription, name="remove_push_subscription"),
    path("service-worker.js", views.service_worker, name="service_worker"),
]
