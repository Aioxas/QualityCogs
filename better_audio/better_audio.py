import aiohttp
import asyncio
import discord
import os
import random
import re
import shutil
import youtube_dl
import logging
# noinspection PyUnresolvedReferences
from __main__ import send_cmd_help
from discord.ext import commands
from cogs.utils import checks
from cogs.utils.dataIO import dataIO
from cogs.utils import chat_formatting
# try:   # Check if Mutagen is installed
#    from mutagen.mp3 import MP3
#    from mutagen.flac import FLAC
#    mutagenAvailable = True
# except:
#    mutagenAvailable = False
from youtube_dl.utils import DownloadError

log = logging.getLogger("red.better_audio")


class BetterAudio:
    """Pandentia's Better Audio + Axas' improvements + Aikaterna's Notify"""

    def __init__(self, bot):
        self.bot = bot
        try:
            self.db = dataIO.load_json("data/better_audio/better_audio.json")
        except FileNotFoundError:
            self.db = {}
        self.loop = self.bot.loop.create_task(self.maintenance_loop())
        self.playing = {}  # what's playing, imported from queue
        self.skip_votes = {}  # votes to skip, per song
        self.voice_clients = {}  # voice clients
        self.players = {}  # players
        # remembering the old status messages so we don't abuse the Discord API
        self.old_status = None
        self.user_cache = {}
        self.playlist = dataIO.load_json("data/better_audio/playlist.json")

    def __unload(self):
        self.loop.cancel()

    def save_db(self):
        dataIO.save_json("data/better_audio/better_audio.json", self.db)

    @staticmethod
    def get_eligible_members(members):
        eligible = []
        for member in members:
            if not member.self_deaf:
                eligible.append(member)
        return eligible

    @staticmethod
    def get_url_info(url):
        with youtube_dl.YoutubeDL({}) as yt:
            return yt.extract_info(url, download=False, process=False)

    async def get_user(self, uid):
        if uid not in self.user_cache:
            self.user_cache[uid] = await self.bot.get_user_info(uid)
        return self.user_cache[uid]

    async def parse_playlist(self, ctx, entries, length):
        playlist = [x for x in entries]
        added = 0
        # total = len(playlist)
        urls = []
        for i in playlist:
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
                self.db[ctx.message.server.id][
                    "queue"].append(assembled_queue)
                added += 1
                # placeholder_msg = await self.bot.edit_message(placeholder_msg,
                #                                              "Successfully added {1} - {0} to the queue!\n"
                #                                              "({2}/{3})"
                #                                              .format(title, author, added, total))
                # await asyncio.sleep(1)
            except:
                await self.bot.send_message(ctx.message.channel,
                                            "Unable to add <{0}> to the queue. Skipping.".format(url))
        return added

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
                if server.id not in self.db:  # set defaults for servers
                    self.db[server.id] = {}
                # backwards-compatibility
                if "volume" not in self.db[server.id]:
                    self.db[server.id]["volume"] = 1.0
                if "vote_percentage" not in self.db[server.id]:
                    self.db[server.id]["vote_percentage"] = 0.5
                if "intentional_disconnect" not in self.db[server.id]:
                    self.db[server.id]["intentional_disconnect"] = True
                if "connected_channel" not in self.db[server.id]:
                    self.db[server.id]["connected_channel"] = None
                if "queue" not in self.db[server.id]:  # create queues
                    self.db[server.id]["queue"] = []
                # repeat mode, off by default
                if "repeat" not in self.db[server.id]:
                    self.db[server.id]["repeat"] = False
                # repeat mode, off by default
                if "stream" not in self.db[server.id]:
                    self.db[server.id]["stream"] = False
                # song announce in channel
                if "NOTIFY" not in self.db[server.id]:
                    self.db[server.id]["NOTIFY"] = False
                # song announce in channel
                if "NOTIFY_CHANNEL" not in self.db[server.id]:
                    self.db[server.id]["NOTIFY_CHANNEL"] = None
                if server.id not in self.skip_votes:  # create skip_votes list of Members
                    self.skip_votes[server.id] = []
                self.voice_clients[
                    server.id] = self.bot.voice_client_in(server)

                if not self.db[server.id]["intentional_disconnect"]:
                    if self.db[server.id]["connected_channel"] is not None:
                        channel = self.bot.get_channel(
                            self.db[server.id]["connected_channel"])
                        if self.voice_clients[server.id] is None:
                            # noinspection PyBroadException
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
                queue = self.db[sid]["queue"]
                if voice_client is not None:
                    if self.players[sid] is None:
                        # noinspection PyBroadException
                        try:
                            self.playing[sid] = {}
                            self.skip_votes[sid] = []
                            next_song = queue.pop(0)
                            if self.db[sid]["repeat"]:
                                self.db[sid]["queue"].append(next_song)
                            self.save_db()

                            url = next_song["url"]
                            self.players[sid] = await self.voice_clients[sid].create_ytdl_player(url)
                            self.players[sid].volume = self.db[sid]["volume"]
                            self.players[sid].start()
                            self.playing[sid]["title"] = next_song["title"]
                            self.playing[sid]["author"] = next_song["author"]
                            self.playing[sid]["url"] = next_song["url"]
                            self.playing[sid]["song_owner"] = await self.get_user(next_song["song_owner"])
                            self.playing[sid]["paused"] = False
                            self.bot.loop.create_task(self._notify())
                        except:  # in case something bad happens, crashing the loop is *really* undesirable
                            pass
                    else:
                        # set volume while player is playing
                        if self.players[sid].volume != self.db[sid]["volume"]:
                            self.players[sid].volume = float(
                                self.db[sid]["volume"])
                        members = self.get_eligible_members(
                            voice_client.channel.voice_members)
                        if len(members) > 0 and not self.players[sid].is_live:
                            self.players[sid].resume()
                            self.playing[sid]["paused"] = False
                        if len(members) == 0 and not self.players[sid].is_live:
                            self.players[sid].pause()
                            self.playing[sid]["paused"] = True
                        try:
                            possible_voters = len(self.get_eligible_members(
                                voice_client.channel.voice_members))
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

    async def metadata_loop(self):
        regi = re.compile(b"StreamTitle=([^`]*?);")
        await self.bot.wait_until_ready()
        track = None
        content = None
        try:
            while True:
                for sid in self.players:
                    if self.voice_clients[sid] is not None and self.players[sid] is not None \
                            and self.db[sid]["stream"] is True:
                        url = self.playing[sid]["url"]
                        async with aiohttp.get(url, headers={'Icy-MetaData': "1"}) as r:
                            urls = {k: v for k, v in dict(r.headers).items()}
                            if "Icy-Metaint" in urls:
                                chunk = int(urls["Icy-Metaint"]) + 1000
                                content = await r.content.read(chunk)
                                if b"StreamTitle" in content:
                                    try:
                                        track = regi.findall(content)[0].decode(
                                            "utf-8").replace("'", "")
                                    except:
                                        track = None
                                        content = None
                                if track:
                                    author, title = track.split(" - ")
                                    if self.playing[sid]["title"] != title \
                                            and self.playing[sid]["author"] != author:
                                        self.playing[sid]["title"] = title
                                        self.playing[sid][
                                            "author"] = author
                                        await self._notify()
                                        track = None
                                        content = None
                                        await asyncio.sleep(100)
                    await asyncio.sleep(15)
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            track = None
            content = None
            return

    async def _notify(self):
        for sid in self.bot.servers:
            notif = self.db[sid.id].get("NOTIFY", None)
            if notif is not None:
                serv = self.db[sid.id].get("connected_channel", None)
                if not serv:
                    continue
                try:
                    song = self.playing[sid.id]["title"]
                except:
                    continue

                color = int(
                    ''.join([random.choice('0123456789ABCDEF') for x in range(6)]), 16)
                channel = self.bot.get_channel(
                    self.db[sid.id]["NOTIFY_CHANNEL"])
                if channel is None:
                    channel = self.bot.get_server(sid.id).default_channel

                if song:
                    message = "Now playing: {}.".format(song)
                    em = discord.Embed(description=str(
                        message), color=discord.Color(value=color))
                    await self.bot.send_message(channel, embed=em)
                else:
                    await self.bot.send_message(channel, "This is an error after the embed")
            elif "NOTIFY" not in self.db[sid.id]:
                self.db[sid.id]["NOTIFY"] = False
                self.save_db()

    @commands.command(pass_context=True, name="playing", aliases=["np", "song"], no_pm=True)
    # aliased so people who aren't used to it can still use it's commands
    async def playing_cmd(self, ctx):
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
                self.db[ctx.message.server.id][
                    "intentional_disconnect"] = False
                self.db[ctx.message.server.id][
                    "connected_channel"] = ctx.message.author.voice_channel.id
                self.save_db()
                await self.bot.say("Summoned to {0} successfully!".format(str(ctx.message.author.voice_channel)))
            else:
                await self.bot.say("I'm already in your channel!")
        else:
            await self.bot.say("You need to join a voice channel first.")

    @commands.command(pass_context=True, name="play", no_pm=True)
    async def play_cmd(self, ctx, url: str, playlist_length: int=None):
        """Plays a SoundCloud or Twitch link."""
        self.playing_text_channel = ctx.message.channel
        if playlist_length is None:
            playlist_length = 10000
        # just to cache it preemptively
        await self.bot.get_user_info(ctx.message.author.id)
        if self.voice_clients[ctx.message.server.id] is None:
            await self.bot.say("You need to summon me first.")
            return
        if ctx.message.author.voice_channel is None:
            await self.bot.say("You need to be in a voice channel.")
            return
        if url.startswith("icy://"):
            url = url[len("icy://"):]
        try:
            await self.bot.send_typing(ctx.message.channel)
            if url.endswith(".pls") or url.endswith(".m3u"):
                async with aiohttp.get(url) as r:
                    urls = str(await r.text())
                    if "icy://" in urls:
                        urls = urls.replace("icy://", "http://")
                    url = re.findall(
                        r"(http(s)?:\/\/[a-zA-Z0-9\:\.\-\_\/\?\=\%]*)", urls)[0][0]
            # probably the best URL matching that's out there
            info = self.get_url_info(url)
            try:  # for bit.ly URLs, etc...
                if info["extractor"] == "generic":
                    info = self.get_url_info(info["url"])
            except KeyError:
                pass
        except DownloadError:
            await self.bot.say("That URL is unsupported right now. Meow")
            return
        if info["extractor"] in ["youtube", "soundcloud"]:
            title = info["title"]
            author = info["uploader"]
            assembled_queue = {
                "url": url, "song_owner": ctx.message.author.id, "title": title, "author": author}
            self.db[ctx.message.server.id]["queue"].append(assembled_queue)
            self.save_db()
            await self.bot.say("Successfully added {1} - {0} to the queue!".format(title, author))
        elif info["extractor"] in ["twitch:stream"]:
            title = info["description"]
            author = info["uploader"]
            assembled_queue = {
                "url": url, "song_owner": ctx.message.author.id, "title": title, "author": author}
            self.db[ctx.message.server.id]["queue"].append(assembled_queue)
            self.save_db()
            await self.bot.say("Successfully added {1} - {0} to the queue!".format(title, author))
        elif info["extractor"] in ["soundcloud:set", "youtube:playlist"]:
            await self.bot.say("Adding a playlist, this may take a while...")
            # placeholder_msg = await self.bot.say("​")
            added = await self.parse_playlist(ctx, info["entries"], playlist_length)
            self.save_db()
            await self.bot.say("Added {0} tracks to the queue.".format(added))
            # asyncio.sleep(5)
            # await self.bot.say("Do you want to save this playlist for future use?")
            # self.save()
        elif info["formats"][0]["format_id"] in ["mpeg", "rtmp", "flac"]:
            author = None
            title = info["webpage_url"]

            assembled_queue = {
                "url": url, "song_owner": ctx.message.author.id, "title": title, "author": author}
            self.db[ctx.message.server.id]["queue"].append(assembled_queue)
            self.save_db()
            await self.bot.say("Successfully added {1} - {0} to the queue!".format(title, author))
        elif info["extractor"] == 'generic':
            try:
                title = info["title"]
            except:
                title = None
            try:
                author = info["uploader"]
            except:
                author = None
            assembled_queue = {
                "url": url, "song_owner": ctx.message.author.id, "title": title, "author": author}
            self.db[ctx.message.server.id]["queue"].append(assembled_queue)
            self.save_db()
            await self.bot.say("Successfully added {1} - {0} to the queue!".format(title, author))
        else:
            await self.bot.say("That URL is unsupported right now.")

    @commands.command(pass_context=True, name="queue", no_pm=True)
    async def queue_cmd(self, ctx):
        """Shows the queue for the current server."""
        queue = self.db[ctx.message.server.id]["queue"]
        if queue:
            number = 1
            human_queue = ""
            for i in queue:
                song_owner = await self.get_user(i["song_owner"])
                human_queue += "**{0}".format(number) + ".** **{author}** - " \
                                                        "**{title}** added by {0}\n".format(
                                                            song_owner, **i)
                number += 1
            # pagify the output, so we don't hit the 2000 character
            paged = chat_formatting.pagify(human_queue, "\n")
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
        self.db[ctx.message.server.id]["queue"] = []
        self.db[ctx.message.server.id]["stream"] = False
        self.save_db()
        if self.players[ctx.message.server.id] is not None:
            self.players[ctx.message.server.id].stop()
        await self.bot.say("Playback stopped.")

    @checks.mod_or_permissions(move_members=True)
    @commands.command(pass_context=True, no_pm=True)
    async def shuffle(self, ctx):
        """Shuffles the queue."""
        queue = self.db[ctx.message.server.id]["queue"]
        random.shuffle(queue)
        self.db[ctx.message.server.id]["queue"] = queue
        self.save_db()
        await self.bot.say("Queue shuffled.")

    @checks.mod_or_permissions(move_members=True)
    @commands.command(pass_context=True, no_pm=True)
    async def forceskip(self, ctx):
        """Skips the current song."""
        if self.players[ctx.message.server.id] is not None:
            self.players[ctx.message.server.id].stop()
        if self.db[ctx.message.server.id]["stream"]:
            self.db[ctx.message.server.id]["stream"] = False
            self.save_db()
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
            if self.db[ctx.message.server.id]["stream"]:
                self.db[ctx.message.server.id]["stream"] = False
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
    async def repeat(self, ctx):
        """Toggles repeat mode for the current server."""
        if self.db[ctx.message.server.id]["repeat"]:
            self.db[ctx.message.server.id]["repeat"] = False
            self.save_db()
            await self.bot.say("Repeat mode disabled.")
        elif not self.db[ctx.message.server.id]["repeat"]:
            self.db[ctx.message.server.id]["repeat"] = True
            self.save_db()
            await self.bot.say("Repeat mode enabled.")

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

    @checks.is_owner()
    @audioset_cmd.command(pass_context=True)
    async def notify(self, ctx):
        """Send a message to the channel when the song changes."""
        if self.db[ctx.message.server.id]["NOTIFY_CHANNEL"] is None:
            await self.bot.say("Remember to set a notification channel before using, thanks.")
        self.db[ctx.message.server.id]["NOTIFY"] = not self.db[
            ctx.message.server.id]["NOTIFY"]
        if self.db[ctx.message.server.id]["NOTIFY"]:
            await self.bot.say("Now notifying when a new track plays.")
        else:
            await self.bot.say("No longer notifying when a new track plays.")
        self.save_db()

    @checks.is_owner()
    @audioset_cmd.command(pass_context=True)
    async def notify_channel(self, ctx, channel: discord.Channel=None):
        """Send a message to the channel when the song changes."""
        if channel is None:
            self.db[ctx.message.server.id][
                "NOTIFY_CHANNEL"] = ctx.message.channel.id
        else:
            self.db[ctx.message.server.id][
                "NOTIFY_CHANNEL"] = self.bot.get_channel(channel).id
        channel = self.db[ctx.message.server.id]["NOTIFY_CHANNEL"]
        await self.bot.say("Notify channel successfully set as {}.".format(self.bot.get_channel(channel).mention))
        if not self.db[ctx.message.server.id]["NOTIFY"]:
            await self.bot.say("Make sure to turn on notifications.")
        self.save_db()

    @checks.is_owner()
    @commands.group(name="playlist", pass_context=True, invoke_without_command=True)
    async def playlisted(self, ctx):
        """General playlist commands"""
        await send_cmd_help(ctx)

    @playlisted.command()
    async def append(self, name: str, link: str):
        """Allows to append a single video to an existing playlist."""
        if name not in self.playlist:
            await self.bot.say("A playlist with this name doesn't exist or"
                               " it was written incorrectly, please try again.")
            return
        info = self.get_url_info(link)

        if info["extractor"] in ["youtube", "soundcloud"]:
            await self.bot.say("Appending {} to {}, please wait.".format(info["title"], name))
            self.playlist[name].append(info["url"])
            dataIO.save_json("data/better_audio/playlist.json", self.playlist)
            await self.bot.say("{} has been appended to {}. Total # of tracks in {}: {}"
                               .format(info["title"], name, name, len(self.playlist[name])))
        else:
            await self.bot.say("Streams and playlists can't be appended to playlists."
                               "To append playlists, use [p]playlist extend name_of_playlist playlist_link")

    @playlisted.command(pass_context=True)
    async def delete(self, ctx, name: str):
        """Allows to delete an existing playlist."""
        if name not in self.playlist:
            await self.bot.say("A playlist with this name doesn't exist or"
                               " it was written incorrectly, please try again.")
            return
        await self.bot.say("Are you sure that you want to delete this playlist? Type Yes/No")
        answer = await self.bot.wait_for_message(timeout=15,
                                                 author=ctx.message.author)
        if answer is None:
            return
        elif answer.content.lower().strip() == "yes":
            self.playlist = {key: value for key,
                             value in self.playlist.items() if key != name}
            dataIO.save_json("data/better_audio/playlist.json", self.playlist)
        else:
            return

    @playlisted.command()
    async def extend(self, name: str, playlist: str, playlist_length: int=None):
        """Allows to extend an existing playlist with another playlist's tracks"""
        if not self.playlist[name]:
            await self.bot.say("A playlist with this name doesn't exist or"
                               " it was written incorrectly, please try again.")
            return
        info = self.get_url_info(playlist)
        if info["extractor"] in ["soundcloud:set", "youtube:playlist"]:
            await self.bot.say("Extending {}, this may take a while...".format(name))
            playlist = [x for x in info["entries"]]
            if playlist_length is None:
                playlist_length = len(playlist)
            for i in playlist:
                if playlist_length != 0:
                    self.playlist[name].append(i["url"])
                    playlist_length -= 1
            dataIO.save_json("data/better_audio/playlist.json", self.playlist)
            await self.bot.say("Playlist successfully extended, the name is {}"
                               " and it now has {} tracks".format(name, len(self.playlist[name])))
        else:
            await self.bot.say("This is not a Souncloud Set or Youtube Playlist,"
                               " please use the correct kind of link. Thanks.")

    @playlisted.command()
    async def list(self):
        """Shows a list of existing playlist, mix, or sets that are saved"""
        await self.bot.say(chat_formatting.box((", ").join(list(keys for keys, values in self.playlist.items()))))

    @playlisted.command(pass_context=True)
    async def play(self, ctx, name: str, playlist_length: int=None):
        """Allows you to play an existing playlist"""
        self.playing_text_channel = ctx.message.channel
        if playlist_length is None:
            playlist_length = len(self.playlist[name])
        # just to cache it preemptively
        await self.bot.get_user_info(ctx.message.author.id)
        if self.voice_clients[ctx.message.server.id] is None:
            await self.bot.say("You need to summon me first.")
            return
        if ctx.message.author.voice_channel is None:
            await self.bot.say("You need to be in a voice channel.")
            return
        if name in self.playlist:
            placeholder_msg = await self.bot.say("​")
            added = 0
            total = playlist_length
            for url in self.playlist[name]:
                    # noinspection PyBroadException
                try:
                    info = self.get_url_info(url)
                    title = info["title"]
                    author = info["uploader"]
                    assembled_queue = {"url": url, "song_owner": ctx.message.author.id,
                                       "title": title, "author": author}
                    self.db[ctx.message.server.id][
                        "queue"].append(assembled_queue)
                    added += 1
                    placeholder_msg = await self.bot.edit_message(placeholder_msg,
                                                                  "Successfully added {1} - {0} to the queue!\n"
                                                                  "({2}/{3})"
                                                                  .format(title, author, added, total))
                    await asyncio.sleep(1)
                except:
                    await self.bot.say("Unable to add <{0}> to the queue. Skipping.".format(url))
            self.save_db()
            await self.bot.say("Added {0} tracks to the queue.".format(added))
        else:
            await self.bot.say("The playlist doesn't exist or"
                               " it was written incorrectly, please try again.")

    @playlisted.command(pass_context=True)
    async def save(self, ctx, name: str, playlist: str, playlist_length: int=None):
        """Allows you to save a Playlist, Mix, or Set for future use."""
        if name in self.playlist:
            await self.bot.say("A playlist with this name already exists, proceeding"
                               " will overwrite the playlist, are you sure? Yes/No")
            answer = await self.bot.wait_for_message(timeout=15,
                                                     author=ctx.message.author)
            if answer is None:
                await self.bot.say("If you want to add more songs to an existing playlist,"
                                   " please use the append function for single songs or extend"
                                   " function for merging an existing playlist with a playlist link")
                return
            elif answer.content.lower().strip() == "yes":
                pass
            else:
                await self.bot.say("If you want to add more songs to an existing playlist,"
                                   " please use the append function for single songs or extend"
                                   " function for merging an existing playlist with a playlist link")
                return
        info = self.get_url_info(playlist)
        if info["extractor"] in ["soundcloud:set", "youtube:playlist"]:
            await self.bot.say("Saving a playlist, this may take a while...")
            playlist = [x for x in info["entries"]]
            if playlist_length is None:
                playlist_length = len(playlist)
            self.playlist[name] = []
            for i in playlist:
                if playlist_length != 0:
                    self.playlist[name].append(i["url"])
                    playlist_length -= 1
            dataIO.save_json("data/better_audio/playlist.json", self.playlist)
            await self.bot.say("Playlist successfully saved, the name is {}"
                               " and it has {} tracks".format(name, len(self.playlist[name])))
        else:
            await self.bot.say("This is not a Souncloud Set or Youtube Playlist,"
                               " please use the correct kind of link. Thanks.")


def check_folders():
    f = "data/better_audio"
    if not os.path.exists(f):
        print("creating data/better_audio directory")
        os.mkdir(f)


def check_files():
    f = 'data/better_audio/better_audio.json'
    g = 'data/better_audio.json'
    h = 'data/better_audio/playlist.json'
    default = {}
    if not os.path.isfile(f):
        print('Creating default better_audio better_audio.json...')
        dataIO.save_json(f, default)
    elif os.path.isfile(g):
        shutil.copy2(g, 'data/better_audio')
        os.remove(g)
    if not os.path.isfile(h):
        print('Creating default better_audio playlist.json...')
        dataIO.save_json(h, default)


def setup(bot):
    #   if mutagenAvailable:
    check_folders()
    check_files()
    bot.add_cog(BetterAudio(bot))
#    else:
#        raise RuntimeError("You need to run 'pip3 install mutagen'")
