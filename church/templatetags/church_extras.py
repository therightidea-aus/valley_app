from django import template

register = template.Library()


@register.filter
def names(users):
    return ", ".join(user.get_full_name() or user.username for user in users.all())


@register.simple_tag(takes_context=True)
def nav_class(context, item):
    active = context.get("active_nav")
    return "nav-link active" if active == item else "nav-link"
