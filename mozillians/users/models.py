import logging
import os
import uuid
from itertools import chain

from pytz import common_timezones

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.db import models
from django.db.models import Manager, ManyToManyField, Q
from django.template.loader import get_template
from django.utils.encoding import iri_to_uri
from django.utils.http import urlquote
from django.utils.timezone import now
from django.utils.translation import ugettext as _
from django.utils.translation import ugettext_lazy as _lazy
from mozillians.common import utils
from mozillians.common.templatetags.helpers import (absolutify, gravatar,
                                                    offset_of_timezone)
from mozillians.common.urlresolvers import reverse
from mozillians.phonebook.validators import (validate_discord, validate_email,
                                             validate_linkedin,
                                             validate_phone_number,
                                             validate_twitter,
                                             validate_username_not_url,
                                             validate_website)
from mozillians.users import get_languages_for_locale
from mozillians.users.managers import (EMPLOYEES, MOZILLIANS, PRIVACY_CHOICES,
                                       PRIVACY_CHOICES_WITH_PRIVATE, PRIVATE,
                                       PUBLIC, PUBLIC_INDEXABLE_FIELDS,
                                       UserProfileQuerySet)
from PIL import Image
from product_details import product_details
from sorl.thumbnail import ImageField, get_thumbnail

COUNTRIES = product_details.get_regions('en-US')
AVATAR_SIZE = (300, 300)
logger = logging.getLogger(__name__)
ProfileManager = Manager.from_queryset(UserProfileQuerySet)


def _calculate_photo_filename(instance, filename):
    """Generate a unique filename for uploaded photo."""
    return os.path.join(settings.USER_AVATAR_DIR, str(uuid.uuid4()) + '.jpg')


class PrivacyField(models.PositiveSmallIntegerField):

    def __init__(self, *args, **kwargs):
        myargs = {'default': MOZILLIANS,
                  'choices': PRIVACY_CHOICES}
        myargs.update(kwargs)
        super(PrivacyField, self).__init__(*args, **myargs)


class UserProfilePrivacyModel(models.Model):
    _privacy_level = None

    privacy_photo = PrivacyField()
    privacy_full_name = PrivacyField()
    privacy_email = PrivacyField(choices=PRIVACY_CHOICES_WITH_PRIVATE,
                                 default=MOZILLIANS)
    privacy_bio = PrivacyField()
    privacy_geo_city = PrivacyField()
    privacy_geo_region = PrivacyField()
    privacy_geo_country = PrivacyField()
    privacy_city = PrivacyField()
    privacy_region = PrivacyField()
    privacy_country = PrivacyField()
    privacy_languages = PrivacyField()
    privacy_date_mozillian = PrivacyField()
    privacy_timezone = PrivacyField()
    privacy_title = PrivacyField()
    privacy_story_link = PrivacyField()

    CACHED_PRIVACY_FIELDS = None

    class Meta:
        abstract = True

    @classmethod
    def clear_privacy_fields_cache(cls):
        """
        Clear any caching of the privacy fields.
        (This is only used in testing.)
        """
        cls.CACHED_PRIVACY_FIELDS = None

    @classmethod
    def privacy_fields(cls):
        """
        Return a dictionary whose keys are the names of the fields in this
        model that are privacy-controlled, and whose values are the default
        values to use for those fields when the user is not privileged to
        view their actual value.

        Note: should be only used through UserProfile . We should
        fix this.

        """
        # Cache on the class object
        if cls.CACHED_PRIVACY_FIELDS is None:
            privacy_fields = {}
            field_names = list(set(chain.from_iterable(
                (field.name, field.attname) if hasattr(field, 'attname') else
                (field.name,) for field in cls._meta.get_fields()
                if not (field.many_to_one and field.related_model is None)
            )))
            for name in field_names:
                if name.startswith('privacy_') or not 'privacy_%s' % name in field_names:
                    # skip privacy fields and uncontrolled fields
                    continue
                field = cls._meta.get_field(name)
                # Okay, this is a field that is privacy-controlled
                # Figure out a good default value for it (to show to users
                # who aren't privileged to see the actual value)
                if isinstance(field, ManyToManyField):
                    default = field.remote_field.model.objects.none()
                else:
                    default = field.get_default()
                privacy_fields[name] = default
            # HACK: There's not really an email field on UserProfile,
            # but it's faked with a property
            privacy_fields['email'] = u''

            cls.CACHED_PRIVACY_FIELDS = privacy_fields
        return cls.CACHED_PRIVACY_FIELDS


class UserProfile(UserProfilePrivacyModel):
    objects = ProfileManager()

    user = models.OneToOneField(User)
    full_name = models.CharField(max_length=255, default='', blank=False,
                                 verbose_name=_lazy(u'Full Name'))
    is_vouched = models.BooleanField(
        default=False,
        help_text='You can edit vouched status by editing invidual vouches')
    can_vouch = models.BooleanField(
        default=False,
        help_text='You can edit can_vouch status by editing invidual vouches')
    last_updated = models.DateTimeField(auto_now=True)
    bio = models.TextField(verbose_name=_lazy(u'Bio'), default='', blank=True)
    photo = ImageField(default='', blank=True, upload_to=_calculate_photo_filename)

    # validated geo data (validated that it's valid geo data, not that the
    # mozillian is there :-) )
    geo_country = models.ForeignKey('geo.Country', blank=True, null=True,
                                    on_delete=models.SET_NULL)
    geo_region = models.ForeignKey('geo.Region', blank=True, null=True, on_delete=models.SET_NULL)
    geo_city = models.ForeignKey('geo.City', blank=True, null=True, on_delete=models.SET_NULL)
    lat = models.FloatField(_lazy(u'Latitude'), blank=True, null=True)
    lng = models.FloatField(_lazy(u'Longitude'), blank=True, null=True)

    # django-cities-light fields
    city = models.ForeignKey('cities_light.City', blank=True, null=True,
                             on_delete=models.SET_NULL)
    region = models.ForeignKey('cities_light.Region', blank=True, null=True,
                               on_delete=models.SET_NULL)
    country = models.ForeignKey('cities_light.Country', blank=True, null=True,
                                on_delete=models.SET_NULL)

    date_mozillian = models.DateField('When was involved with Mozilla',
                                      null=True, blank=True, default=None)
    timezone = models.CharField(max_length=100, blank=True, default='',
                                choices=zip(common_timezones, common_timezones))
    title = models.CharField(_lazy(u'What do you do for Mozilla?'),
                             max_length=70, blank=True, default='')

    story_link = models.URLField(
        _lazy(u'Link to your contribution story'),
        help_text=_lazy(u'If you have created something public that '
                        u'tells the story of how you came to be a '
                        u'Mozillian, specify that link here.'),
        max_length=1024, blank=True, default='')
    # This is the Auth0 user ID. We are saving only the primary here.
    auth0_user_id = models.CharField(max_length=1024, default='', blank=True)
    is_staff = models.BooleanField(default=False)

    def __unicode__(self):
        """Return this user's name when their profile is called."""
        return self.display_name

    def get_absolute_url(self):
        return reverse('phonebook:profile_view', args=[self.user.username])

    class Meta:
        db_table = 'profile'
        ordering = ['full_name']

    def __getattribute__(self, attrname):
        """Special privacy aware __getattribute__ method.

        This method returns the real value of the attribute of object,
        if the privacy_level of the attribute is at least as large as
        the _privacy_level attribute.

        Otherwise it returns a default privacy respecting value for
        the attribute, as defined in the privacy_fields dictionary.

        special_functions provides methods that privacy safe their
        respective properties, where the privacy modifications are
        more complex.
        """
        _getattr = (lambda x: super(UserProfile, self).__getattribute__(x))
        privacy_fields = UserProfile.privacy_fields()
        privacy_level = _getattr('_privacy_level')
        special_functions = {
            'accounts': '_accounts',
            'alternate_emails': '_alternate_emails',
            'email': '_primary_email',
            'is_public_indexable': '_is_public_indexable',
            'languages': '_languages',
            'vouches_made': '_vouches_made',
            'vouches_received': '_vouches_received',
            'vouched_by': '_vouched_by',
            'websites': '_websites',
            'identity_profiles': '_identity_profiles'
        }

        if attrname in special_functions:
            return _getattr(special_functions[attrname])

        if not privacy_level or attrname not in privacy_fields:
            return _getattr(attrname)

        field_privacy = _getattr('privacy_%s' % attrname)
        if field_privacy < privacy_level:
            return privacy_fields.get(attrname)

        return _getattr(attrname)

    def _filter_accounts_privacy(self, accounts):
        if self._privacy_level:
            return accounts.filter(privacy__gte=self._privacy_level)
        return accounts

    @property
    def _accounts(self):
        _getattr = (lambda x: super(UserProfile, self).__getattribute__(x))
        excluded_types = [ExternalAccount.TYPE_WEBSITE, ExternalAccount.TYPE_EMAIL]
        accounts = _getattr('externalaccount_set').exclude(type__in=excluded_types)
        return self._filter_accounts_privacy(accounts)

    @property
    def _alternate_emails(self):
        _getattr = (lambda x: super(UserProfile, self).__getattribute__(x))
        accounts = _getattr('externalaccount_set').filter(type=ExternalAccount.TYPE_EMAIL)
        return self._filter_accounts_privacy(accounts)

    @property
    def _identity_profiles(self):
        _getattr = (lambda x: super(UserProfile, self).__getattribute__(x))
        accounts = _getattr('idp_profiles').all()
        return self._filter_accounts_privacy(accounts)

    @property
    def _is_public_indexable(self):
        for field in PUBLIC_INDEXABLE_FIELDS:
            if getattr(self, field, None) and getattr(self, 'privacy_%s' % field, None) == PUBLIC:
                return True
        return False

    @property
    def _languages(self):
        _getattr = (lambda x: super(UserProfile, self).__getattribute__(x))
        if self._privacy_level > _getattr('privacy_languages'):
            return _getattr('language_set').none()
        return _getattr('language_set').all()

    @property
    def _primary_email(self):
        _getattr = (lambda x: super(UserProfile, self).__getattribute__(x))

        privacy_fields = UserProfile.privacy_fields()

        if self._privacy_level:
            # Try IDP contact first
            if self.idp_profiles.exists():
                contact_ids = self.identity_profiles.filter(primary_contact_identity=True)
                if contact_ids.exists():
                    return contact_ids[0].email
                return ''

            # Fallback to user.email
            if _getattr('privacy_email') < self._privacy_level:
                return privacy_fields['email']

        # In case we don't have a privacy aware attribute access
        if self.idp_profiles.filter(primary_contact_identity=True).exists():
            return self.idp_profiles.filter(primary_contact_identity=True)[0].email
        return _getattr('user').email

    @property
    def _vouched_by(self):
        privacy_level = self._privacy_level
        voucher = (UserProfile.objects.filter(vouches_made__vouchee=self)
                   .order_by('vouches_made__date'))

        if voucher.exists():
            voucher = voucher[0]
            if privacy_level:
                voucher.set_instance_privacy_level(privacy_level)
                for field in UserProfile.privacy_fields():
                    if getattr(voucher, 'privacy_%s' % field) >= privacy_level:
                        return voucher
                return None
            return voucher

        return None

    def _vouches(self, type):
        _getattr = (lambda x: super(UserProfile, self).__getattribute__(x))

        vouch_ids = []
        for vouch in _getattr(type).all():
            vouch.vouchee.set_instance_privacy_level(self._privacy_level)
            for field in UserProfile.privacy_fields():
                if getattr(vouch.vouchee, 'privacy_%s' % field, 0) >= self._privacy_level:
                    vouch_ids.append(vouch.id)
        vouches = _getattr(type).filter(pk__in=vouch_ids)

        return vouches

    @property
    def _vouches_made(self):
        _getattr = (lambda x: super(UserProfile, self).__getattribute__(x))
        if self._privacy_level:
            return self._vouches('vouches_made')
        return _getattr('vouches_made')

    @property
    def _vouches_received(self):
        _getattr = (lambda x: super(UserProfile, self).__getattribute__(x))
        if self._privacy_level:
            return self._vouches('vouches_received')
        return _getattr('vouches_received')

    @property
    def _websites(self):
        _getattr = (lambda x: super(UserProfile, self).__getattribute__(x))
        accounts = _getattr('externalaccount_set').filter(type=ExternalAccount.TYPE_WEBSITE)
        return self._filter_accounts_privacy(accounts)

    @property
    def display_name(self):
        return self.full_name

    @property
    def privacy_level(self):
        """Return user privacy clearance."""
        if self.user.is_superuser:
            return PRIVATE
        if self.groups.filter(name='staff').exists():
            return EMPLOYEES
        if self.is_vouched:
            return MOZILLIANS
        return PUBLIC

    @property
    def is_complete(self):
        """Tests if a user has all the information needed to move on
        past the original registration view.

        """
        return self.display_name.strip() != ''

    @property
    def is_public(self):
        """Return True is any of the privacy protected fields is PUBLIC."""
        # TODO needs update

        for field in type(self).privacy_fields():
            if getattr(self, 'privacy_%s' % field, None) == PUBLIC:
                return True
        return False

    @property
    def is_manager(self):
        return self.user.is_superuser

    @property
    def date_vouched(self):
        """ Return the date of the first vouch, if available."""
        vouches = self.vouches_received.all().order_by('date')[:1]
        if vouches:
            return vouches[0].date
        return None

    def set_instance_privacy_level(self, level):
        """Sets privacy level of instance."""
        self._privacy_level = level

    def set_privacy_level(self, level, save=True):
        """Sets all privacy enabled fields to 'level'."""
        for field in type(self).privacy_fields():
            setattr(self, 'privacy_%s' % field, level)
        if save:
            self.save()

    def get_photo_thumbnail(self, geometry='160x160', **kwargs):
        if 'crop' not in kwargs:
            kwargs['crop'] = 'center'

        if self.photo and default_storage.exists(self.photo.name):
            # Workaround for legacy images in RGBA model

            try:
                image_obj = Image.open(self.photo)
            except IOError:
                return get_thumbnail(settings.DEFAULT_AVATAR_PATH, geometry, **kwargs)

            if image_obj.mode == 'RGBA':
                new_fh = default_storage.open(self.photo.name, 'w')
                converted_image_obj = image_obj.convert('RGB')
                converted_image_obj.save(new_fh, 'JPEG')
                new_fh.close()

            return get_thumbnail(self.photo, geometry, **kwargs)
        return get_thumbnail(settings.DEFAULT_AVATAR_PATH.format(), geometry, **kwargs)

    def get_photo_url(self, geometry='160x160', **kwargs):
        """Return photo url.

        If privacy allows and no photo set, return gravatar link.
        If privacy allows and photo set return local photo link.
        If privacy doesn't allow return default local link.
        """
        privacy_level = getattr(self, '_privacy_level', MOZILLIANS)
        if (not self.photo and self.privacy_photo >= privacy_level):
            return gravatar(self.email, size=geometry)

        photo_url = self.get_photo_thumbnail(geometry, **kwargs).url
        if photo_url.startswith('https://') or photo_url.startswith('http://'):
            return photo_url
        return absolutify(photo_url)

    def is_vouchable(self, voucher):
        """Check whether self can receive a vouch from voucher."""
        # If there's a voucher, they must be able to vouch.
        if voucher and not voucher.can_vouch:
            return False

        # Maximum VOUCH_COUNT_LIMIT vouches per account, no matter what.
        if self.vouches_received.all().count() >= settings.VOUCH_COUNT_LIMIT:
            return False

        # If you've already vouched this account, you cannot do it again
        vouch_query = self.vouches_received.filter(voucher=voucher)
        if voucher and vouch_query.exists():
            return False

        return True


    def timezone_offset(self):
        """
        Return minutes the user's timezone is offset from UTC.  E.g. if user is
        4 hours behind UTC, returns -240.
        If user has not set a timezone, returns None (not 0).
        """
        if self.timezone:
            return offset_of_timezone(self.timezone)

    def save(self, *args, **kwargs):
        self._privacy_level = None
        autovouch = kwargs.pop('autovouch', False)

        super(UserProfile, self).save(*args, **kwargs)
        # Auto_vouch follows the first save, because you can't
        # create foreign keys without a database id.

        if autovouch:
            self.auto_vouch()


class IdpProfile(models.Model):
    """Basic Identity Provider information for Profiles."""
    PROVIDER_UNKNOWN = 0
    PROVIDER_PASSWORDLESS = 10
    PROVIDER_GOOGLE = 20
    PROVIDER_GITHUB = 30
    PROVIDER_FIREFOX_ACCOUNTS = 31
    PROVIDER_LDAP = 40

    PROVIDER_TYPES = (
        (PROVIDER_UNKNOWN, 'Unknown Provider',),
        (PROVIDER_PASSWORDLESS, 'Passwordless Provider',),
        (PROVIDER_GOOGLE, 'Google Provider',),
        (PROVIDER_GITHUB, 'Github Provider',),
        (PROVIDER_FIREFOX_ACCOUNTS, 'Firefox Accounts Provider',),
        (PROVIDER_LDAP, 'LDAP Provider',),

    )
    # High Security OPs
    HIGH_AAL_ACCOUNTS = [PROVIDER_LDAP,
                         PROVIDER_FIREFOX_ACCOUNTS,
                         PROVIDER_GITHUB,
                         PROVIDER_GOOGLE]

    profile = models.ForeignKey(UserProfile, related_name='idp_profiles')
    type = models.IntegerField(choices=PROVIDER_TYPES,
                               default=None,
                               null=True,
                               blank=False)
    # Auth0 required data
    auth0_user_id = models.CharField(max_length=1024, default='', blank=True)
    primary = models.BooleanField(default=False)
    email = models.EmailField(blank=True, default='')
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    privacy = models.PositiveIntegerField(default=MOZILLIANS, choices=PRIVACY_CHOICES_WITH_PRIVATE)
    primary_contact_identity = models.BooleanField(default=False)
    username = models.CharField(max_length=1024, default='', blank=True)

    def get_provider_type(self):
        """Helper method to autopopulate the model type given the user_id."""
        if 'ad|' in self.auth0_user_id:
            return self.PROVIDER_LDAP

        if 'oauth2|firefoxaccounts' in self.auth0_user_id:
            return self.PROVIDER_FIREFOX_ACCOUNTS

        if 'github|' in self.auth0_user_id:
            return self.PROVIDER_GITHUB

        if 'google-oauth2|' in self.auth0_user_id:
            return self.PROVIDER_GOOGLE

        if 'email|' in self.auth0_user_id:
            return self.PROVIDER_PASSWORDLESS

        return self.PROVIDER_UNKNOWN

    def save(self, *args, **kwargs):
        """Custom save method.

        Provides a default contact identity and a helper to assign the provider type.
        """
        self.type = self.get_provider_type()
        # If there isn't a primary contact identity, create one
        if not (IdpProfile.objects.filter(profile=self.profile,
                                          primary_contact_identity=True).exists()):
            self.primary_contact_identity = True

        super(IdpProfile, self).save(*args, **kwargs)

        # Save profile.privacy_email when a primary contact identity changes
        profile = self.profile
        if self.primary_contact_identity:
            profile.privacy_email = self.privacy
        # Set the user id in the userprofile too
        if self.primary:
            profile.auth0_user_id = self.auth0_user_id
        profile.save()

    def __unicode__(self):
        return u'{}|{}|{}'.format(self.profile, self.type, self.email)

    class Meta:
        unique_together = ('profile', 'type', 'email')


class Vouch(models.Model):
    vouchee = models.ForeignKey(UserProfile, related_name='vouches_received')
    voucher = models.ForeignKey(UserProfile, related_name='vouches_made',
                                null=True, default=None, blank=True,
                                on_delete=models.SET_NULL)
    description = models.TextField(max_length=500, verbose_name=_lazy(u'Reason for Vouching'),
                                   default='')
    autovouch = models.BooleanField(default=False)
    date = models.DateTimeField()

    class Meta:
        verbose_name_plural = 'vouches'
        unique_together = ('vouchee', 'voucher')
        ordering = ['-date']

    def __unicode__(self):
        return u'{0} vouched by {1}'.format(self.vouchee, self.voucher)


class UsernameBlacklist(models.Model):
    value = models.CharField(max_length=30, unique=True)
    is_regex = models.BooleanField(default=False)

    def __unicode__(self):
        return self.value

    class Meta:
        ordering = ['value']


class ExternalAccount(models.Model):
    # Constants for type field values.
    TYPE_AMO = 'AMO'
    TYPE_BMO = 'BMO'
    TYPE_EMAIL = 'EMAIL'
    TYPE_MDN = 'MDN'
    TYPE_SUMO = 'SUMO'
    TYPE_FACEBOOK = 'FACEBOOK'
    TYPE_TWITTER = 'TWITTER'
    TYPE_AIM = 'AIM'
    TYPE_SKYPE = 'SKYPE'
    TYPE_YAHOO = 'YAHOO'
    TYPE_WEBSITE = 'WEBSITE'
    TYPE_BITBUCKET = 'BITBUCKET'
    TYPE_SLIDESHARE = 'SLIDESHARE'
    TYPE_WEBMAKER = 'WEBMAKER'
    TYPE_MOWIKI = 'MOZILLAWIKI'
    TYPE_REMO = 'REMO'
    TYPE_LINKEDIN = 'LINKEDIN'
    TYPE_JABBER = 'JABBER'
    TYPE_DISCOURSE = 'DISCOURSE'
    TYPE_LANYRD = 'LANYRD'
    TYPE_LANDLINE = 'Phone (Landline)'
    TYPE_MOBILE = 'Phone (Mobile)'
    TYPE_MOPONTOON = 'MOZILLAPONTOON'
    TYPE_TRANSIFEX = 'TRANSIFEX'
    TYPE_TELEGRAM = 'TELEGRAM'
    TYPE_MASTODON = 'MASTODON'
    TYPE_DISCORD = 'DISCORD'
    TYPE_MOZPHAB = 'MOZPHAB'

    # Account type field documentation:
    # name: The name of the service that this account belongs to. What
    #       users see
    # url: If the service features profile pages for its users, then
    #      this field should be a link to that profile page. User's
    #      identifier should be replaced by the special string
    #      {identifier}.
    # validator: Points to a function which will clean and validate
    #            user's entry. Function should return the cleaned
    #            data.
    ACCOUNT_TYPES = {
        TYPE_AMO: {'name': 'Mozilla Add-ons',
                   'url': 'https://addons.mozilla.org/user/{identifier}/',
                   'validator': validate_username_not_url},
        TYPE_BMO: {'name': 'Bugzilla (BMO)',
                   'url': 'https://bugzilla.mozilla.org/user_profile?login={identifier}',
                   'validator': validate_username_not_url},
        TYPE_EMAIL: {'name': 'Alternate email address',
                     'url': '',
                     'validator': validate_email},
        TYPE_BITBUCKET: {'name': 'Bitbucket',
                         'url': 'https://bitbucket.org/{identifier}',
                         'validator': validate_username_not_url},
        TYPE_MDN: {'name': 'MDN',
                   'url': 'https://developer.mozilla.org/profiles/{identifier}',
                   'validator': validate_username_not_url},
        TYPE_SUMO: {'name': 'Mozilla Support',
                    'url': 'https://support.mozilla.org/user/{identifier}',
                    'validator': validate_username_not_url},
        TYPE_FACEBOOK: {'name': 'Facebook',
                        'url': 'https://www.facebook.com/{identifier}',
                        'validator': validate_username_not_url},
        TYPE_TWITTER: {'name': 'Twitter',
                       'url': 'https://twitter.com/{identifier}',
                       'validator': validate_twitter},
        TYPE_AIM: {'name': 'AIM', 'url': ''},
        TYPE_SKYPE: {'name': 'Skype', 'url': ''},
        TYPE_SLIDESHARE: {'name': 'SlideShare',
                          'url': 'http://www.slideshare.net/{identifier}',
                          'validator': validate_username_not_url},
        TYPE_YAHOO: {'name': 'Yahoo! Messenger', 'url': ''},
        TYPE_WEBSITE: {'name': 'Website URL',
                       'url': '',
                       'validator': validate_website},
        TYPE_WEBMAKER: {'name': 'Mozilla Webmaker',
                        'url': 'https://{identifier}.makes.org',
                        'validator': validate_username_not_url},
        TYPE_MOWIKI: {'name': 'Mozilla Wiki', 'url': 'https://wiki.mozilla.org/User:{identifier}',
                      'validator': validate_username_not_url},
        TYPE_REMO: {'name': 'Mozilla Reps', 'url': 'https://reps.mozilla.org/u/{identifier}/',
                    'validator': validate_username_not_url},
        TYPE_LINKEDIN: {'name': 'LinkedIn',
                        'url': 'https://www.linkedin.com/in/{identifier}/',
                        'validator': validate_linkedin},
        TYPE_JABBER: {'name': 'XMPP/Jabber',
                      'url': '',
                      'validator': validate_email},
        TYPE_MASTODON: {'name': 'Mastodon',
                        'url': '',
                        'validator': validate_email},
        TYPE_DISCOURSE: {'name': 'Mozilla Discourse',
                         'url': 'https://discourse.mozilla.org/users/{identifier}',
                         'validator': validate_username_not_url},
        TYPE_LANYRD: {'name': 'Lanyrd',
                      'url': 'http://lanyrd.com/profile/{identifier}/',
                      'validator': validate_username_not_url},
        TYPE_LANDLINE: {'name': 'Phone (Landline)',
                        'url': '',
                        'validator': validate_phone_number},
        TYPE_MOBILE: {'name': 'Phone (Mobile)',
                      'url': '',
                      'validator': validate_phone_number},
        TYPE_MOPONTOON: {'name': 'Mozilla Pontoon',
                         'url': 'https://pontoon.mozilla.org/contributor/{identifier}/',
                         'validator': validate_email},
        TYPE_TRANSIFEX: {'name': 'Transifex',
                         'url': 'https://www.transifex.com/accounts/profile/{identifier}/',
                         'validator': validate_username_not_url},
        TYPE_TELEGRAM: {'name': 'Telegram',
                        'url': 'https://telegram.me/{identifier}',
                        'validator': validate_username_not_url},
        TYPE_DISCORD: {'name': 'Discord',
                       'url': '',
                       'validator': validate_discord},
        TYPE_MOZPHAB: {'name': 'Mozilla Phabricator',
                       'url': 'https://phabricator.services.mozilla.com/p/{identifier}/',
                       'validator': validate_username_not_url},
    }

    user = models.ForeignKey(UserProfile)
    identifier = models.CharField(max_length=255, verbose_name=_lazy(u'Account Username'))
    type = models.CharField(max_length=30,
                            choices=sorted([(k, v['name']) for (k, v) in ACCOUNT_TYPES.iteritems()
                                            if k != TYPE_EMAIL], key=lambda x: x[1]),
                            verbose_name=_lazy(u'Account Type'))
    privacy = models.PositiveIntegerField(default=MOZILLIANS, choices=PRIVACY_CHOICES_WITH_PRIVATE)

    class Meta:
        ordering = ['type']
        unique_together = ('identifier', 'type', 'user')

    def get_identifier_url(self):
        url = self.ACCOUNT_TYPES[self.type]['url'].format(identifier=urlquote(self.identifier))
        if self.type == 'LINKEDIN' and '://' in self.identifier:
            return self.identifier

        return iri_to_uri(url)

    def unique_error_message(self, model_class, unique_check):
        if model_class == type(self) and unique_check == ('identifier', 'type', 'user'):
            return _('You already have an account with this name and type.')
        else:
            return super(ExternalAccount, self).unique_error_message(model_class, unique_check)

    def __unicode__(self):
        return self.type


class Language(models.Model):
    code = models.CharField(max_length=63, choices=get_languages_for_locale('en'))
    userprofile = models.ForeignKey(UserProfile)

    class Meta:
        ordering = ['code']
        unique_together = ('code', 'userprofile')

    def __unicode__(self):
        return self.code

    def get_english(self):
        return self.get_code_display()

    def get_native(self):
        if not getattr(self, '_native', None):
            languages = get_languages_for_locale(self.code)
            for code, language in languages:
                if code == self.code:
                    self._native = language
                    break
        return self._native

    def unique_error_message(self, model_class, unique_check):
        if (model_class == type(self) and unique_check == ('code', 'userprofile')):
            return _('This language has already been selected.')
        return super(Language, self).unique_error_message(model_class, unique_check)
