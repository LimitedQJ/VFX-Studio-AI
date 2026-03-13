import discord
from discord.ext import commands
from discord import app_commands
from PIL import Image
import io
import os
import asyncio
import datetime
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
executor = ThreadPoolExecutor()


# ─────────────────────────────────────────
# CORE: slice frames and build APNG
# ─────────────────────────────────────────

def build_webp(image_bytes: bytes, grid_x: int, grid_y: int, frame_size: int, fps: int = 30) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    expected_w = grid_x * frame_size
    expected_h = grid_y * frame_size

    if img.width != expected_w or img.height != expected_h:
        img = img.resize((expected_w, expected_h), Image.LANCZOS)

    frames = []
    for row in range(grid_y):
        for col in range(grid_x):
            left = col * frame_size
            top = row * frame_size
            frame = img.crop((left, top, left + frame_size, top + frame_size)).convert("RGBA")
            frame = frame.resize((1024, 1024), Image.LANCZOS)
            frames.append(frame)

    duration_ms = max(20, int(1000 / fps))
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=duration_ms,
        lossless=False,
        quality=90,
    )
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────
# MODAL: Flipbook settings
# ─────────────────────────────────────────

class FlipbookSettingsModal(discord.ui.Modal, title="⚙️ Flipbook Settings"):
    grid_x = discord.ui.TextInput(
        label="Grid X (columns)",
        placeholder="e.g. 6",
        min_length=1, max_length=3
    )
    grid_y = discord.ui.TextInput(
        label="Grid Y (rows)",
        placeholder="e.g. 6",
        min_length=1, max_length=3
    )

    def __init__(self, image_bytes: bytes):
        super().__init__()
        self.image_bytes = image_bytes

    async def on_submit(self, interaction: discord.Interaction):
        try:
            gx = int(self.grid_x.value)
            gy = int(self.grid_y.value)
            assert gx > 0 and gy > 0
        except Exception:
            await interaction.response.send_message(
                "❌ Please enter valid numbers greater than 0.", ephemeral=True
            )
            return

        img = Image.open(io.BytesIO(self.image_bytes))
        w, h = img.size

        if w % gx != 0 or h % gy != 0:
            await interaction.response.send_message(
                f"❌ Image size **{w}×{h}px** is not divisible by grid **{gx}×{gy}**. Try different grid values.",
                ephemeral=True
            )
            return

        res_x = w // gx
        res_y = h // gy

        await interaction.response.defer(thinking=True, ephemeral=False)

        try:
            loop = asyncio.get_running_loop()
            webp_bytes = await loop.run_in_executor(
                executor, lambda: build_webp(self.image_bytes, gx, gy, res_x, fps=30)
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create WebP: {e}", ephemeral=True)
            return

        file = discord.File(io.BytesIO(webp_bytes), filename="flipbook.webp")
        await interaction.followup.send(file=file)


# ─────────────────────────────────────────
# VIEW: Static / Flipbook buttons
# ─────────────────────────────────────────

class TextureTypeView(discord.ui.View):
    def __init__(self, image_bytes: bytes):
        super().__init__(timeout=120)
        self.image_bytes = image_bytes

    @discord.ui.button(label="🖼️ Static", style=discord.ButtonStyle.secondary)
    async def static_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        img_file = discord.File(io.BytesIO(self.image_bytes), filename="texture.png")
        await interaction.response.send_message(file=img_file)

    @discord.ui.button(label="🎞️ Flipbook", style=discord.ButtonStyle.primary)
    async def flipbook_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        modal = FlipbookSettingsModal(image_bytes=self.image_bytes)
        await interaction.response.send_modal(modal)


# ─────────────────────────────────────────
# SLASH COMMAND: /flipbookcreate
# ─────────────────────────────────────────

@bot.tree.command(name="createapreview", description="Create a preview gives you the right to watch an animation of a texture.")
@app_commands.describe(image="Upload your flipbook image")
async def flipbook_create(interaction: discord.Interaction, image: discord.Attachment):
    if not image.content_type or not image.content_type.startswith("image/"):
        await interaction.response.send_message("❌ Please attach an image file!", ephemeral=True)
        return

    image_bytes = await image.read()

    embed = discord.Embed(
        title="🎨 Texture Setup",
        description="Select the texture type:",
        color=discord.Color.blurple()
    )
    embed.add_field(name="🖼️ Static", value="Send the image as-is", inline=True)
    embed.add_field(name="🎞️ Flipbook", value="Slice into frames and create a WebP", inline=True)
    embed.set_image(url=image.url)

    view = TextureTypeView(image_bytes=image_bytes)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)



# ─────────────────────────────────────────
# EVENTS & ERROR HANDLER
# ─────────────────────────────────────────

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot is online as {bot.user} (ID: {bot.user.id})")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
    else:
        if not interaction.response.is_done():
            await interaction.response.send_message(f"⚠️ An error occurred: {error}", ephemeral=True)
        raise error


bot.run(TOKEN)
