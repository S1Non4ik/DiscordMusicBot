import disnake
from disnake.ext import commands
from disnake import FFmpegPCMAudio
from cfg.cfg import *
import asyncio
import yt_dlp
import time
from concurrent.futures import ThreadPoolExecutor
import logging
from collections import defaultdict

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('music_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('music_bot')

queues = defaultdict(list)
voice_clients = {}
now_playing = {}
active_views = {}  

yt_dl_options = {
    "format": "bestaudio/best",
    "extractaudio": True,
    "audioformat": "mp3",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0"
}

ytdl = yt_dlp.YoutubeDL(yt_dl_options)

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin',
    'options': '-vn -filter:a "volume=0.8"'
}



class MusicControls(disnake.ui.View):
    def __init__(self, bot, guild_id, timeout=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.message = None
        self.last_update = 0
        self.update_task = None

    async def start_updater(self):
        while True:
            try:
                if self.message and (time.time() - self.last_update) > 5:
                    await self.update_embed()
            except Exception as e:
                logger.error(f"Progress update error: {e}")
            await asyncio.sleep(5)

    async def update_embed(self):
        embed = self.create_embed()
        try:
            await self.message.edit(embed=embed, view=self)
            self.last_update = time.time()
        except Exception as e:
            logger.error(f"Failed to update embed: {e}")

    def create_embed(self):
        embed = disnake.Embed(
            title="🎵 Сейчас играет",
            color=0x9147ff,
            description=self._get_progress_bar() + self._get_queue_list()
        )
        embed.set_footer(text=f"⚡ Музыкальный Бот • {time.strftime('%H:%M')}")
        return embed

    def _get_progress_bar(self):
        if self.guild_id not in now_playing:
            return "🔴 Нет активного воспроизведения\n"

        track = now_playing[self.guild_id]
        duration = track.get('duration', 1)

        start_time = track.get('start_time', time.time())
        elapsed = time.time() - start_time
        progress = min(elapsed / duration, 1.0)

        filled = int(progress * 15)
        bar = "▬" * filled + "⚪" + "▬" * (14 - filled)

        current_time = self._format_time(elapsed)
        total_time = self._format_time(duration)

        return f"{bar}\n⏱️ `{current_time} / {total_time}`\n🎶 **{track['title']}**\n\n"

    def _get_queue_list(self):
        if not queues.get(self.guild_id):
            return "📭 Очередь пуста"

        queue_text = "📜 **Очередь:**\n"
        for i, track in enumerate(queues[self.guild_id][:5], 1):
            queue_text += f"{i}. {track['title']} (`{self._format_time(track['duration'])}`)\n"

        if len(queues[self.guild_id]) > 5:
            queue_text += f"🔸 И ещё {len(queues[self.guild_id]) - 5} треков"

        return queue_text

    def _format_time(self, seconds):
        return time.strftime("%M:%S", time.gmtime(seconds))

    @disnake.ui.button(emoji="⏯️", style=disnake.ButtonStyle.green, row=0)
    async def play_pause_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        voice_client = voice_clients.get(self.guild_id)
        if not voice_client:
            return await inter.response.send_message("❌ Бот не подключен", ephemeral=True)

        if voice_client.is_paused():
            voice_client.resume()
            await inter.response.send_message("▶️ Воспроизведение возобновлено", ephemeral=True)
        elif voice_client.is_playing():
            voice_client.pause()
            await inter.response.send_message("⏸️ Воспроизведение приостановлено", ephemeral=True)
        else:
            if queues.get(self.guild_id):
                await self.bot.get_cog("Music").play_next(self.guild_id)
                await inter.response.send_message("▶️ Воспроизведение начато", ephemeral=True)
            else:
                await inter.response.send_message("❌ Очередь пуста", ephemeral=True)

        await self.update_embed()

    @disnake.ui.button(emoji="⏭️", style=disnake.ButtonStyle.blurple, row=0)
    async def skip_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        voice_client = voice_clients.get(self.guild_id)
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
            await inter.response.send_message("⏭️ Трек пропущен", ephemeral=True)
        else:
            await inter.response.send_message("❌ Нет активного воспроизведения", ephemeral=True)

    @disnake.ui.button(emoji="⏹️", style=disnake.ButtonStyle.red, row=0)
    async def stop_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        cog = self.bot.get_cog("Music")
        if cog:
            await cog.cleanup_guild(self.guild_id)
            await inter.response.send_message("⏹️ Воспроизведение остановлено", ephemeral=True)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.locks = defaultdict(asyncio.Lock)
        self.cleanup_task = self.bot.loop.create_task(self.periodic_cleanup())
        self.progress_task = self.bot.loop.create_task(self._start_progress_updater())

    async def _start_progress_updater(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            for view in active_views.values():
                if isinstance(view, MusicControls) and view.message:
                    try:
                        await view.update_embed()
                    except Exception as e:
                        logger.error(f"Progress update error: {e}")
            await asyncio.sleep(5)

    async def periodic_cleanup(self):
        while True:
            await asyncio.sleep(1800)
            try:
                current_time = time.time()
                for guild_id in list(queues.keys()):
                    queues[guild_id] = [t for t in queues[guild_id] if current_time - t['added_at'] < 7200]

                    if not queues.get(guild_id) and not voice_clients.get(guild_id, {}).is_playing():
                        await self.cleanup_guild(guild_id)
            except Exception as e:
                logger.error(f"Periodic cleanup error: {e}")

    async def cleanup_guild(self, guild_id):
        async with self.locks[guild_id]:
            try:
                if guild_id in voice_clients:
                    vc = voice_clients[guild_id]
                    if vc.is_connected():
                        await vc.disconnect()
                    del voice_clients[guild_id]

                if guild_id in queues:
                    del queues[guild_id]

                if guild_id in now_playing:
                    del now_playing[guild_id]

                logger.info(f"Cleaned up guild {guild_id}")
            except Exception as e:
                logger.error(f"Error cleaning guild {guild_id}: {e}")
            finally:
                self.locks.pop(guild_id, None)

    async def get_audio_source(self, url: str):
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                self.executor,
                lambda: ytdl.extract_info(url, download=False)
            )

            if 'entries' in data:
                data = data['entries'][0]

            filename = data['url'] if 'url' in data else None
            if not filename:
                filename = data['formats'][0]['url']

            return FFmpegPCMAudio(filename, **ffmpeg_options)
        except Exception as e:
            logger.error(f"Error getting audio source: {e}")
            raise Exception(f"Ошибка при получении аудио: {str(e)}")

    async def play_next(self, guild_id: int):
        async with self.locks[guild_id]:
            if not queues[guild_id] or guild_id not in voice_clients:
                return

            voice_client = voice_clients[guild_id]
            if voice_client.is_playing() or voice_client.is_paused():
                return

            try:
                track = queues[guild_id][0]
                source = await self.get_audio_source(track['url'])

                def after_playing(error):
                    if error:
                        logger.error(f"Playback error: {error}")
                    asyncio.run_coroutine_threadsafe(
                        self.after_playback(guild_id, error),
                        self.bot.loop
                    )

                voice_client.play(source, after=after_playing)
                track['start_time'] = time.time()
                now_playing[guild_id] = track
                logger.info(f"Now playing: {track['title']} in guild {guild_id}")

            except Exception as e:
                logger.error(f"Error in play_next: {e}")
                await self.after_playback(guild_id, error=e)

    async def after_playback(self, guild_id: int, error=None):
        """Обработка завершения воспроизведения"""
        async with self.locks[guild_id]:
            if guild_id in now_playing:
                del now_playing[guild_id]

            if queues[guild_id]:
                queues[guild_id].pop(0)
                if queues[guild_id]:
                    await self.play_next(guild_id)
                else:
                    await self.cleanup_guild(guild_id)

    @commands.slash_command(guild_ids=[guild], description="Воспроизвести музыку")
    async def play(self, inter: disnake.ApplicationCommandInteraction, query: str):
        try:
            if not inter.author.voice:
                return await inter.response.send_message("Вы должны быть в голосовом канале!", ephemeral=True)

            await inter.response.defer()

            voice_client = voice_clients.get(inter.guild.id)
            if not voice_client:
                try:
                    voice_client = await inter.author.voice.channel.connect()
                    voice_clients[inter.guild.id] = voice_client
                    await inter.followup.send(f"Подключился к {inter.author.voice.channel.mention}")
                except Exception as e:
                    logger.error(f"Connection error: {e}")
                    return await inter.followup.send("Не удалось подключиться к голосовому каналу")

            try:
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(
                    self.executor,
                    lambda: ytdl.extract_info(query, download=False)
                )

                if not data:
                    raise Exception("Не удалось получить информацию о треке")

                if 'entries' in data: 
                    track = data['entries'][0]
                else:  
                    track = data

                track_info = {
                    'url': track['url'],
                    'title': track.get('title', 'Без названия'),
                    'duration': track.get('duration', 0),
                    'added_by': inter.author.display_name,
                    'added_at': time.time()
                }

                queues[inter.guild.id].append(track_info)
                await inter.followup.send(f"Добавлено в очередь: {track_info['title']}")

                if not voice_client.is_playing() and not voice_client.is_paused():
                    await self.play_next(inter.guild.id)

            except Exception as e:
                logger.error(f"Error in play command: {e}")
                await inter.followup.send(f"Ошибка: {str(e)}")

        except Exception as e:
            logger.error(f"Unexpected error in play: {e}")
            await inter.followup.send("Произошла непредвиденная ошибка")

    @commands.slash_command(guild_ids=[guild], description='Показать очередь')
    async def queue(self, inter: disnake.ApplicationCommandInteraction):
        try:
            if not queues.get(inter.guild.id):
                return await inter.response.send_message("🎶 Очередь пуста", ephemeral=True)

            view = MusicControls(self.bot, inter.guild.id)
            embed = view.create_embed()

            await inter.response.send_message(embed=embed, view=view)
            view.message = await inter.original_response()
            active_views[inter.guild.id] = view

            if not view.update_task:
                view.update_task = self.bot.loop.create_task(view.start_updater())

        except Exception as e:
            logger.error(f"Queue error: {e}")
            await inter.response.send_message(f"🚫 Ошибка: {str(e)}", ephemeral=True)

    def _format_time(self, seconds):
        if isinstance(seconds, (int, float)):
            minutes, seconds = divmod(int(seconds), 60)
            return f"{minutes:02d}:{seconds:02d}"
        return "N/A"

    def cog_unload(self):
        self.cleanup_task.cancel()
        self.executor.shutdown(wait=False)
        logger.info("Music cog unloaded")


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
    logger.info("Music cog loaded")
