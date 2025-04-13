import disnake
from disnake.ext import commands
from disnake import FFmpegPCMAudio
from cfg.cfg import *
import asyncio
import yt_dlp
import time

queues = {}
voice_clients = {}

yt_dl_options = {
    "format": "bestaudio/best",
    "extract_flat": True,  
    "no_cache": True,
    "socket_timeout": 15,
    "ignoreerrors": True,
    "quiet": True,
}

ytdl = yt_dlp.YoutubeDL(yt_dl_options)

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin',
    'options': '-vn -filter:a "volume=0.25"'
}


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.loop.create_task(self.clean_old_entries())
        print('Музыкальный модуль активирован')

    async def clean_old_entries(self):
        while True:
            await asyncio.sleep(3600)  
            try:
                current_time = time.time()
                for guild_id in list(queues.keys()):
                    queues[guild_id] = [
                        t for t in queues[guild_id]
                        if current_time - t['added_at'] < 1800  
                    ]
                    if not queues[guild_id]:  
                        del queues[guild_id]

                for guild_id in list(voice_clients.keys()):
                    if guild_id not in queues:
                        client = voice_clients[guild_id]
                        if client.is_connected():
                            await client.disconnect()
                        del voice_clients[guild_id]

            except Exception as e:
                print(f"Ошибка в clean_old_entries: {e}")



    async def fetch_info(self, url: str) -> dict:
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(
                None,
                self._sync_extract_info,
                url
            )

            if not data:
                raise Exception("Не удалось получить информацию")

            return self._process_data(data, url)

        except Exception as e:
            print(f"Ошибка: {e}")
            raise

    def _sync_extract_info(self, url: str) -> dict:
        return ytdl.extract_info(url, download=False)

    def _process_data(self, data: dict, original_url: str) -> dict:
        if 'entries' in data:
            return {
                'type': 'playlist',
                'url': original_url,
                'title': data.get('title', 'Плейлист'),
                'entries': [
                    {
                        'url': e['url'],
                        'title': e.get('title', 'Трек'),
                        'original_url': original_url,
                        'added_at': time.time()
                    }
                    for e in data['entries'] if e
                ]
            }

        return {
            'type': 'track',
            'url': data.get('url', original_url),
            'title': data.get('title', 'Трек'),
            'original_url': original_url,
            'added_at': time.time()
        }

    @commands.slash_command(guild_ids=[guild], description='Воспроизвести трек или плейлист')
    async def play(self, inter: disnake.ApplicationCommandInteraction, query: str):
        try:
            if not inter.author.voice:
                return await inter.response.send_message("🔇 Вы не в голосовом канале!", ephemeral=True)

            await inter.response.defer()

            if inter.guild.id not in voice_clients:
                voice_client = await inter.author.voice.channel.connect()
                voice_clients[inter.guild.id] = voice_client
                await inter.followup.send(f"🔊 Подключился к {inter.author.voice.channel.mention}")

            if inter.guild.id not in queues:
                queues[inter.guild.id] = []

            data = await self.fetch_info(query)

            if data['type'] == 'playlist':
                queues[inter.guild.id].extend(data['entries'])
                await inter.followup.send(
                    f"🎶 Добавлен плейлист {data['title']} ({len(data['entries'])} треков)"
                )
            else:
                queues[inter.guild.id].append(data)
                await inter.followup.send(f"🎶 Добавлен трек: {data['title']}")

            if len(queues[inter.guild.id]) == 1 or not voice_clients[inter.guild.id].is_playing():
                await self.play_next(inter.guild.id)

        except Exception as e:
            await inter.followup.send(f"🚫 Ошибка: {str(e)}")
            print(f"Play error: {e}")

    async def play_next(self, guild_id, retries=3):
        if not queues.get(guild_id) or not voice_clients.get(guild_id):
            return

        track = queues[guild_id][0]

        try:
            # Используем уже распарсенные данные
            player = FFmpegPCMAudio(track['url'], **ffmpeg_options)
            voice_clients[guild_id].play(
                player,
                after=lambda e: self.bot.loop.create_task(self.song_finished(guild_id))
            )

        except Exception as e:
            print(f"Ошибка воспроизведения: {e}")
            if retries > 0:
                await asyncio.sleep(2)
                await self.play_next(guild_id, retries - 1)
            else:
                await self.force_skip(guild_id)

    async def song_finished(self, guild_id):
        try:
            if queues.get(guild_id):
                queues[guild_id].pop(0)
                if queues[guild_id]:
                    await self.play_next(guild_id)
                else:
                    await self.stop_voice(guild_id)
        except Exception as e:
            print(f"Ошибка завершения трека: {e}")

    async def force_skip(self, guild_id):
        try:
            if queues.get(guild_id):
                queues[guild_id].pop(0)
                if queues[guild_id]:
                    await self.play_next(guild_id)
                else:
                    await self.stop_voice(guild_id)
        except Exception as e:
            print(f"Ошибка принудительного пропуска: {e}")

    async def stop_voice(self, guild_id):
        try:
            if guild_id in voice_clients:
                client = voice_clients[guild_id]
                if client.is_connected():
                    await client.disconnect()
                del voice_clients[guild_id]
            if guild_id in queues:
                del queues[guild_id]
        except Exception as e:
            print(f"Ошибка отключения: {e}")

    @commands.slash_command(guild_ids=[guild], description='Пропустить текущий трек')
    async def skip(self, inter: disnake.ApplicationCommandInteraction):
        try:
            if inter.guild.id in voice_clients:
                voice_clients[inter.guild.id].stop()
                await inter.response.send_message("⏭️ Трек пропущен")
            else:
                await inter.response.send_message("❌ Бот не подключен", ephemeral=True)
        except Exception as e:
            await inter.response.send_message(f"🚫 Ошибка: {str(e)}")

    @commands.slash_command(guild_ids=[guild], description='Остановить воспроизведение')
    async def stop(self, inter: disnake.ApplicationCommandInteraction):
        try:
            await self.stop_voice(inter.guild.id)
            await inter.response.send_message("⏹️ Воспроизведение остановлено")
        except Exception as e:
            await inter.response.send_message(f"🚫 Ошибка: {str(e)}")


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
