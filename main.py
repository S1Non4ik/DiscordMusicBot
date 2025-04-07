import disnake
from disnake.ext import commands
from disnake.ext.commands import Bot
from cfg.cfg import *
import os


activity = disnake.Streaming(name="With 💙 by S1Non_",url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')

intents = disnake.Intents.all()
bot = Bot(command_prefix ='!', intents=intents, activity=activity, status=disnake.Status.do_not_disturb)
bot.remove_command("help")



@bot.event
async def on_ready():
    async def load_cogg(name):
        bot.load_extension(f"modules.{name}")

    async def reload_cogg(name):
        bot.unload_extension(f"modules.{name}")
        bot.load_extension(f"modules.{name}")


    print('------')
    print(bot.user.name)
    print(bot.user.id)
    print('------')
    for filename in os.listdir("./modules"):
        if filename.endswith(".py"):
            try:
                await load_cogg(filename[:-3])
            except Exception as ex:
                print(f"{filename[:-3]} crashed. I'm automaticly fixing it")
                await reload_cogg(filename[:-3])

bot.run(token)