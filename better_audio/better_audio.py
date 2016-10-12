import asyncio
import discord
import youtube_dl
import re
import random
# noinspection PyUnresolvedReferences
from __main__ import send_cmd_help
from discord.ext import commands
from cogs.utils import checks
from cogs.utils.dataIO import dataIO
from cogs.utils import chat_formatting


class BetterAudio:
    """Pandentia's Better Audio"""

    def __init__(self, bot):
        self.bot = bot
        try:
            self.db = dataIO.load_json("./data/better_audio.json")
        except FileNotFoundError:
            self.db = {}
        self.loop = self.bot.loop.create_task(self.maintenance_loop())
        self.playing = {}  # what's playing, imported from queue
        self.queues = {}  # what's in the queue
        self.skip_votes = {}  # votes to skip, per song
        self.voice_clients = {}  # voice clients
        self.players = {}  # players
        self.old_status = None  # remembering the old status messages so we don't abuse the Discord API
        self.user_cache = {}

    def __unload(self):
        self.loop.cancel()

    def save_db(self):
        dataIO.save_json("./data/better_audio.json", self.db)

    def get_eligible_members(self, members):
        eligible = []
        for member in members:
            if not member.bot and not member.self_deaf:
                eligible.append(member)
        return eligible

    def get_url_info(self, url):
        with youtube_dl.YoutubeDL({}) as yt:
            return yt.extract_info(url, download=False, process=False)

    async def get_user(self, uid):
        if uid not in self.user_cache:
            self.user_cache[uid] = await self.bot.get_user_info(uid)
        return self.user_cache[uid]

    async def set_status(self, status):
        if status is not self.old_status:
            self.old_status = status
            await self.bot.change_presence(game=discord.Game(name=status))

    async def maintenance_loop(self):
        while True:
            old_db = self.db
            for server in self.bot.servers:
                if server.id not in self.players:  # set nonexistent voice clients and players to None
                    self.players[server.id] = None
                if server.id not in self.queues:  # create queues
                    self.queues[server.id] = []
                if server.id not in self.db:  # set defaults for servers
                    self.db[server.id] = {}
                if "volume" not in self.db[server.id]:  # backwards-compatibility
                    self.db[server.id]["volume"] = 1.0
                if "vote_percentage" not in self.db[server.id]:
                    self.db[server.id]["vote_percentage"] = 0.5
                if "intentional_disconnect" not in self.db[server.id]:
                    self.db[server.id]["intentional_disconnect"] = True
                if "connected_channel" not in self.db[server.id]:
                    self.db[server.id]["connected_channel"] = None
                if server.id not in self.skip_votes:  # create skip_votes list of Members
                    self.skip_votes[server.id] = []
                self.voice_clients[server.id] = self.bot.voice_client_in(server)

                if not self.db[server.id]["intentional_disconnect"]:
                    if self.db[server.id]["connected_channel"] is not None:
                        channel = self.bot.get_channel(self.db[server.id]["connected_channel"])
                        if self.voice_clients[server.id] is None:
                            try:
                                await self.bot.join_voice_channel(channel)
                            except:  # too broad, I know, but we can't risk crashing the loop because of this
                                pass

            for sid in self.players:  # clean up dead players
                player = self.players[sid]
                if player is not None:
                    if player.is_done():
                        self.players[sid] = None
            for sid in self.voice_clients:  # clean up dead voice clients
                voice_client = self.voice_clients[sid]
                if voice_client is not None:
                    if not voice_client.is_connected():
                        self.voice_clients[sid] = None
            for sid in dict(self.playing):  # clean up empty playing messages
                playing = self.playing[sid]
                if playing == {}:
                    self.playing.pop(sid)

            if "global" not in self.db:
                self.db["global"] = {"playing_status": False}

            # Queue processing:
            for sid in self.voice_clients:
                voice_client = self.voice_clients[sid]
                player = self.players[sid]
                queue = self.queues[sid]
                if voice_client is not None:
                    if player is None:
                        # noinspection PyBroadException
                        try:
                            self.playing[sid] = {}
                            self.skip_votes[sid] = []
                            next_song = queue.pop(0)
                            url = next_song["url"]
                            self.players[sid] = await self.voice_clients[sid].create_ytdl_player(url)
                            self.players[sid].volume = self.db[sid]["volume"]
                            self.players[sid].start()
                            self.playing[sid]["title"] = next_song["title"]
                            self.playing[sid]["author"] = next_song["author"]
                            self.playing[sid]["url"] = next_song["url"]
                            self.playing[sid]["song_owner"] = await self.get_user(next_song["song_owner"])
                            self.playing[sid]["paused"] = False
                        except:  # in case something bad happens, crashing the loop is *really* undesirable
                            pass
                    else:
                        if player.volume != self.db[sid]["volume"]:  # set volume while player is playing
                            self.players[sid].volume = float(self.db[sid]["volume"])

                        members = self.get_eligible_members(voice_client.channel.voice_members)
                        if len(members) > 0 and not self.players[sid].is_live:
                            self.players[sid].resume()
                            self.playing[sid]["paused"] = False
                        if len(members) == 0 and not self.players[sid].is_live:
                            self.players[sid].pause()
                            self.playing[sid]["paused"] = True
                        try:
                            possible_voters = len(self.get_eligible_members(voice_client.channel.voice_members))
                            votes = 0
                            for member in voice_client.channel.voice_members:
                                if member in self.skip_votes[sid]:
                                    votes += 1

                            if (votes / possible_voters) > float(self.db[sid]["vote_percentage"]):
                                self.players[sid].stop()
                        except ZeroDivisionError:
                            pass

            if self.db["global"]["playing_status"]:
                playing_servers = 0
                for server in self.playing:
                    if self.playing[server] != {}:
                        if not self.playing[server]["paused"]:
                            playing_servers += 1
                if playing_servers == 0:
                    await self.set_status(None)
                elif playing_servers == 1:
                    # noinspection PyBroadException
                    try:
                        for i in self.playing:
                            if self.playing[i] != {}:
                                playing = self.playing[i]
                        # noinspection PyUnboundLocalVariable
                        status = "{title} - {author}".format(**playing)
                        await self.set_status(status)
                    except:
                        pass
                else:
                    status = "music on {0} servers".format(playing_servers)
                    await self.set_status(status)
            else:
                await self.set_status(None)

            if self.db != old_db:
                self.save_db()

            await asyncio.sleep(1)

    @commands.command(pass_context=True, name="playing", aliases=["np", "song"], no_pm=True)
    async def playing_cmd(self, ctx):  # aliased so people who aren't used to it can still use it's commands
        """Shows the currently playing song."""
        if ctx.message.server.id in self.playing:
            playing = self.playing[ctx.message.server.id]
            if self.playing[ctx.message.server.id] == {}:
                playing = None
        else:
            playing = None

        if playing is not None:
            await self.bot.say("I'm currently playing **{title}** by **{author}**.\n"
                               "Link: <{url}>\n"
                               "Added by {song_owner}".format(**playing))
        else:
            await self.bot.say("Nothing currently playing.")

    @commands.command(pass_context=True, name="summon", no_pm=True)
    async def summon_cmd(self, ctx):
        """Summons the bot to your voice channel."""
        if ctx.message.author.voice_channel is not None:
            if self.voice_clients[ctx.message.server.id] is None:
                await self.bot.join_voice_channel(ctx.message.author.voice_channel)
                self.db[ctx.message.server.id]["intentional_disconnect"] = False
                self.db[ctx.message.server.id]["connected_channel"] = ctx.message.author.voice_channel.id
                self.save_db()
                await self.bot.say("Summoned to {0} successfully!".format(str(ctx.message.author.voice_channel)))
            else:
                await self.bot.say("I'm already in your channel!")
        else:
            await self.bot.say("You need to join a voice channel first.")

    @commands.command(pass_context=True, name="play", no_pm=True)
    async def play_cmd(self, ctx, url: str, playlist_length: int=999):
        """Plays a SoundCloud or Twitch link."""
        await self.bot.get_user_info(ctx.message.author.id)  # just to cache it preemptively
        if self.voice_clients[ctx.message.server.id] is None:
            await self.bot.say("You need to summon me first.")
            return
        if ctx.message.author.voice_channel is None:
            await self.bot.say("You need to be in a voice channel.")
            return
        if re.match(r"^http(s)?://soundcloud\.com/[0-9a-zA-Z\-_]*/[0-9a-zA-Z\-_]*", url) or \
                re.match(r"^http(s)?://(www\.)?twitch\.tv/[0-9a-zA-Z\-_]*$", url) or \
                re.match(r"^http(s)?://(www\.)?(m\.)?youtube\.com/watch\?v=.{11}$", url):  # match supported links
            info = self.get_url_info(url)
            if "entries" in info:
                await self.bot.say("Adding a playlist, this may take a while...")
                placeholder_msg = await self.bot.say("​")
                added = 0
                total = len(info["entries"])
                length = playlist_length
                urls = []
                for i in info["entries"]:
                    if length != 0:
                        urls.append(i["url"])
                        length -= 1

                for url in urls:
                    # noinspection PyBroadException
                    try:
                        info = self.get_url_info(url)
                        title = info["title"]
                        author = info["uploader"]
                        assembled_queue = {"url": url, "song_owner": ctx.message.author.id,
                                           "title": title, "author": author}
                        self.queues[ctx.message.server.id].append(assembled_queue)
                        added += 1
                        placeholder_msg = await self.bot.edit_message(placeholder_msg,
                                                                      "Successfully added {1} - {0} to the queue!\n"
                                                                      "({2}/{3})"
                                                                      .format(title, author, added, total))
                        await asyncio.sleep(1)
                    except:
                        await self.bot.say("Unable to add <{0}> to the queue. Skipping.".format(url))
                await self.bot.say("Added {0} tracks to the queue.".format(added))
            else:
                title = info["title"]
                author = info["uploader"]
                assembled_queue = {"url": url, "song_owner": ctx.message.author.id, "title": title, "author": author}
                self.queues[ctx.message.server.id].append(assembled_queue)
                await self.bot.say("Successfully added {1} - {0} to the queue!".format(title, author))
        else:
            await self.bot.say("That URL is unsupported right now.")

    @commands.command(pass_context=True, name="queue", no_pm=True)
    async def queue_cmd(self, ctx):
        """Shows the queue for the current server."""
        queue = self.queues[ctx.message.server.id]
        if queue:
            number = 1
            human_queue = ""
            for i in queue:
                song_owner = await self.get_user(i["song_owner"])
                human_queue += "**{0}".format(number) + ".** **{author}** - " \
                                                        "**{title}** added by {0}\n".format(song_owner, **i)
                number += 1
            paged = chat_formatting.pagify(human_queue, "\n")  # pagify the output, so we don't hit the 2000 character
            #                                                    limit
            for page in paged:
                await self.bot.say(page)
        else:
            await self.bot.say("The queue is empty! Queue something with the play command.")

    @commands.command(pass_context=True, name="skip", no_pm=True)
    async def skip_cmd(self, ctx):
        """Registers your vote to skip."""
        if ctx.message.author not in self.skip_votes[ctx.message.server.id]:
            self.skip_votes[ctx.message.server.id].append(ctx.message.author)
            await self.bot.say("Vote to skip registered.")
        else:
            self.skip_votes[ctx.message.server.id].remove(ctx.message.author)
            await self.bot.say("Vote to skip unregistered.")

    @checks.mod_or_permissions(move_members=True)
    @commands.command(pass_context=True, no_pm=True)
    async def stop(self, ctx):
        """Be warned, this clears the queue and stops playback."""
        self.playing[ctx.message.server.id] = {}
        self.queues[ctx.message.server.id] = []
        if self.players[ctx.message.server.id] is not None:
            self.players[ctx.message.server.id].stop()
        await self.bot.say("Playback stopped.")

    @checks.mod_or_permissions(move_members=True)
    @commands.command(pass_context=True, no_pm=True)
    async def shuffle(self, ctx):
        """Shuffles the queue."""
        queue = self.queues[ctx.message.server.id]
        random.shuffle(queue)
        self.queues[ctx.message.server.id] = queue
        await self.bot.say("Queue shuffled.")

    @checks.mod_or_permissions(move_members=True)
    @commands.command(pass_context=True, no_pm=True)
    async def forceskip(self, ctx):
        """Skips the current song."""
        if self.players[ctx.message.server.id] is not None:
            self.players[ctx.message.server.id].stop()
            await self.bot.say("Song skipped. Blame {0}.".format(ctx.message.author.mention))

    @checks.mod_or_permissions(move_members=True)
    @commands.command(pass_context=True, no_pm=True)
    async def disconnect(self, ctx):
        """Disconnects the bot from the server."""
        self.playing[ctx.message.server.id] = {}
        if self.players[ctx.message.server.id] is not None:
            self.players[ctx.message.server.id].stop()
        if self.voice_clients[ctx.message.server.id] is not None:
            self.db[ctx.message.server.id]["intentional_disconnect"] = True
            self.db[ctx.message.server.id]["connected_channel"] = None
            self.save_db()
            await self.voice_clients[ctx.message.server.id].disconnect()
            await self.bot.say("Disconnected.")

    @commands.command()
    async def audio_source(self):
        """Where the source code for this audio cog can be found."""
        await self.bot.say("https://github.com/Pandentia/Red-Cogs/")

    @commands.group(name="audioset", pass_context=True, invoke_without_command=True)
    async def audioset_cmd(self, ctx):
        """Sets configuration settings."""
        await send_cmd_help(ctx)

    @checks.mod_or_permissions(move_members=True)
    @audioset_cmd.command(pass_context=True, no_pm=True)
    async def volume(self, ctx, volume: int):
        """Sets the audio volume for this server."""
        if 0 <= volume <= 200:
            volume /= 100
            self.db[ctx.message.server.id]["volume"] = volume
            self.save_db()
            await self.bot.say("Volume for this server set to {0}%.".format(str(int(volume * 100))))
        else:
            await self.bot.say("Try a volume between 0 and 200%.")

    @checks.mod_or_permissions(move_members=True)
    @audioset_cmd.command(pass_context=True, no_pm=True)
    async def vote_ratio(self, ctx, percentage: int):
        """Sets the vote ratio required to skip a song."""
        percentage /= 100
        if 0 < percentage < 1:
            self.db[ctx.message.server.id]["vote_percentage"] = percentage
            self.save_db()
            await self.bot.say("Skip threshold set to {0}%.".format(int(percentage * 100)))
        else:
            await self.bot.say("Try a threshold between 0 and 100.")

    @checks.mod_or_permissions(move_members=True)
    @audioset_cmd.command(pass_context=True, no_pm=True)
    async def lock(self):
        """Locks the bot to your voice channel and summons it there permanently."""

    @checks.is_owner()
    @audioset_cmd.command()
    async def status(self):
        """Toggles the playing status messages."""
        if self.db["global"]["playing_status"]:
            self.db["global"]["playing_status"] = False
            self.save_db()
            await self.bot.say("Playing messages disabled.")
        elif not self.db["global"]["playing_status"]:
            self.db["global"]["playing_status"] = True
            self.save_db()
            await self.bot.say("Playing messages enabled.")


def setup(bot):
    bot.add_cog(BetterAudio(bot))
