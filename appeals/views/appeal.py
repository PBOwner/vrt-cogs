import discord
from redbot.core import commands
from redbot.core.bot import Red

from ..abc import MixinMeta
from ..db.tables import AppealGuild, AppealSubmission
from .submission import SubmissionView


class AppealView(discord.ui.View):
    def __init__(self, custom_id: str) -> None:
        super().__init__(timeout=None)
        self.submit_appeal.custom_id = custom_id
        self.cooldown = commands.CooldownMapping.from_cooldown(1, 60, commands.BucketType.user)

    async def on_timeout(self) -> None:
        await super().on_timeout()

    async def on_error(self, error: Exception, item: discord.ui.Item, interaction: discord.Interaction) -> None:
        await super().on_error(error, item, interaction)

    @discord.ui.button(label="Submit Appeal", style=discord.ButtonStyle.primary)
    async def submit_appeal(self, interaction: discord.Interaction, button: discord.Button):
        bucket = self.cooldown.get_bucket(interaction.message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            return await interaction.response.send_message(f"Try again in {retry_after:.0f} seconds", ephemeral=True)
        bot: Red = interaction.client
        cog: MixinMeta | None = bot.get_cog("Appeals")
        if not cog:
            return await interaction.response.send_message(
                "The Appeals cog is not loaded, try again later", ephemeral=True
            )
        if not cog.db:
            return await interaction.response.send_message(
                "Database connection is not active, try again later", ephemeral=True
            )

        ready, reason = await cog.conditions_met(interaction.guild)
        if not ready:
            return await interaction.response.send_message(f"Appeal system not ready: {reason}", ephemeral=True)
        appealguild = await AppealGuild.objects().get(AppealGuild.id == interaction.guild.id)
        if not appealguild:
            return await interaction.response.send_message(
                "Appeal system is no longer setup for this server", ephemeral=True
            )
        target_guild = bot.get_guild(appealguild.target_guild_id)
        if not target_guild:
            return await interaction.response.send_message(
                "The server you're appealing for is no longer available", ephemeral=True
            )
        if not target_guild.me.guild_permissions.ban_members:
            return await interaction.response.send_message(
                "I don't have permission to unban members in the server you're appealing for!", ephemeral=True
            )
        is_admin = await bot.is_admin(interaction.user)
        if not is_admin and interaction.user.id in [m.id for m in target_guild.members]:
            return await interaction.response.send_message(
                "You're already a member of the server you're appealing for, which means you aren't banned!",
                ephemeral=True,
            )

        refresh = False
        if self.submit_appeal.label != appealguild.button_label:
            self.submit_appeal.label = appealguild.button_label
            refresh = True
        if self.submit_appeal.style != getattr(discord.ButtonStyle, appealguild.button_style):
            self.submit_appeal.style = getattr(discord.ButtonStyle, appealguild.button_style)
            refresh = True
        if self.submit_appeal.emoji != appealguild.get_emoji(bot):
            self.submit_appeal.emoji = appealguild.get_emoji(bot)
            refresh = True

        if refresh:
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)

        if not is_admin:
            try:
                await target_guild.fetch_ban(interaction.user)
            except discord.NotFound:
                return await interaction.followup.send(
                    "You're not banned from the server you're appealing for!", ephemeral=True
                )

        if appealguild.appeal_limit == 1:
            existing = await AppealSubmission.objects().get(
                (AppealSubmission.guild == interaction.guild.id) & (AppealSubmission.user_id == interaction.user.id)
            )
            if existing:
                ts, relative = existing.created("F"), existing.created("R")
                txt = f"You have already submitted an appeal on {ts} ({relative})"
                if existing.status != "pending":
                    txt += f" and it was **{existing.status.capitalize()}**"
                return await interaction.followup.send(txt, ephemeral=True)
        else:
            pending = await AppealSubmission.objects().get(
                (AppealSubmission.guild == interaction.guild.id)
                & (AppealSubmission.user_id == interaction.user.id)
                & (AppealSubmission.status == "pending")
            )
            if pending:
                ts, relative = pending.created("F"), pending.created("R")
                txt = f"You still have a pending appeal submitted on {ts} ({relative})"
                return await interaction.followup.send(txt, ephemeral=True)
            existing = await AppealSubmission.objects().where(
                (AppealSubmission.guild == interaction.guild.id) & (AppealSubmission.user_id == interaction.user.id)
            )
            if len(existing) >= appealguild.appeal_limit:
                return await interaction.followup.send(
                    f"You have already submitted {len(existing)} appeals which is the maximum allowed",
                    ephemeral=True,
                )

        questions = await cog.db_utils.get_sorted_questions(interaction.guild.id)
        view = SubmissionView(questions)
        embed = await view.make_embed()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
