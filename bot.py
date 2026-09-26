# -*- coding: utf-8 -*-
"""
بوت تذاكر عبر ديسكورد - دعم ميلاد
مبني باستخدام discord.py مع نظام سجلات (Logs) كامل ورابط مباشر لقناة كل تذكرة
"""

import os
import io
import logging
import asyncio
import datetime
from typing import Optional
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# ============ الاعدادات (config) - من متغيرات البيئة ============
# =========================================================

def _clean_id(raw: str):
    if not raw:
        return None
    cleaned = raw.strip().strip("<>").strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        raise RuntimeError(
            f"القيمة '{raw}' ليست رقم معرف صحيح. يجب ان تتكون من ارقام فقط بدون اقواس < > او مسافات."
        )


BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = _clean_id(os.getenv("GUILD_ID"))
TICKET_CATEGORY_ID = _clean_id(os.getenv("TICKET_CATEGORY_ID"))
STAFF_ROLE_ID = _clean_id(os.getenv("STAFF_ROLE_ID")) or 1552620926917419068
SPECIAL_ADMIN_ID = _clean_id(os.getenv("SPECIAL_ADMIN_ID")) or 920981254554406952
LOG_CHANNEL_ID = _clean_id(os.getenv("LOG_CHANNEL_ID")) or 1281894208550076477
PANEL_IMAGE_URL = os.getenv("PANEL_IMAGE_URL", "")

# توكن GitHub شخصي بصلاحية gist فقط، يستخدم لرفع سجل المحادثة كملف يفتح مباشرة بالمتصفح
# بدون هذا المتغير سيتم ارفاق ملف المحادثة داخل ديسكورد فقط بدون رابط معاينة بالمتصفح
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# الرموز التعبيرية المخصصة للازرار
TICKET_ICON_EMOJI = os.getenv("TICKET_ICON_EMOJI", "<:linkssssss:1536040564112367738>")
CLAIM_EMOJI = os.getenv("CLAIM_EMOJI", "<:claim:1536007978090500096>")
DELETE_EMOJI = os.getenv("DELETE_EMOJI", "<:delete:1536007930325770340>")

# عداد التذاكر التلقائي (يتم اعادة ضبطه تلقائيا عند الاقلاع بالاعتماد على القنوات الموجودة فعلا)
ticket_counter = 1

if not BOT_TOKEN:
    raise RuntimeError("يجب تعبئة متغير BOT_TOKEN من لوحة تحكم Railway (Variables).")

# =========================================================
# ===================== نهاية الاعدادات ====================
# =========================================================

# =========================================================
# ========================= اللوق =========================
# نظام تسجيل احترافي يطبع كل الاحداث والاخطاء في الكونسول
# (تقدر تشوفها مباشرة من صفحة Logs بمنصة Railway)
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("melaad_tickets")
# نخفت شوية ضجيج مكتبة discord الداخلية ونبقي فقط التحذيرات والاخطاء
logging.getLogger("discord").setLevel(logging.WARNING)


intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


def is_staff(member: discord.Member) -> bool:
    if member.id == SPECIAL_ADMIN_ID:
        return True
    if member.guild_permissions.administrator:
        return True
    role = discord.utils.get(member.roles, id=STAFF_ROLE_ID)
    return role is not None


def parse_topic(topic: str):
    data = {}
    if not topic:
        return data
    for part in topic.split("|"):
        if ":" in part:
            k, v = part.split(":", 1)
            data[k.strip()] = v.strip()
    return data


def ticket_jump_url(channel: discord.TextChannel) -> str:
    """رابط مباشر يودي لقناة التذكرة بالضغط عليه"""
    return f"https://discord.com/channels/{channel.guild.id}/{channel.id}"


async def safe_log_send(guild: discord.Guild, **send_kwargs):
    """ارسال رسالة الى قناة اللوق مع تسجيل اي خطا بدل ما يوقف تنفيذ باقي الكود"""
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        logger.warning("قناة اللوق (LOG_CHANNEL_ID=%s) غير موجودة او البوت لا يراها", LOG_CHANNEL_ID)
        return
    try:
        await log_channel.send(**send_kwargs)
    except discord.Forbidden:
        logger.error("لا يملك البوت صلاحية الارسال داخل قناة اللوق")
    except Exception:
        logger.exception("فشل ارسال رسالة الى قناة اللوق")


async def build_transcript_html(channel: discord.TextChannel) -> str:
    """تبني محتوى HTML كامل يحتوي على جميع رسائل وصور التذكرة كنص واحد"""
    messages = []
    async for msg in channel.history(limit=None, oldest_first=True):
        messages.append(msg)

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>سجل تذكرة - {channel.name}</title>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #36393f; color: #dcddde; padding: 20px; }}
            .header {{ border-bottom: 2px solid #000; padding-bottom: 10px; margin-bottom: 20px; }}
            .message {{ background-color: #2f3136; padding: 10px; margin-bottom: 10px; border-radius: 5px; }}
            .author {{ font-weight: bold; color: #5865f2; }}
            .time {{ font-size: 0.8em; color: #72767d; margin-right: 10px; }}
            .content {{ margin-top: 5px; white-space: pre-wrap; }}
            .attachment {{ margin-top: 5px; color: #00aff4; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>سجل المحادثة للتذكرة: {channel.name}</h2>
            <p>تاريخ الارشفة: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
        </div>
    """

    for msg in messages:
        time_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        content = discord.utils.escape_mentions(msg.content) if msg.content else ""
        attachments_html = ""
        for att in msg.attachments:
            attachments_html += f'<div class="attachment"><a href="{att.url}" target="_blank">مرفق: {att.filename}</a></div>'

        html_content += f"""
        <div class="message">
            <span class="author">{msg.author.display_name} ({msg.author})</span>
            <span class="time">{time_str}</span>
            <div class="content">{content}</div>
            {attachments_html}
        </div>
        """

    html_content += "</body></html>"
    return html_content


def html_to_discord_file(html_content: str, channel_name: str) -> discord.File:
    """يحول نص HTML جاهز الى ملف مرفق يرفع على ديسكورد"""
    file_bytes = io.BytesIO(html_content.encode("utf-8"))
    return discord.File(file_bytes, filename=f"transcript-{channel_name}.html")


async def upload_transcript_preview_link(channel_name: str, html_content: str) -> Optional[str]:
    """
    ترفع محتوى المحادثة كملف Gist عام على GitHub وترجع رابط معاينة
    يفتح مباشرة بالمتصفح (بدون تنزيل) عبر htmlpreview.github.io
    ترجع None اذا لم يكن متغير GITHUB_TOKEN معبى او صار خطا اثناء الرفع
    """
    if not GITHUB_TOKEN:
        return None

    filename = f"transcript-{channel_name}.html"
    payload = {
        "description": f"سجل محادثة تذكرة - {channel_name}",
        "public": True,
        "files": {
            filename: {"content": html_content}
        },
    }
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.github.com/gists",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.error("فشل رفع سجل المحادثة الى GitHub Gist، رمز الحالة: %s | %s", resp.status, body)
                    return None
                data = await resp.json()
                raw_url = data["files"][filename]["raw_url"]
                return f"https://htmlpreview.github.io/?{raw_url}"
    except Exception:
        logger.exception("خطا غير متوقع اثناء رفع سجل المحادثة الى GitHub")
        return None


async def auto_delete_ticket_task(channel_id: int, guild_id: int, owner_id: int):
    """
    تنتظر هذه الدالة مدة ساعتين ثم تتحقق مما اذا كان صاحب التذكرة نفسه
    قد ارسل اي رسالة داخل القناة، بغض النظر عن ردود فريق الدعم.
    في حال عدم وجود اي رد من صاحب التذكرة يتم حذف القناة دون اي اشعار.
    """
    await asyncio.sleep(7200)  # الانتظار ساعتين (7200 ثانية)

    try:
        current_channel = bot.get_channel(channel_id)
        if not current_channel:
            logger.info("تخطي الحذف التلقائي للقناة %s لانها غير موجودة اصلا", channel_id)
            return

        owner_replied = False
        async for msg in current_channel.history(limit=None, oldest_first=True):
            if msg.author.id == owner_id:
                owner_replied = True
                break

        if not owner_replied:
            logger.info("حذف تلقائي للتذكرة %s بسبب عدم رد صاحبها خلال ساعتين", current_channel.name)
            await current_channel.delete(reason="حذف تلقائي - لم يرد صاحب التذكرة خلال ساعتين")

            guild = bot.get_guild(guild_id)
            if guild:
                embed = discord.Embed(
                    title="حذف تلقائي لتذكرة",
                    description="تم حذف هذه التذكرة تلقائيا لعدم رد صاحبها خلال ساعتين من فتحها",
                    color=discord.Color.orange(),
                    timestamp=datetime.datetime.utcnow(),
                )
                embed.add_field(name="اسم التذكرة", value=current_channel.name, inline=True)
                embed.add_field(name="صاحب التذكرة", value=f"<@{owner_id}>", inline=True)
                await safe_log_send(guild, embed=embed)
        else:
            logger.info("لا حذف تلقائي للتذكرة %s لان صاحبها رد بالفعل", current_channel.name)
    except discord.NotFound:
        pass
    except Exception:
        logger.exception("خطا غير متوقع اثناء تنفيذ مهمة الحذف التلقائي للقناة %s", channel_id)


def next_ticket_number(guild: discord.Guild) -> int:
    """
    يحسب رقم التذكرة التالي بالاعتماد على القنوات الموجودة فعليا
    بدلا من الاعتماد فقط على متغير بالذاكرة قد يتصفر عند اعادة تشغيل البوت
    """
    max_number = 0
    channels = guild.channels
    for ch in channels:
        if isinstance(ch, discord.TextChannel) and ch.name.startswith("🎫・"):
            suffix = ch.name.split("🎫・", 1)[-1]
            if suffix.isdigit():
                max_number = max(max_number, int(suffix))
    return max_number + 1


def find_open_ticket(guild: discord.Guild, user_id: int):
    """يبحث عن تذكرة مفتوحة حاليا لنفس العضو لمنع فتح اكثر من تذكرة بنفس الوقت"""
    for ch in guild.channels:
        if not isinstance(ch, discord.TextChannel):
            continue
        data = parse_topic(ch.topic)
        if data.get("user") == str(user_id):
            return ch
    return None


# =========================================================
# ==================== نافذة تاكيد الحذف ===================
# =========================================================

class ConfirmDeleteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="تاكيد الحذف", style=discord.ButtonStyle.danger, custom_id="confirm_delete_btn")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        data = parse_topic(channel.topic)
        ticket_type = data.get("type")

        if ticket_type == "special":
            if interaction.user.id != SPECIAL_ADMIN_ID:
                await interaction.response.send_message("لا يمكنك حذف هذه التذكرة", ephemeral=True)
                return
        else:
            if not is_staff(interaction.user):
                await interaction.response.send_message("لا تملك صلاحية حذف التذكرة", ephemeral=True)
                return

        button.disabled = True
        await interaction.response.edit_message(content="جاري حفظ السجل وحذف التذكرة خلال خمس ثواني", view=None)

        owner_id = data.get("user")
        owner_mention = f"<@{owner_id}>" if owner_id else "غير معروف"

        try:
            html_content = await build_transcript_html(channel)
            transcript_file = html_to_discord_file(html_content, channel.name)
            preview_link = await upload_transcript_preview_link(channel.name, html_content)

            embed = discord.Embed(
                title="تم اغلاق التذكرة وحفظ السجل",
                color=discord.Color.red(),
                timestamp=datetime.datetime.utcnow()
            )
            embed.add_field(name="اسم التذكرة", value=channel.name, inline=True)
            embed.add_field(name="صاحب التذكرة", value=owner_mention, inline=True)
            embed.add_field(name="بواسطة", value=interaction.user.mention, inline=True)
            if preview_link:
                embed.add_field(name="فتح السجل بالمتصفح", value=f"[اضغط هنا لمشاهدة المحادثة]({preview_link})", inline=False)
            else:
                embed.add_field(
                    name="فتح السجل بالمتصفح",
                    value="غير متاح حاليا (لم يتم تعبئة متغير GITHUB_TOKEN)، الملف المرفق ادناه يحتوي على السجل كاملا",
                    inline=False
                )

            # يتم ارسال السجل مع ملف المحادثة نفسه والابقاء عليه في القناة (بدون حذفه)
            await safe_log_send(interaction.guild, embed=embed, file=transcript_file)
            logger.info("تم اغلاق التذكرة %s بواسطة %s", channel.name, interaction.user)
        except Exception:
            logger.exception("فشل انشاء او ارسال سجل التذكرة %s قبل حذفها", channel.name)

        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"تم الاغلاق بواسطة {interaction.user}")
        except discord.NotFound:
            pass
        except Exception:
            logger.exception("فشل حذف قناة التذكرة %s", channel.name)


# =========================================================
# ==================== ازرار / نوافذ الحذف والاستلام =========
# =========================================================

class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji=DELETE_EMOJI, custom_id="ticket_delete")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        data = parse_topic(channel.topic)
        ticket_type = data.get("type")

        if ticket_type == "special":
            if interaction.user.id != SPECIAL_ADMIN_ID:
                await interaction.response.send_message("لا يمكنك حذف هذه التذكرة", ephemeral=True)
                return
        else:
            if not is_staff(interaction.user):
                await interaction.response.send_message("لا تملك صلاحية حذف التذكرة", ephemeral=True)
                return

        await interaction.response.send_message(
            content="هل انت متاكد من انك تريد حذف هذه التذكرة؟",
            view=ConfirmDeleteView(),
            ephemeral=True
        )

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji=CLAIM_EMOJI, custom_id="ticket_claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        guild = interaction.guild
        data = parse_topic(channel.topic)
        ticket_type = data.get("type")

        if ticket_type == "special":
            if interaction.user.id != SPECIAL_ADMIN_ID:
                await interaction.response.send_message("لا يمكنك استلام هذه التذكرة", ephemeral=True)
                return
        else:
            if not is_staff(interaction.user):
                await interaction.response.send_message("لا تملك صلاحية استلام التذكرة", ephemeral=True)
                return

        # نؤكد استلام التفاعل فورا قبل اي طلب بطيء لتفادي خطا عدم الاستجابة
        await interaction.response.defer()

        try:
            staff_role = guild.get_role(STAFF_ROLE_ID)
            if staff_role:
                await channel.set_permissions(staff_role, view_channel=False)

            await channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True)
        except discord.Forbidden:
            logger.error("صلاحيات ناقصة عند محاولة استلام التذكرة %s من قبل %s", channel.name, interaction.user)
            await interaction.followup.send(
                "لا يملك البوت الصلاحيات الكافية لتعديل صلاحيات هذه القناة، تاكد من صلاحية Manage Channels وترتيب رتبة البوت",
                ephemeral=True
            )
            return
        except Exception:
            logger.exception("خطا غير متوقع اثناء استلام التذكرة %s", channel.name)
            await interaction.followup.send("حدث خطا غير متوقع اثناء استلام التذكرة", ephemeral=True)
            return

        button.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            logger.exception("فشل تعديل زر الاستلام بعد استلام التذكرة %s", channel.name)

        await interaction.followup.send(f"تم استلام هذه التذكرة من قبل {interaction.user.mention}")
        logger.info("تم استلام التذكرة %s من قبل %s", channel.name, interaction.user)

        embed = discord.Embed(
            title="تم استلام التذكرة وقفلها",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="القناة", value=channel.mention, inline=True)
        embed.add_field(name="المستلم", value=interaction.user.mention, inline=True)
        embed.add_field(name="رابط التذكرة", value=f"[اضغط هنا]({ticket_jump_url(channel)})", inline=False)
        await safe_log_send(guild, embed=embed)


# =========================================================
# ================= قائمة اختيار نوع التذكرة =================
# =========================================================

class TicketTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="استفسار", value="inquiry"),
            discord.SelectOption(label="شكوى", value="complaint"),
            discord.SelectOption(label="مساعدة", value="help"),
            discord.SelectOption(label="رتب خاصة", value="special"),
        ]
        super().__init__(placeholder="اختر طلبك", options=options, min_values=1, max_values=1,
                         custom_id="ticket_type_select")

    async def callback(self, interaction: discord.Interaction):
        global ticket_counter
        value = self.values[0]
        guild = interaction.guild
        user = interaction.user

        # نؤكد استلام التفاعل فورا لان انشاء القناة قد ياخذ وقتا او يفشل
        await interaction.response.defer(ephemeral=True)

        existing_ticket = find_open_ticket(guild, user.id)
        if existing_ticket:
            await interaction.edit_original_response(
                content=f"لديك تذكرة مفتوحة بالفعل هنا {existing_ticket.mention}",
                view=None
            )
            return

        category = None
        if TICKET_CATEGORY_ID:
            category = guild.get_channel(TICKET_CATEGORY_ID)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        if value == "special":
            special_member = guild.get_member(SPECIAL_ADMIN_ID)
            if special_member:
                overwrites[special_member] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
        else:
            staff_role = guild.get_role(STAFF_ROLE_ID)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )

        channel_number = max(ticket_counter, next_ticket_number(guild))
        channel_name = f"🎫・{channel_number}"
        ticket_counter = channel_number + 1

        topic = f"type:{value}|user:{user.id}"

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=topic,
                reason=f"فتح تذكرة جديدة بواسطة {user}",
            )
        except discord.Forbidden:
            logger.error("صلاحيات ناقصة عند انشاء قناة تذكرة جديدة للعضو %s", user)
            await interaction.edit_original_response(
                content="لا يملك البوت الصلاحيات الكافية لانشاء قناة تذكرة جديدة، يرجى مراجعة الادارة",
                view=None
            )
            return
        except Exception:
            logger.exception("فشل انشاء قناة تذكرة جديدة للعضو %s", user)
            await interaction.edit_original_response(
                content="حدث خطا غير متوقع اثناء انشاء التذكرة، حاول مرة اخرى لاحقا",
                view=None
            )
            return

        staff_role = guild.get_role(STAFF_ROLE_ID)
        mention_line = f"{staff_role.mention if staff_role else 'فريق الدعم'} | {user.mention}"

        welcome_embed = discord.Embed(
            description="اهلا وسهلا يرجى كتابة موضوع طلبك وسيتم الرد عليك من قبل المسؤولين",
            color=discord.Color.dark_theme(),
        )
        if PANEL_IMAGE_URL:
            welcome_embed.set_image(url=PANEL_IMAGE_URL)

        try:
            ticket_message = await ticket_channel.send(
                content=mention_line, embed=welcome_embed, view=TicketActionsView()
            )
            await ticket_message.pin()
        except discord.HTTPException:
            logger.warning("لم يتم تثبيت رسالة الترحيب داخل التذكرة %s", ticket_channel.name)
        except Exception:
            logger.exception("فشل ارسال رسالة الترحيب داخل التذكرة %s", ticket_channel.name)

        embed = discord.Embed(
            title="تم فتح تذكرة جديدة",
            color=discord.Color.green(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="صاحب التذكرة", value=user.mention, inline=True)
        embed.add_field(name="النوع", value=value, inline=True)
        embed.add_field(name="القناة", value=ticket_channel.mention, inline=True)
        embed.add_field(name="رابط التذكرة", value=f"[اضغط هنا]({ticket_jump_url(ticket_channel)})", inline=False)
        await safe_log_send(guild, embed=embed)

        logger.info("تم فتح تذكرة جديدة (%s) من النوع %s بواسطة %s", ticket_channel.name, value, user)

        await interaction.edit_original_response(content=f"تم فتح تذكرتك هنا {ticket_channel.mention}", view=None)

        # البدء بمراقبة الحذف التلقائي بعد ساعتين من عدم رد صاحب التذكرة
        asyncio.create_task(auto_delete_ticket_task(ticket_channel.id, guild.id, user.id))


class TicketTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())


# =========================================================
# ===================== لوحة فتح التذكرة =====================
# =========================================================

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji=TICKET_ICON_EMOJI, custom_id="open_ticket_panel")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(view=TicketTypeView(), ephemeral=True)


# =========================================================
# ========================= الاوامر =========================
# =========================================================

@bot.tree.command(name="panel", description="ارسال لوحة فتح التذاكر")
@app_commands.checks.has_permissions(administrator=True)
async def panel(interaction: discord.Interaction):
    content = PANEL_IMAGE_URL if PANEL_IMAGE_URL else ""

    await interaction.channel.send(content=content, view=TicketPanelView())
    await interaction.response.send_message("تم ارسال اللوحة", ephemeral=True)
    logger.info("تم ارسال لوحة فتح التذاكر بواسطة %s في القناة %s", interaction.user, interaction.channel)


@panel.error
async def panel_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("هذا الامر مخصص للمشرفين فقط", ephemeral=True)
    else:
        raise error


@bot.tree.command(name="memberadd", description="اضافة شخص للتذكرة الحالية")
@app_commands.describe(member="الشخص الذي تريد اضافته الى التذكرة")
async def memberadd(interaction: discord.Interaction, member: discord.Member):
    channel = interaction.channel
    data = parse_topic(channel.topic)

    if "type" not in data:
        await interaction.response.send_message("هذا الامر يعمل فقط داخل قناة تذكرة", ephemeral=True)
        return

    if not is_staff(interaction.user):
        await interaction.response.send_message("لا تملك صلاحية اضافة اشخاص الى التذاكر", ephemeral=True)
        return

    # نؤكد استلام التفاعل فورا حتى لا يظهر خطا عدم الاستجابة اذا تاخر الطلب التالي
    await interaction.response.defer(ephemeral=False)

    try:
        await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
    except discord.Forbidden:
        logger.error("صلاحيات ناقصة عند اضافة العضو %s الى التذكرة %s", member, channel.name)
        await interaction.followup.send(
            "لا يملك البوت الصلاحيات الكافية لاضافة هذا العضو الى القناة، تاكد من صلاحية Manage Channels وترتيب رتبة البوت",
            ephemeral=True
        )
        return
    except Exception:
        logger.exception("خطا غير متوقع اثناء اضافة العضو %s الى التذكرة %s", member, channel.name)
        await interaction.followup.send("حدث خطا غير متوقع اثناء اضافة العضو الى التذكرة", ephemeral=True)
        return

    await interaction.followup.send(f"تم اضافة {member.mention} الى التذكرة")
    logger.info("تم اضافة العضو %s الى التذكرة %s بواسطة %s", member, channel.name, interaction.user)


@memberadd.error
async def memberadd_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("لا تملك صلاحية استخدام هذا الامر", ephemeral=True)
    else:
        raise error


# =========================================================
# ============= معالج اخطاء عام لكل اوامر السلاش =============
# اي خطا غير متوقع بأي امر (حتى لو ما فيه معالج خاص فيه)
# يتم تسجيله باللوق والرد على المستخدم بدل ما يعلق بصمت
# =========================================================

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    command_name = interaction.command.name if interaction.command else "غير معروف"
    logger.error("خطا غير متوقع في الامر '%s' من قبل %s: %s", command_name, interaction.user, error, exc_info=error)

    try:
        if interaction.response.is_done():
            await interaction.followup.send("حدث خطا غير متوقع اثناء تنفيذ الامر", ephemeral=True)
        else:
            await interaction.response.send_message("حدث خطا غير متوقع اثناء تنفيذ الامر", ephemeral=True)
    except Exception:
        logger.exception("فشل حتى ارسال رسالة الخطا للمستخدم")


@bot.event
async def on_error(event_method, *args, **kwargs):
    logger.exception("خطا غير متوقع داخل الحدث '%s'", event_method)


# =========================================================
# ========================= الاقلاع =========================
# =========================================================

@bot.event
async def on_ready():
    global ticket_counter

    bot.add_view(TicketPanelView())
    bot.add_view(TicketTypeView())
    bot.add_view(TicketActionsView())

    if GUILD_ID:
        guild_obj = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild_obj)
        await bot.tree.sync(guild=guild_obj)
    else:
        await bot.tree.sync()

    # اعادة ضبط عداد التذاكر بالاعتماد على القنوات الموجودة فعليا بكل السيرفرات
    for guild in bot.guilds:
        ticket_counter = max(ticket_counter, next_ticket_number(guild))

    logger.info("تم تسجيل الدخول كـ %s (معرف: %s)", bot.user, bot.user.id)
    logger.info("عدد السيرفرات المتصلة: %s | عداد التذاكر الحالي: %s", len(bot.guilds), ticket_counter)


if __name__ == "__main__":
    bot.run(BOT_TOKEN)
