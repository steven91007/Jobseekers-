"""The Discord client: command sync, the ticker, and lifecycle cleanup."""

import logging

import discord
from discord import app_commands
from discord.ext import tasks

from . import commands as bot_commands
from . import db
from .config import Config
from .pusher import Pusher

log = logging.getLogger(__name__)

TICK_MINUTES = 5


class JobBotClient(discord.Client):
    def __init__(self, cfg: Config, conn):
        # Intents.default() is entirely non-privileged, but unlike Intents.none()
        # it keeps the guilds intent — without which there is no channel cache,
        # so get_channel() and guild.me would both come back empty.
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.conn = conn
        self.tree = app_commands.CommandTree(self)
        self.pusher = Pusher(self, conn, cfg)
        self._swept = False

    async def setup_hook(self) -> None:
        bot_commands.setup(self.tree, self.conn, self.cfg, self.pusher)

        if self.cfg.dev_guild_id:
            guild = discord.Object(id=self.cfg.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("synced %d commands to dev guild %s",
                     len(synced), self.cfg.dev_guild_id)
        else:
            synced = await self.tree.sync()
            log.info("synced %d global commands (may take up to 1h to appear)",
                     len(synced))

        # Started here rather than in on_ready, which fires again on every
        # reconnect and would stack duplicate loops.
        self.ticker.start()

    async def on_ready(self) -> None:
        log.info("logged in as %s (id=%s)", self.user, self.user.id)
        if not self._swept:
            self._swept = True
            await self._sweep_orphans()

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        count = db.deactivate_guild(self.conn, guild.id, "left_guild")
        if count:
            log.info("left guild %s, deactivated %d subscriptions", guild.id, count)

    async def _sweep_orphans(self) -> None:
        """Deactivate subscriptions whose channel is provably gone.

        Only NotFound counts. A cold cache makes get_channel() return None for
        perfectly healthy channels, so treating that as missing would
        mass-deactivate everything on startup.
        """
        for sub in db.due_subscriptions(self.conn):
            if self.get_channel(sub.channel_id) is not None:
                continue
            try:
                await self.fetch_channel(sub.channel_id)
            except discord.NotFound:
                db.set_active(self.conn, sub.id, False, "channel_deleted")
                log.warning("sub #%s: channel %s gone, deactivated",
                            sub.id, sub.channel_id)
            except discord.HTTPException:
                pass

    @tasks.loop(minutes=TICK_MINUTES)
    async def ticker(self) -> None:
        # The loop body is wrapped so it is structurally incapable of raising:
        # an unhandled exception stops a tasks.loop permanently while the bot
        # stays online looking perfectly healthy.
        try:
            await self.pusher.tick()
        except Exception:
            log.exception("tick failed")

    @ticker.before_loop
    async def _before_ticker(self) -> None:
        await self.wait_until_ready()

    @ticker.error
    async def _ticker_error(self, exc: BaseException) -> None:
        log.exception("ticker crashed, restarting", exc_info=exc)
        self.ticker.restart()

    async def close(self) -> None:
        self.ticker.cancel()
        await super().close()
