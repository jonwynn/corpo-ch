from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("corpoch", "0029_alter_discordtoken_options_alter_discorduser_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="action_revision",
            field=models.PositiveBigIntegerField(
                default=0, editable=False,
                help_text="Invalidates delayed sporting actions after any match correction.",
            ),
        ),
        migrations.AddField(
            model_name="matchban",
            name="action_phase",
            field=models.CharField(
                choices=[("unknown", "Unknown"), ("opening", "Opening"), ("tiebreaker", "Tiebreaker")],
                default="unknown",
                help_text="Verified phase of the surviving ban or save.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="matchround",
            name="selection_kind",
            field=models.CharField(
                choices=[
                    ("unknown", "Unknown"), ("player", "Player"),
                    ("referee", "Referee"), ("automatic", "Automatic"),
                ],
                default="unknown",
                help_text="Verified origin of the surviving chart selection.",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="bracketrules",
            name="tb_ruleset",
            field=models.CharField(
                choices=[
                    ("single", "Single TB"), ("csc", "CSC TB Rules"),
                    ("banpick", "'NPDO' Ban/Pick"), ("refdecide", "Ref picks from unplayed"),
                    ("bansave", "Ban-Save"), ("corp_cup", "CORP Cup: opening bans only, loser picks"),
                ],
                default="single", help_text="Ruleset to determine how tiebreakers player.",
                max_length=32, verbose_name="Tiebreaker Ruleset",
            ),
        ),
    ]
