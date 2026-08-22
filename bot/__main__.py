"""Entry point: python -m bot"""

import logging
import sys
from logging.handlers import RotatingFileHandler

import discord

from . import config, db
from .client import JobBotClient


def _setup_logging(cfg: config.Config) -> None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # encoding is not optional here: this project logs plenty of CJK, and the
    # Windows default (cp950) would raise UnicodeEncodeError mid-write.
    file_handler = RotatingFileHandler(
        cfg.log_dir / "bot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(cfg.log_level)
    root.addHandler(file_handler)
    root.addHandler(stream)

    logging.getLogger("discord").setLevel(logging.WARNING)


def main() -> int:
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(f"設定錯誤: {e}", file=sys.stderr)
        return 1

    _setup_logging(cfg)
    log = logging.getLogger("bot")
    log.info("starting; push time %s %s, db %s",
             cfg.push_time.strftime("%H:%M"), cfg.tz, cfg.db_path)

    conn = db.connect(cfg.db_path)
    client = JobBotClient(cfg, conn)

    try:
        client.run(cfg.token, log_handler=None)
    except discord.LoginFailure:
        log.error("Discord 拒絕了這個 token。請確認 .env 裡的 DISCORD_TOKEN 是否正確／已被重設。")
        return 1
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
