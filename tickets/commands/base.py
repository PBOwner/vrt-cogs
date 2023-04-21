import asyncio
import datetime
import logging
from abc import ABC
from io import BytesIO
from typing import Union

import discord
from discord import app_commands
from discord.utils import escape_markdown
from redbot.core import commands
from redbot.core.commands import parse_timedelta
from redbot.core.i18n import Translator
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.mod import is_admin_or_superior

from ..abc import MixinMeta

LOADING = "https://i.imgur.com/l3p6EMX.gif"
log = logging.getLogger("red.vrt.tickets.base")
_ = Translator("Tickets", __file__)


class BaseCommands(MixinMeta, ABC):
    @commands.hybrid_command(
        name="add", description="Add a user to your ticket"
    )
    @app_commands.describe(
        user="The Discord user you want to add to your ticket"
    )
    async def add_user_to_ticket(
        self, ctx: commands.Context, *, user: discord.Member
    ):
        """Add a user to your ticket"""
        conf = await self.config.guild(ctx.guild).all()
        opened = conf["opened"]
        owner_id = self.get_ticket_owner(opened, str(ctx.channel.id))
        if not owner_id:
            return await ctx.send(
                _(
                    "This is not a ticket channel, or it has been removed from config"
                )
            )
        # If a mod tries
        can_add = False
        for role in ctx.author.roles:
            if role.id in conf["support_roles"]:
                can_add = True
        if ctx.author.id == ctx.guild.owner_id:
            can_add = True
        if await is_admin_or_superior(self.bot, ctx.author):
            can_add = True
        if owner_id == str(ctx.author.id) and conf["user_can_manage"]:
            can_add = True
        if not can_add:
            return await ctx.send(
                _("You do not have permissions to add users to this ticket")
            )
        channel = ctx.channel
        if isinstance(channel, discord.TextChannel):
            await ctx.channel.set_permissions(
                user, read_messages=True, send_messages=True
            )
        else:
            await channel.add_user(user)
        await ctx.send(
            f"**{user.name}** " + _("has been added to this ticket!")
        )

    @commands.hybrid_command(
        name="renameticket", description="Rename your ticket"
    )
    @app_commands.describe(new_name="The new name for your ticket")
    async def rename_ticket(self, ctx: commands.Context, *, new_name: str):
        """Rename your ticket channel"""
        conf = await self.config.guild(ctx.guild).all()
        opened = conf["opened"]
        owner_id = self.get_ticket_owner(opened, str(ctx.channel.id))
        if not owner_id:
            return await ctx.send(
                _(
                    "This is not a ticket channel, or it has been removed from config"
                )
            )
        can_rename = False
        for role in ctx.author.roles:
            if role.id in conf["support_roles"]:
                can_rename = True
        if ctx.author.id == ctx.guild.owner_id:
            can_rename = True
        if await is_admin_or_superior(self.bot, ctx.author):
            can_rename = True
        if owner_id == str(ctx.author.id) and conf["user_can_rename"]:
            can_rename = True
        if not can_rename:
            return await ctx.send(
                _("You do not have permissions to rename this ticket")
            )
        await ctx.channel.edit(name=new_name)
        # Threads already alert to name changes
        if isinstance(ctx.channel, discord.TextChannel):
            await ctx.send(_("Ticket has been renamed"))

    @commands.hybrid_command(name="close", description="Close your ticket")
    @app_commands.describe(reason="Reason for closing the ticket")
    async def close_a_ticket(
        self, ctx: commands.Context, *, reason: str = None
    ):
        """
        Close your ticket

        **Examples**
        `[p]close` - closes ticket with no reason attached
        `[p]close thanks for helping!` - closes with reason "thanks for helping!"
        `[p]close 1h` - closes in 1 hour with no reason attached
        `[p]close 1m thanks for helping!` - closes in 1 minute with reason "thanks for helping!"
        """
        user = ctx.author
        conf = await self.config.guild(ctx.guild).all()
        opened = conf["opened"]
        owner_id = self.get_ticket_owner(opened, str(ctx.channel.id))
        if not owner_id:
            return await ctx.send(
                _(
                    "This is not a ticket channel, or it has been removed from config"
                )
            )
        can_close = False
        for role in user.roles:
            if role.id in conf["support_roles"]:
                can_close = True
        if user.id == ctx.guild.owner_id:
            can_close = True
        if await is_admin_or_superior(self.bot, user):
            can_close = True
        if owner_id == str(user.id) and conf["user_can_close"]:
            can_close = True

        if not can_close:
            return await ctx.send(
                _("You do not have permissions to close this ticket")
            )
        else:
            owner = ctx.guild.get_member(int(owner_id))
            if not owner:
                owner = await self.bot.fetch_user(int(owner_id))

        if reason:
            timestring = reason.split(" ")[0]
            if td := parse_timedelta(timestring):

                def check(m: discord.Message):
                    return m.channel.id == ctx.channel.id

                reason = reason.replace(timestring, "")
                if not reason.strip():
                    # User provided delayed close with no reason attached
                    reason = None
                closing_in = int((datetime.datetime.now() + td).timestamp())
                closemsg = _("This ticket will close {}").format(
                    f"<t:{closing_in}:R>"
                )
                msg = await ctx.send(f"{owner.mention}, {closemsg}")
                try:
                    await ctx.bot.wait_for(
                        "message", check=check, timeout=td.total_seconds()
                    )
                except asyncio.TimeoutError:
                    pass
                else:
                    cancelled = _("Closing cancelled!")
                    await msg.edit(content=cancelled)
                    return

        await self.close_ticket(
            owner, ctx.channel, conf, reason, ctx.author.name
        )

    async def close_ticket(
        self,
        member: Union[discord.Member, discord.User],
        channel: discord.TextChannel,
        conf: dict,
        reason: str,
        closedby: str,
    ):
        opened = conf["opened"]
        if not opened:
            return
        uid = str(member.id)
        cid = str(channel.id)
        if uid not in opened:
            return
        if cid not in opened[uid]:
            return

        ticket = opened[uid][cid]
        pfp = ticket["pfp"]
        opened = ticket["opened"]
        panel_name = ticket["panel"]
        panel = conf["panels"][panel_name]
        threads = panel.get("threads")
        if (
            not channel.permissions_for(member.guild.me).manage_channels
            and not threads
        ):
            return await channel.send(
                _(
                    "I am missing the `Manage Channels` permission to close this ticket!"
                )
            )
        if (
            not channel.permissions_for(member.guild.me).manage_threads
            and threads
        ):
            return await channel.send(
                _(
                    "I am missing the `Manage Threads` permission to close this ticket!"
                )
            )

        opened = int(datetime.datetime.fromisoformat(opened).timestamp())
        closed = int(datetime.datetime.now().timestamp())
        closer_name = escape_markdown(closedby)
        desc = (
            _("Ticket created by ")
            + f"**{member.name}-{member.id}**"
            + _(" has been closed.\n")
        )
        desc += _("`PanelType: `") + f"{panel_name}\n"
        desc += _("`Opened on: `") + f"<t:{opened}:F>\n"
        desc += _("`Closed on: `") + f"<t:{closed}:F>\n"
        desc += _("`Closed by: `") + f"{closer_name}\n"
        desc += _("`Reason:    `") + str(reason)
        embed = discord.Embed(
            title=_("Ticket Closed"),
            description=desc,
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=pfp)
        log_chan = (
            self.bot.get_channel(panel["log_channel"])
            if panel["log_channel"]
            else None
        )
        text = ""
        filename = f"{member.name}-{member.id}.txt"
        filename = filename.replace("/", "")
        if conf["transcript"]:
            em = discord.Embed(
                description=_("Archiving channel..."),
                color=discord.Color.magenta(),
            )
            em.set_footer(text=_("This channel will be deleted once complete"))
            em.set_thumbnail(url=LOADING)
            await channel.send(embed=em)
            answers = ticket.get("answers")
            if answers:
                r = _("Response")
                for q, a in answers.items():
                    text += (
                        f"{self.bot.user.display_name}: {q}\n" f"{r}: {a}\n"
                    )
            history = await self.fetch_channel_history(channel)
            for msg in history:
                if msg.author.id == self.bot.user.id:
                    continue
                if not msg:
                    continue
                att = [a.filename for a in msg.attachments]
                if msg.content:
                    text += f"{msg.author.name}: {msg.content}\n"
                if att:
                    text += _("Files uploaded: ") + humanize_list(att) + "\n"

        # Send off new messages
        if log_chan and ticket["logmsg"]:
            if text:
                file = discord.File(BytesIO(text.encode()), filename=filename)
                await log_chan.send(embed=embed, file=file)
            else:
                await log_chan.send(embed=embed)

            # Delete old log msg
            log_msg_id = ticket["logmsg"]
            try:
                log_msg = await log_chan.fetch_message(log_msg_id)
            except discord.HTTPException:
                log.warning("Failed to get log channel message")
                log_msg = None
            if log_msg:
                try:
                    await log_msg.delete()
                except Exception as e:
                    log.warning(f"Failed to auto-delete log message: {e}")

        if conf["dm"]:
            try:
                if text:
                    file = discord.File(
                        BytesIO(text.encode()), filename=filename
                    )
                    await member.send(embed=embed, file=file)
                else:
                    await member.send(embed=embed)
            except discord.Forbidden:
                pass

        # Delete ticket channel
        try:
            await channel.delete()
        except Exception as e:
            log.warning(f"Failed to delete ticket channel: {e}")

        async with self.config.guild(member.guild).opened() as tickets:
            if uid not in tickets:
                return
            if cid not in tickets[uid]:
                return
            del tickets[uid][cid]

    @staticmethod
    async def fetch_channel_history(channel: discord.TextChannel):
        history = []
        async for msg in channel.history(oldest_first=True):
            history.append(msg)
        return history

    @staticmethod
    async def ticket_owner_hastyped(
        channel: discord.TextChannel, user: discord.Member
    ):
        async for msg in channel.history(limit=50, oldest_first=True):
            if msg.author.id == user.id:
                return True

    @staticmethod
    def get_ticket_owner(opened: dict, channel_id: str):
        for uid, tickets in opened.items():
            if channel_id in tickets:
                return uid