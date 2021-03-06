from mock import patch

from django.test.client import RequestFactory
from mozillians.common.tests import TestCase
from mozillians.groups import forms
from mozillians.groups.models import Group
from mozillians.groups.tests import GroupAliasFactory, GroupFactory
from mozillians.users.models import IdpProfile
from mozillians.users.tests import UserFactory
from nose.tools import eq_, ok_


class GroupCreateFormTests(TestCase):
    def test_group_creation(self):
        form_data = {'name': 'test group',
                     'accepting_new_members': Group.REVIEWED}
        form = forms.GroupCreateForm(data=form_data)
        ok_(form.is_valid())
        form.save()
        ok_(Group.objects.filter(name='test group').exists())

    def test_name_unique(self):
        group = GroupFactory.create()
        GroupAliasFactory.create(alias=group, name='bar')
        form = forms.GroupCreateForm({'name': 'bar',
                                      'accepting_new_members': Group.REVIEWED})
        ok_(not form.is_valid())
        ok_('name' in form.errors)
        msg = u'This name already exists.'
        ok_(msg in form.errors['name'])

    def test_creation_without_group_type(self):
        form_data = {'name': 'test group', 'is_access_group': False}
        form = forms.GroupCreateForm(data=form_data)
        ok_(not form.is_valid())
        msg = u'This field is required.'
        ok_(msg in form.errors['accepting_new_members'])
        eq_(len(form.errors), 1)

    def test_creation_access_group_anonymous(self):
        form_data = {'name': 'test group',
                     'is_access_group': True,
                     'accepting_new_members': Group.REVIEWED}
        form = forms.GroupCreateForm(data=form_data)
        ok_(not form.is_valid())
        msg = u'You do not have the permissions to provision an access group.'
        ok_(msg in form.errors['is_access_group'])
        eq_(len(form.errors), 1)

    def test_creation_access_group_superuser(self):
        superuser = UserFactory.create(is_superuser=True)
        request = RequestFactory().request()
        request.user = superuser
        form_data = {'name': 'test group',
                     'is_access_group': True,
                     'accepting_new_members': 'by_request'}
        form = forms.GroupCreateForm(data=form_data, request=request)
        ok_(form.is_valid())

    def test_creation_access_group_LDAP_Idp(self):
        user = UserFactory.create()
        IdpProfile.objects.create(
            profile=user.userprofile,
            auth0_user_id='ad|foo@mozillafoundation.org',
            email='foo@mozillafoundation.org',
            primary=True,
            primary_contact_identity=True
        )
        request = RequestFactory().request()
        request.user = user
        form_data = {'name': 'test group',
                     'is_access_group': True,
                     'accepting_new_members': 'by_request'}
        form = forms.GroupCreateForm(data=form_data, request=request)
        ok_(form.is_valid())

    def test_creation_access_group_without_LDAP_Idp(self):
        user = UserFactory.create()
        IdpProfile.objects.create(
            profile=user.userprofile,
            auth0_user_id='email|foo@bar.com',
            email='foo@bar.com',
            primary=True,
            primary_contact_identity=True
        )
        request = RequestFactory().request()
        request.user = user
        form_data = {'name': 'test group',
                     'is_access_group': True,
                     'accepting_new_members': 'by_request'}
        form = forms.GroupCreateForm(data=form_data, request=request)
        ok_(not form.is_valid())
        msg = u'You do not have the permissions to provision an access group.'
        ok_(msg in form.errors['is_access_group'])
        eq_(len(form.errors), 1)

    def test_creation_access_group_with_type_open(self):
        user = UserFactory.create()
        IdpProfile.objects.create(
            profile=user.userprofile,
            auth0_user_id='ad|foo@bar.com',
            email='foo@bar.com',
            primary=True,
            primary_contact_identity=True
        )
        request = RequestFactory().request()
        request.user = user
        form_data = {'name': 'test group',
                     'is_access_group': True,
                     'accepting_new_members': Group.OPEN}
        form = forms.GroupCreateForm(data=form_data, request=request)
        ok_(not form.is_valid())
        msg = u'Group must be of type Reviewed or Closed for Access Groups.'
        ok_(msg in form.errors['is_access_group'])
        eq_(len(form.errors), 1)

    def test_creation_access_group_with_type_reviewed(self):
        user = UserFactory.create()
        IdpProfile.objects.create(
            profile=user.userprofile,
            auth0_user_id='ad|foo@mozilla.com',
            email='foo@mozilla.com',
            primary=True,
            primary_contact_identity=True
        )
        request = RequestFactory().request()
        request.user = user
        form_data = {'name': 'test group',
                     'is_access_group': True,
                     'accepting_new_members': Group.REVIEWED}
        form = forms.GroupCreateForm(data=form_data, request=request)
        ok_(form.is_valid())

    def test_creation_access_group_with_type_closed(self):
        user = UserFactory.create()
        IdpProfile.objects.create(
            profile=user.userprofile,
            auth0_user_id='ad|foo@mozilla.com',
            email='foo@mozilla.com',
            primary=True,
            primary_contact_identity=True,
        )
        request = RequestFactory().request()
        request.user = user
        form_data = {'name': 'test group',
                     'is_access_group': True,
                     'accepting_new_members': Group.CLOSED}
        form = forms.GroupCreateForm(data=form_data, request=request)
        ok_(form.is_valid())

    def test_legacy_group_curators_validation(self):
        group = GroupFactory.create()
        request = RequestFactory().request()
        request.user = None

        # Update form without adding curators
        form_data = {'name': 'test_group'}
        form = forms.GroupCuratorsForm(instance=group, data=form_data, request=request)

        ok_(form.is_valid())

        # Ensure that groups has no curators
        group = Group.objects.get(id=group.id)
        ok_(not group.curators.exists())

    def test_group_curators_validation(self):
        group = GroupFactory.create()
        curator = UserFactory.create()
        request = RequestFactory().request()
        request.user = curator
        group.curators.add(curator.userprofile)

        # Update form without adding curators
        form_data = {'name': 'test_group',
                     'curators': []}
        form = forms.GroupCuratorsForm(instance=group, data=form_data, request=request)

        ok_(not form.is_valid())
        eq_(form.errors, {'curators': [u'The group must have at least one curator.']})


class BaseGroupEditTestCase(TestCase):

    def validate_group_edit_forms(self, form_class, instance, data, request=None, valid=True):
        form = form_class(instance=instance, data=data, request=request)

        if valid:
            ok_(form.is_valid())
            form.save()

            # Get the object from the db
            obj = instance._meta.model.objects.get(pk=instance.pk)
            # compare the value of each field in the object with the ones in the data dict
            for field in [f for f in obj._meta.fields if f.name in data.keys()]:
                eq_(field.value_from_object(obj), data[field.name])
        else:
            ok_(not form.is_valid())

        return form


class GroupEditFormTests(BaseGroupEditTestCase):

    def test_edit_basic_form_with_data(self):
        group = GroupFactory.create()
        data = {'name': 'test group',
                'description': 'sample description',
                'irc_channel': 'foobar',
                'website': 'https://example.com',
                'wiki': 'https://example-wiki.com'}
        self.validate_group_edit_forms(forms.GroupBasicForm, group, data)

    def test_edit_basic_form_without_data(self):
        group = GroupFactory.create()
        data = {}
        form = self.validate_group_edit_forms(forms.GroupBasicForm, group, data, None, False)
        eq_(form.errors, {'name': [u'This field is required.']})

    def test_edit_curators(self):
        curator = UserFactory.create()
        group = GroupFactory.create()
        request = RequestFactory().request()
        request.user = curator

        data = {'curators': [curator.id]}
        self.validate_group_edit_forms(forms.GroupCuratorsForm, group, data, request)

    def test_edit_terms(self):
        group = GroupFactory.create()
        data = {'terms': 'foobar'}
        self.validate_group_edit_forms(forms.GroupTermsExpirationForm, group, data)

    def test_edit_terms_without_data(self):
        group = GroupFactory.create()
        data = {}
        self.validate_group_edit_forms(forms.GroupTermsExpirationForm, group, data)

    def test_edit_invalidation(self):
        group = GroupFactory.create()
        data = {'invalidation_days': 5}
        self.validate_group_edit_forms(forms.GroupTermsExpirationForm, group, data)

    def test_edit_invalidation_invalid_data(self):
        group = GroupFactory.create()
        data = {'invalidation_days': 1000}
        form = self.validate_group_edit_forms(forms.GroupTermsExpirationForm, group,
                                              data, None, False)
        eq_(form.errors, {'invalidation_days': [u'The maximum expiration date for a group '
                                                'cannot exceed two years.']})

    def test_edit_terms_and_invalidation(self):
        group = GroupFactory.create()
        data = {'terms': 'foobar',
                'invalidation_days': 40}
        self.validate_group_edit_forms(forms.GroupTermsExpirationForm, group, data)

    def test_edit_invitation_without_curator(self):
        invitee = UserFactory.create()
        group = GroupFactory.create()
        request = RequestFactory().request()
        request.user = UserFactory.create()
        data = {'invites': [invitee.userprofile.id]}

        form = self.validate_group_edit_forms(forms.GroupInviteForm, group, data, request, False)
        eq_(form.errors, {'invites': [u'You need to be the curator of this group before '
                                      'inviting someone to join.']})

    def test_edit_admin_without_permissions(self):
        group = GroupFactory.create()
        data = {}
        request = RequestFactory().request()
        request.user = UserFactory.create()
        form = self.validate_group_edit_forms(forms.GroupAdminForm, group, data, request, False)
        eq_(form.errors, {'__all__': [u'You need to be the administrator of this group '
                                      'in order to edit this section.']})

    def test_edit_admin(self):
        group = GroupFactory.create()
        request = RequestFactory().request()
        request.user = UserFactory.create(is_superuser=True)
        data = {'functional_area': True,
                'visible': True,
                'members_can_leave': True}
        self.validate_group_edit_forms(forms.GroupAdminForm, group, data, request)

    def test_email_invite(self):
        curator = UserFactory.create()
        group = GroupFactory.create()
        group.curators.add(curator.userprofile)
        request = RequestFactory().request()
        request.user = curator
        data = {'invite_email_text': u'Custom message in the email.'}
        self.validate_group_edit_forms(forms.GroupCustomEmailForm, group, data, request)


class GroupCriteriaFormTests(TestCase):
    def test_access_group_type_open(self):
        curator = UserFactory.create()
        group = GroupFactory.create(is_access_group=True)
        group.curators.add(curator.userprofile)
        request = RequestFactory().request()
        request.user = curator
        form_data = {'accepting_new_members': Group.OPEN}
        form = forms.GroupCriteriaForm(instance=group, data=form_data)
        ok_(not form.is_valid())
        eq_(len(form.errors), 1)

    def test_access_group_type_reviewed(self):
        curator = UserFactory.create()
        group = GroupFactory.create(is_access_group=True)
        group.curators.add(curator.userprofile)
        request = RequestFactory().request()
        request.user = curator
        form_data = {'accepting_new_members': Group.REVIEWED,
                     'new_member_criteria': 'Criteria'}
        form = forms.GroupCriteriaForm(data=form_data)
        ok_(form.is_valid())

    def test_access_group_type_closed(self):
        curator = UserFactory.create()
        group = GroupFactory.create(is_access_group=True)
        group.curators.add(curator.userprofile)
        request = RequestFactory().request()
        request.user = curator
        form_data = {'accepting_new_members': Group.CLOSED}
        form = forms.GroupCriteriaForm(data=form_data)
        ok_(form.is_valid())
