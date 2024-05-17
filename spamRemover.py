import gd
import yaml
from aiohttp import ClientSession
import asyncio
import random
import re
from colorama import Fore, init


# Inspired by: https://github.com/ThioJoe/YT-Spammer-Purge
# SEE: https://www.youtube.com/watch?v=-vOakOgYLUI


# Slightly modified version of my last one that should get through the majority of even obfuscated shady invite links
# Throw me an issue on github if this fails to pickup new spammer techniques
DISCORD_INVITE_REGEX = re.compile(
    r"(?:(?:(?:disc(?:o|0)rd(?:.|,)"
    r"(?:gg|io|me|li))|"
    r"(?:(?:disc(?:o|0)rd(?:a|\@)pp(?:.|,)com/invite)))"
    r"(?:[^/s]*)/([^\s/]+))"
)


# TODO: Optionally flag Grabify links as spam, even if they have no dangerous redirections


class BlackListManager:
    """Manages all blacklisted guilds and handles flagged content"""

    def __init__(self, servers: list[int]) -> None:
        self.guildIDs = set(map(str, servers))
        self.cache: dict[str, str] = {}
        self.whitelisted: dict[str, str] = {}

    async def guild_id_for_invite(self, invite: str):
        async with ClientSession() as client:
            async with client.get(
                "https://discord.com/api/v10/invites/{}".format(invite)
            ) as resp:
                if resp.status != 200:
                    await resp.close()
                    return None
                _json = await resp.json()
        try:
            return _json["guild_id"]
        # Non Existant Key
        except KeyError:
            return

    async def invite_is_spam(self, invite: str):

        # Some urls may contain external parameters so we may need to cleanse these off,
        # there primarly used by other spammers as obfuscation techniques...
        for splitter in ["?", "&", "|", "<", ">", "~", "/", "$", "^", "*", "!"]:
            if splitter in invite:
                invite = invite.split(splitter, 1)[0]

        if self.whitelisted.get(invite):
            return False

        elif self.cache.get(invite):
            return self.cache[invite] != "NONE"

        elif _id := await self.guild_id_for_invite(invite):
            if _id in self.guildIDs:
                self.cache[invite] = _id
                return True
            else:
                self.whitelisted[invite] = _id
                return False

        else:
            self.cache[invite] = "NONE"
            return False

    async def comment_is_spam(self, content: str):
        """The comment contains a discord invite link to a scammy place"""
        for word in content.split():
            if DISCORD_INVITE_REGEX.search(word.lower()):
                if await self.invite_is_spam(
                    DISCORD_INVITE_REGEX.search(word).group(1)
                ):
                    return True
        return False


def read_config():
    with open("config.yaml", "r") as r:
        data = yaml.safe_load(r)
    return data


class MRClean:
    def __init__(
        self,
        username: str,
        password: str,
        servers: list[int],
        block_spammer: bool = True,
        delete_comment: bool = True,
        dislike_comment: bool = True,
    ) -> None:
        self.username = username
        self.password = password
        self.manager = BlackListManager(servers)
        self.block_spammer = block_spammer
        self.delete_comment = delete_comment
        self.dislike_comment = dislike_comment
        """Dislikes a comment if comment removal fails or we aren't the level-owner"""

        self.blocked_spammers_cache: set[int] = set()

        self.client = gd.Client()

    # Server doesn't like us performing more than 60 requests per minute (rough estimate)
    async def backoff():
        return await asyncio.sleep(random.uniform(1, 3))

    def random_reply(self) -> str:
        return random.choice(self.replies)

    async def check_comment(self, comment: gd.LevelComment):
        await self.backoff()
        if not await self.manager.comment_is_spam(comment.content):
            return

        print(
            Fore.LIGHTRED_EX
            + f"[!] Spam Detected {comment.author.name} : {comment.content}  MessageID:[{comment.id}]"
            + Fore.RESET
        )

        deletionFailed = False
        # See if we have the power to delete the comment given...
        if (
            self.delete_comment
            and comment.client.user.account_id == comment.level.creator.account_id
        ):
            print(
                Fore.LIGHTYELLOW_EX + "[...] Attempting to remove spam..." + Fore.RESET
            )
            try:
                await comment.delete()
                print(
                    Fore.LIGHTGREEN_EX
                    + "[+] Spam Was Removed Successfully!"
                    + Fore.RESET
                )
                return
            except gd.GDError as error:
                print(f"[Error] Could Not Remove Spam Due to {error}")
                deletionFailed = True
            finally:
                await self.backoff()

        if self.dislike_comment and not deletionFailed:
            print(
                Fore.LIGHTYELLOW_EX
                + "[...] Attempting to Dislike Comment..."
                + Fore.RESET
            )
            try:
                await comment.dislike()
            except gd.GDError as error:
                print(
                    Fore.LIGHTRED_EX
                    + f"[Error] Could Not Dislike the Spammer Comment to {error}"
                    + Fore.RESET
                )
            finally:
                await self.backoff()

        if self.block_spammer and not (
            comment.author.account_id in self.blocked_spammers_cache
        ):
            print(
                Fore.LIGHTYELLOW_EX
                + "[...] Attempting to Block the Spammer..."
                + Fore.RESET
            )
            try:
                await comment.author.block()
                print(
                    Fore.LIGHTGREEN_EX
                    + f"[+] Spammer {comment.author.name} Was Blocked Successfully!"
                    + Fore.RESET
                )
                self.blocked_spammers_cache.add(comment.author.account_id)
            except gd.GDError as error:
                print(
                    f"[Error] Could Not Block the Spammer {comment.author.name} Due to {error}"
                )

            finally:
                await self.backoff()

    async def purge_comment_spam_from_level(self, level: gd.Level):

        print(f'Removing Spam from {level.name} by "{level.creator.name}"')

        previous = []

        # TODO: Grab Pagesum so we can implement progress

        page = 0
        while True:
            print(Fore.LIGHTYELLOW_EX + f"[...] cleaning up page {page}" + Fore.RESET)
            comments = await level.get_comments_on_page(page=page)
            page += 1

            if not comments or (comments == previous):
                break

            previous = comments

            for comment in comments:
                await self.check_comment(comment)

        print(
            Fore.LIGHTGREEN_EX
            + f'[+] Cleaned Up Level Comments for {level.name} by "{level.creator.name}"'
            + Fore.RESET
        )

    async def cleanup_all_account_levels(self):
        """Cleans up spam comments on all levels a user logged into owns..."""
        print(Fore.LIGHTRED_EX + "[!] WARNING! Purging All Your Levels on your Account May take a long time due to Robtop's Rate-Limiting!" + Fore.RESET)
        async with self.client.login(self.username, self.password) as client:
            previous: list[gd.Level] = []
            page = 0

            while (True):
                print(Fore.LIGHTYELLOW_EX + f"[...] Purging Spam From Levels on page {page}...")
                levels = await client.user.get_levels_on_page(page)
                page += 1

                if not levels or (levels == previous):
                    break
                
                previous = levels

                for level in levels:
                    await self.purge_comment_spam_from_level(level)
                
    async def cleanup_account_levels_by_range(self, pages:int = 20):
        async with self.client.login(self.username, self.password) as client:
            previous: list[gd.Level] = []
            page = 0

            for page in range(pages):
                print(Fore.LIGHTYELLOW_EX + f"[...] Purging Spam From Levels on page {page}...")
                levels = await client.user.get_levels_on_page(page)
                page += 1

                if not levels or (levels == previous):
                    break
                
                previous = levels

                for level in levels:
                    await self.purge_comment_spam_from_level(level)
