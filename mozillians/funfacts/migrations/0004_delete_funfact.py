# -*- coding: utf-8 -*-
# Generated by Django 1.11.25 on 2020-09-25 15:08
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('funfacts', '0003_auto_20180110_0726'),
    ]

    operations = [
        migrations.DeleteModel(
            name='FunFact',
        ),
    ]