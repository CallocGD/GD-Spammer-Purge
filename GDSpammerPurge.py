import asyncclick as click 
from aiohttp import ClientSession
from reporter import Reporter
from async_tools import AsyncPoolExecutor, wrap_async_iter
from contextlib import asynccontextmanager
import asyncio
import yaml
import random
import re 
import gd


# TODO: N Word Remover and Woke Comment Removers and custom user blacklist


# TODO: Modify Regex further to include repeated letters.
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
        self.lock = asyncio.Lock()
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

        # The locks can be taxxing or expensive so the only logical place for it is on 
        # the cache to prevent it's memory from ripping apart
        elif _id := await self.guild_id_for_invite(invite):
            if _id in self.guildIDs:
                async with self.lock:
                    self.cache[invite] = _id
                return True
            else:
                async with self.lock:
                    self.whitelisted[invite] = _id
                return False

        else:
            async with self.lock:
                self.cache[invite] = "NONE"
            return False

    async def comment_is_spam(self, content: str):
        """The comment contains a discord invite link to a scammy place"""
        
        # Allow for some asynchronous concurrency except if we have any comments that match...
        for word in content.split():
            if DISCORD_INVITE_REGEX.search(word.lower()):
                if await self.invite_is_spam(
                    DISCORD_INVITE_REGEX.search(word).group(1)
                ):
                    return True
        return False


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
        self.reporter = Reporter()
        self.client = gd.Client()

    
    async def backoff():
        return await asyncio.sleep(random.uniform(1, 2)) 

    @asynccontextmanager
    async def begin(self):
        """Begins the cleaning process by logging into your gd account and firing up a 
        threadpool over asyncio to the manager for faster concurrency"""
        await self.reporter.update_default_message("Logging into your account")
        async with self.client.login(self.username, self.password) as client, AsyncPoolExecutor(3) as pool:
            yield (client, pool)
    
    @wrap_async_iter
    async def request_for_all_levels(self, client:gd.Client):
        previous = []
        page = 0
        while (True):
            await self.reporter.update_default_message("Requesting Levels on page %i" % page)
            levels = await client.user.get_levels_on_page(page)
            ids = [l.id for l in levels]
            if previous == ids:
                break
            previous = ids
            for level in levels:
                await self.reporter.success(f"Got Level \"{level.name}\" And Preparing to scan it")
                yield level
            await self.backoff()
    
    @wrap_async_iter
    async def get_all_comments(self, level:gd.Level):
        previous = []
        page = 0
        while (True):
            await self.reporter.update_default_message("Requesting Comments on page %i" % page)
            comments = await level.get_comments_on_page(page=page)
            ids = [comment.id for comment in comments]
            if previous == ids:
                break
            previous = ids
            for comment in comments:
                yield comment
            await self.backoff()

    # NOTE: Contact me if you get ratelimited by this part... 
    async def filter_comments(self, comment:gd.LevelComment):
        # Discord shouldn't ratelimit us especially since we have a cache to prevent 
        # ourselves from sending too many requests it also making removing spam a lot faster.
        if not await self.manager.comment_is_spam(comment.content):
            return
        
        self.reporter.warning(f"[Spam Detected] {comment.author.name} : {comment.content}"\
                              f"   [MessageID]: {comment.id}  [UserID]: {comment.author.id}"\
                              f"   [AccountID]: {comment.author.account_id}")
       
        deletionFailed = False
        if self.delete_comment and (comment.client.user.account_id == comment.level.creator.account_id):
            self.reporter.pending(f"Attempting to delete spam comment made by {comment.author.name}")    
            try:
                await comment.delete()
                self.reporter.success(f"Comment From {comment.author.name} ")
            except gd.GDError as error:
                self.reporter.error(f"Could Not Remove Spam Due to {error}")
                deletionFailed = True
            finally:
                await self.backoff()

        if self.block_spammer and (comment.author.id not in self.blocked_spammers_cache):
            self.reporter.pending(f"Attempting to block \"{comment.author.name}\"")
            try:
                await comment.author.block()
                self.blocked_spammers_cache.add(comment.author.id)
                self.reporter.success(f"Spammer \"{comment.author.name}\" has been blocked")
            except gd.Error as error:
                self.reporter.error(f"Could Not Block the Spammer Due to {error}")
            finally:
                await self.backoff()

        if self.dislike_comment or (deletionFailed and not comment.is_disliked()):
            self.reporter.pending("Attempting to flag the comment given as spam")
            try:
                await comment.dislike()
            except gd.Error as error:
                self.reporter.error(f"Could Report our dissatisfaction Due to {error}")
            finally:
                await self.backoff()
        

    async def start(self):
        async with self.begin() as ctx:
            client , pool = ctx
            async for level in self.request_for_all_levels(client):
                await pool.map(self.filter_comments, self.get_all_comments(level))
        await self.reporter.success("Finished Cleaning comments")

    async def run(self):
        self.reporter.run(self.start())
    
    @staticmethod
    def read_blacklist() -> list[int]:
        try:
            with open("config.yaml", "r") as r:
                data = yaml.safe_load(r)
        except FileNotFoundError:
            with open("config.yaml", "w") as w:
                w.write("""banned-guilds:\n  # Enter discord guild ids you do not want in your comments here...\n  - "1202336147007610960"\n  # - "<guild-id>\"""")
            return MRClean.read_blacklist()       
        return data["banned-guilds"]

    @classmethod
    def from_config(
        cls, 
        username: str,
        password: str,
        servers: list[int] = [],
        block_spammer: bool = True,
        delete_comment: bool = True,
        dislike_comment: bool = True
    ):
        """Configures Spam filter from config file (config.yaml) (default)"""
        servers = list(set(servers).union(map(int, cls.read_blacklist())))
        return cls(username, password, servers, block_spammer, delete_comment, dislike_comment)


@click.command
@click.argument("username")
@click.password_option()
@click.option(
    "--block-spammer",
    "--block",
    "-b",
    default=False,
    is_flag=True,
    help="Blocks the Spammer from being able to send you dms",
)
@click.option(
    "--delete-comment",
    "--delete",
    "-d",
    default=False,
    is_flag=True,
    help="Deletes Spam Comment if discord invite is identified as being spam",
)
@click.option(
    "--dislike-comment",
    "--dislike",
    default=False,
    is_flag=True,
    help="dislikes comment if deletion fails or deletion of comment is not set to true",
)
async def main(
    username: str,
    password: str,
    block_spammer: bool,
    delete_comment: bool,
    dislike_comment: bool,
):
    """Cleans up Spam comments in Geometry dash to protect you and others from harassment and bullying"""
    await MRClean.from_config(
        username=username,
        password=password,
        block_spammer=block_spammer,
        delete_comment=delete_comment,
        dislike_comment=dislike_comment,
    ).run()

if __name__ == "__main__":
    main()


