from mock import patch

from django.core import mail
from django.core.urlresolvers import reverse
from django.test.utils import override_script_prefix
from mozillians.common.tests import TestCase
from mozillians.groups.models import Group, GroupMembership
from mozillians.groups.tests import GroupFactory
from mozillians.users.tests import UserFactory
from nose.tools import eq_, ok_


class TestGroupRemoveMember(TestCase):
    def setUp(self):
        self.group = GroupFactory()
        self.member = UserFactory()
        self.group.add_member(self.member.userprofile)
        with override_script_prefix('/en-US/'):
            self.url = reverse('groups:remove_member',
                               kwargs={'url': self.group.url,
                                       'user_pk': self.member.userprofile.pk})

    def test_as_manager(self):
        # manager can remove another from a group they're not curator of
        user = UserFactory(manager=True)
        with self.login(user) as client:
            response = client.post(self.url, follow=False)
        eq_(302, response.status_code)
        ok_(not self.group.has_member(self.member.userprofile))

    def test_as_manager_from_unleavable_group(self):
        # manager can remove people even from unleavable groups
        user = UserFactory(manager=True)
        with self.login(user) as client:
            response = client.post(self.url, follow=False)
        eq_(302, response.status_code)
        ok_(not self.group.has_member(self.member.userprofile))

    def test_as_manager_removing_curator(self):
        # but even manager cannot remove a curator
        user = UserFactory(manager=True)
        self.group.curators.add(self.member.userprofile)
        with self.login(user) as client:
            response = client.post(self.url, follow=False)
        eq_(302, response.status_code)
        ok_(self.group.has_member(self.member.userprofile))

    def test_as_simple_user_removing_self(self):
        # user can remove themselves
        with self.login(self.member) as client:
            response = client.post(self.url, follow=False)
        eq_(302, response.status_code)
        ok_(not self.group.has_member(self.member.userprofile))

    def test_as_simple_user_removing_self_from_unleavable_group(self):
        # user cannot leave an unleavable group
        self.group.members_can_leave = False
        self.group.save()
        with self.login(self.member) as client:
            response = client.post(self.url, follow=False)
        eq_(302, response.status_code)
        ok_(self.group.has_member(self.member.userprofile))

    def test_as_simple_user_removing_another(self):
        # user cannot remove anyone else
        user = UserFactory()
        with self.login(user) as client:
            response = client.post(self.url, follow=False)
        eq_(404, response.status_code)

    def test_as_curator(self):
        # curator can remove another
        curator = UserFactory()
        self.group.curators.add(curator.userprofile)
        with self.login(curator) as client:
            response = client.post(self.url, follow=False)
        eq_(302, response.status_code)
        ok_(not self.group.has_member(self.member.userprofile))

    def test_as_curator_twice(self):
        # removing a second time doesn't blow up
        curator = UserFactory()
        self.group.curator = curator.userprofile
        self.group.save()
        with self.login(curator) as client:
            client.post(self.url, follow=False)
            client.post(self.url, follow=False)

    def test_as_curator_from_unleavable(self):
        # curator can remove another even from an unleavable group
        self.group.members_can_leave = False
        self.group.save()
        curator = UserFactory()
        self.group.curators.add(curator.userprofile)
        with self.login(curator) as client:
            response = client.post(self.url, follow=False)
        eq_(302, response.status_code)
        ok_(not self.group.has_member(self.member.userprofile))
