from django.conf.urls import url
from django.contrib.auth.decorators import login_required
from django.views.generic import TemplateView
from mozillians.common.decorators import allow_public, allow_unvouched
from mozillians.phonebook import views as phonebook_views

app_name = 'mozillians'
urlpatterns = [
    url(r'^$', phonebook_views.home, name='home'),
    url(r'^login/$', phonebook_views.login, name='login'),
    url(r'^logout/$', phonebook_views.logout, name='logout'),
    url(r'^user/edit/$', phonebook_views.edit_profile, name='profile_edit'),
    url(r'^u/(?P<username>[\w.@+-]+)/$', phonebook_views.view_profile, name='profile_view'),
    # Use Auth0 to verify an identity
    url(r'^user/delete/identity/(?P<identity_pk>\d+)/$', phonebook_views.delete_identity,
        name='delete_identity'),
    url(r'^user/primary/contact/identity/(?P<identity_pk>\d+)/$',
        phonebook_views.change_primary_contact_identity,
        name='change_primary_contact_identity'),
    url(r'^confirm-delete/$', phonebook_views.confirm_delete, name='profile_confirm_delete'),
    url(r'^delete/$', phonebook_views.delete, name='profile_delete'),
    url(r'^user/delete_idp_profiles/$', phonebook_views.delete_idp_profiles,
        name='delete_idp_profiles'),
    # Haystack search
    url(r'^search/$', allow_public(phonebook_views.PhonebookSearchView.as_view()),
        name='haystack_search'),
    url(r'^country/(?P<country>[A-Za-z0-9 \.\,]+)/$',
        phonebook_views.PhonebookSearchView.as_view(), name='list_country'),
    url(r'^country/(?P<country>[A-Za-z0-9 \.\,]+)/city/(?P<city>.+)/$',
        phonebook_views.PhonebookSearchView.as_view(), name='list_city'),
    url((r'^country/(?P<country>[A-Za-z0-9 \.\,]+)/'
         'region/(?P<region>.+)/city/(?P<city>.+)/$'),
        phonebook_views.PhonebookSearchView.as_view(), name='list_region_city'),
    url(r'^country/(?P<country>[A-Za-z0-9 \.]+)/region/(?P<region>.+)/$',
        phonebook_views.PhonebookSearchView.as_view(), name='list_region'),
    # Static pages need csrf for post to work
    url(r'^about/$',
        allow_public(TemplateView.as_view(template_name='phonebook/about.html')),
        name='about'),
    url(r'^about/dinomcvouch$',
        allow_public(TemplateView.as_view(template_name='phonebook/about-dinomcvouch.html')),
        name='about-dinomcvouch'),
    # CSP violation URL
    url(r'^capture-csp-violation$', phonebook_views.capture_csp_violation,
        name='capture-csp-violation')
]
