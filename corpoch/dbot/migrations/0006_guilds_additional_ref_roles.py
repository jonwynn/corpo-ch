from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dbot', '0005_chemoji_name_alter_chemoji_icon'),
    ]

    operations = [
        migrations.AddField(
            model_name='guilds',
            name='additional_ref_roles',
            field=models.ManyToManyField(
                blank=True,
                help_text='Additional roles in this guild that can start matches. Any configured referee role is sufficient.',
                related_name='additional_ref_guilds',
                to='dbot.roles',
                verbose_name='Additional Discord Ref Roles',
            ),
        ),
    ]
