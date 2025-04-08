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
    "extract_flat": "in_playlist",
    "no_cache": True,
    "socket_timeout": 15,
    "cookiefile": "cookies.txt",
    "ignoreerrors": True,
    "age_limit": 18,
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
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
            for guild_id in list(queues.keys()):
                queues[guild_id] = [t for t in queues[guild_id] if time.time() - t['added_at'] < 1800]

    @commands.slash_command(guild_ids=[guild], description='Воспроизвести трек или плейлист')
    async def play(self, inter: disnake.ApplicationCommandInteraction, query: str):
        try:
            if not inter.author.voice:
                return await inter.response.send_message("🔇 Вы не в голосовом канале!", ephemeral=True)

            if inter.guild.id not in voice_clients:
                voice_client = await inter.author.voice.channel.connect()
                voice_clients[inter.guild.id] = voice_client
                await inter.response.send_message(f"🔊 Подключился к {inter.author.voice.channel.mention}")
            else:
                await inter.response.defer()
                voice_client = voice_clients[inter.guild.id]

            if inter.guild.id not in queues:
                queues[inter.guild.id] = []

            data = self.extract_info(query)

            if 'entries' in data:
                playlist_title = data.get('title', 'Неизвестный плейлист')
                track_titles = [entry.get('title', 'Неизвестный трек') for entry in data['entries']]
                queues[inter.guild.id].append({
                    'url': query,
                    'title': f"Плейлист - {playlist_title}",
                    'tracks': track_titles,
                    'added_at': time.time()
                })
                await inter.followup.send(f"🎶 Добавлен {playlist_title} с треками: {', '.join(track_titles)}")
            else:  # Если это одиночный трек
                track_title = data.get('title', 'Неизвестный трек')
                queues[inter.guild.id].append({
                    'url': query,
                    'title': track_title,
                    'added_at': time.time()
                })
                await inter.followup.send(f"🎶 Добавлен трек: {track_title}")

            await self.show_queue(inter)

            if len(queues[inter.guild.id]) == 1:
                await self.play_next(inter.guild.id)

        except Exception as e:
            await inter.followup.send(f"🚫 Ошибка: {str(e)}")
            print(f"Play error: {e}")

    async def show_queue(self, inter: disnake.ApplicationCommandInteraction):
        queue = queues.get(inter.guild.id, [])
        if not queue:
            await inter.followup.send("🎶 Очередь пуста.")
            return

        queue_message = "🎶 Текущая очередь:\n"
        for idx, track in enumerate(queue):
            if 'tracks' in track:  # Если это плейлист
                queue_message += f"{idx + 1}. {track['title']} - Треки: {', '.join(track['tracks'])}\n"
            else:  # Если это одиночный трек
                queue_message += f"{idx + 1}. {track['title']}\n"

        await inter.followup.send(queue_message)

    async def play_next(self, guild_id, retries=3):
        if not queues.get(guild_id) or not voice_clients.get(guild_id):
            return

        try:
            track = queues[guild_id][0]
            loop = asyncio.get_event_loop()

            data = await loop.run_in_executor(None, self.extract_info, track['url'])

            if 'entries' in data:
                entries = list(data['entries'])
                if not entries:
                    raise Exception("Плейлист пуст")

                queues[guild_id].pop(0)

                for entry in reversed(entries):
                    if entry and entry.get('url'):
                        queues[guild_id].insert(0, {
                            'url': entry['url'],
                            'title': entry.get('title', 'Неизвестный трек'),  # Сохраняем название трека
                            'added_at': time.time()
                        })

                await self.play_next(guild_id)
                return

            if 'url' not in data:
                raise Exception("Не удалось получить URL трека")

            url = data['url']
            headers = data.get('http_headers', {})

            headers_str = "\r\n".join([f"{k}: {v}" for k, v in headers.items()])
            current_ffmpeg_options = {
                'before_options': ffmpeg_options['before_options'],
                'options': f'{ffmpeg_options["options"]} -headers "{headers_str}"'
            }

            player = FFmpegPCMAudio(url, **current_ffmpeg_options)

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

    def extract_info(self, url):
        return ytdl.extract_info(
            url,
            download=False,
            process=True,
            extra_info={'extract_flat': True}
        )

    async def song_finished(self, guild_id):
        try:
            if queues.get(guild_id) and len(queues[guild_id]) > 0:
                queues[guild_id].pop(0)

            if queues.get(guild_id) and len(queues[guild_id]) > 0:
                await self.play_next(guild_id)
            else:
                await self.stop_voice(guild_id)
        except Exception as e:
            print(f"Ошибка завершения трека: {e}")

    async def force_skip(self, guild_id):
        try:
            if queues.get(guild_id) and len(queues[guild_id]) > 0:
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
        except KeyError:
            pass
        except Exception as e:
            print(f"Ошибка отключения: {e}")

        try:
            if guild_id in queues:
                del queues[guild_id]
        except KeyError:
            pass

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
            print(f"Skip error: {e}")

    @commands.slash_command(guild_ids=[guild], description='Остановить воспроизведение')
    async def stop(self, inter: disnake.ApplicationCommandInteraction):
        try:
            await self.stop_voice(inter.guild.id)
            await inter.response.send_message("⏹️ Воспроизведение остановлено")
        except Exception as e:
            await inter.response.send_message(f"🚫 Ошибка: {str(e)}")
            print(f"Stop error: {e}")


def setup(bot: commands.Bot):
    bot.add_cog(Music(bot))
