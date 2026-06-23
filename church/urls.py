from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("register/", views.register, name="register"),
    path("register/done/", views.register_done, name="register_done"),
    path("my-schedule/", views.my_schedule, name="my_schedule"),
    path("catering/", views.catering, name="catering"),
    path("catering/claim/", views.claim_catering, name="claim_catering"),
    path("rosters/", views.rosters, name="rosters"),
    path("calendar/", views.calendar, name="calendar"),
    path("more/", views.more, name="more"),
    path("profile/", views.profile, name="profile"),
    path("sunday-plan/<int:pk>/", views.sunday_plan_detail, name="sunday_plan_detail"),
    path("assignment/<int:pk>/", views.assignment_detail, name="assignment_detail"),
    path("sunday-duty/<int:pk>/", views.sunday_duty_detail, name="sunday_duty_detail"),
    path("notifications/<int:pk>/dismiss/", views.dismiss_notification, name="dismiss_notification"),
    path("roster-reminders/send/", views.send_roster_reminder, name="send_roster_reminder"),
    path("users/<int:pk>/approve/", views.approve_pending_user, name="approve_pending_user"),
    path("users/<int:pk>/dismiss/", views.dismiss_pending_user, name="dismiss_pending_user"),
    path("push/subscribe/", views.save_push_subscription, name="save_push_subscription"),
    path("push/unsubscribe/", views.remove_push_subscription, name="remove_push_subscription"),
    path("service-worker.js", views.service_worker, name="service_worker"),
]
