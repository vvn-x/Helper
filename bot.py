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
import html
import sqlite3
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
STAFF_ROLE_ID = _clean_id(os.getenv("STAFF_ROLE_ID")) or 1535668575585566871
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

# حظر فتح التذاكر مؤقتاً، محفوظ في SQLite ليبقى بعد إعادة التشغيل.
TICKET_BAN_DB = os.getenv("TICKET_BAN_DB", "ticket_bans.sqlite3")

def init_ticket_ban_db():
    with sqlite3.connect(TICKET_BAN_DB) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ticket_bans (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, expires_at TEXT, reason TEXT, PRIMARY KEY (guild_id, user_id))")

def get_ticket_ban(guild_id: int, user_id: int):
    with sqlite3.connect(TICKET_BAN_DB) as conn:
        row = conn.execute("SELECT expires_at FROM ticket_bans WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
        if not row:
            return None
        if row[0] is None:
            return "permanent"
        expires = datetime.datetime.fromisoformat(row[0])
        if expires <= datetime.datetime.now(datetime.timezone.utc):
            conn.execute("DELETE FROM ticket_bans WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            return None
        return expires

def set_ticket_ban(guild_id: int, user_id: int, hours: Optional[int], reason: str):
    expires = None if hours is None else (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).isoformat()
    with sqlite3.connect(TICKET_BAN_DB) as conn:
        conn.execute("INSERT OR REPLACE INTO ticket_bans (guild_id,user_id,expires_at,reason) VALUES (?,?,?,?)", (guild_id,user_id,expires,reason))

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

    safe_channel_name = html.escape(channel.name)
    html_content = f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>سجل تذكرة - {safe_channel_name}</title>
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
            <h2>سجل المحادثة للتذكرة: {safe_channel_name}</h2>
            <p>تاريخ الارشفة: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
        </div>
    """

    for msg in messages:
        time_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        content = html.escape(discord.utils.escape_mentions(msg.content)) if msg.content else ""
        author_name = html.escape(str(msg.author.display_name))
        author_tag = html.escape(str(msg.author))
        attachments_html = ""
        for att in msg.attachments:
            attachments_html += f'<div class="attachment"><a href="{att.url}" target="_blank" rel="noopener noreferrer">مرفق: {html.escape(att.filename)}</a></div>'

        html_content += f"""
        <div class="message">
            <span class="author">{author_name} ({author_tag})</span>
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
        "public": False,
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
        if data.get("user") == str(user_id) and data.get("type") in {"inquiry", "complaint", "help", "special"}:
            return ch
    return None


# =========================================================
# ==================== نافذة تاكيد الحذف ===================
# =========================================================

class ConfirmDeleteView(discord.ui.View):
    def __init__(self, requester_id: int, ticket_channel_id: int):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.ticket_channel_id = ticket_channel_id

    @discord.ui.button(label="تاكيد الحذف", style=discord.ButtonStyle.danger, custom_id="confirm_delete_btn")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.guild.get_channel(self.ticket_channel_id) if interaction.guild else None
        if channel is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("قناة التذكرة لم تعد موجودة", ephemeral=True)
            return
        data = parse_topic(channel.topic)
        ticket_type = data.get("type")
        claimer_id = data.get("claimed")
        allowed = interaction.user.guild_permissions.administrator or (claimer_id and str(interaction.user.id) == claimer_id)
        if ticket_type == "special":
            allowed = interaction.user.guild_permissions.administrator or interaction.user.id == SPECIAL_ADMIN_ID
        if not allowed:
            await interaction.response.send_message("إغلاق التذكرة مسموح فقط لمستلمها أو للأدمنستريتر", ephemeral=True)
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

class RenameTicketModal(discord.ui.Modal, title="إعادة تسمية التذكرة"):
    new_name = discord.ui.TextInput(
        label="الاسم الجديد للتذكرة",
        placeholder="اكتب الاسم الجديد بدون رموز أو مسافات",
        max_length=90,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("هذا الخيار يعمل داخل قناة التذكرة فقط", ephemeral=True)
            return
        cleaned = "-".join(str(self.new_name).strip().split()).lower()
        if not cleaned:
            await interaction.response.send_message("اكتب اسمًا صالحًا للتذكرة", ephemeral=True)
            return
        try:
            await channel.edit(name=cleaned[:100], reason=f"إعادة تسمية التذكرة بواسطة {interaction.user}")
            await interaction.response.send_message(f"تم تغيير اسم التذكرة إلى {channel.mention}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("البوت لا يملك صلاحية إدارة القنوات", ephemeral=True)


class TicketMemberSelect(discord.ui.UserSelect):
    def __init__(self, action: str):
        self.action = action
        placeholder = "اختر العضو المراد إضافته" if action == "add" else "اختر العضو المراد إزالته"
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, custom_id=f"ticket_member_{action}")

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel
        member = self.values[0]
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("هذا الخيار يعمل داخل قناة التذكرة فقط", ephemeral=True)
            return
        try:
            if self.action == "add":
                await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
                msg = f"تمت إضافة {member.mention} إلى التذكرة"
            else:
                await channel.set_permissions(member, overwrite=None)
                msg = f"تمت إزالة {member.mention} من التذكرة"
            await interaction.response.send_message(msg, ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("البوت لا يملك صلاحية تعديل صلاحيات القناة", ephemeral=True)


class TicketMemberActionView(discord.ui.View):
    def __init__(self, action: str):
        super().__init__(timeout=120)
        self.add_item(TicketMemberSelect(action))


class TicketBanDurationSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        self.owner_id = owner_id
        options = [
            discord.SelectOption(label="ساعة واحدة", value="1"),
            discord.SelectOption(label="6 ساعات", value="6"),
            discord.SelectOption(label="24 ساعة", value="24"),
            discord.SelectOption(label="3 أيام", value="72"),
            discord.SelectOption(label="7 أيام", value="168"),
            discord.SelectOption(label="دائم من فتح التذاكر", value="permanent"),
        ]
        super().__init__(placeholder="اختر مدة منع فتح التذاكر", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("تحتاج إلى صلاحية إدارة السيرفر لاستخدام هذا الخيار", ephemeral=True)
            return
        value = self.values[0]
        hours = None if value == "permanent" else int(value)
        set_ticket_ban(interaction.guild.id, self.owner_id, hours, f"تم بواسطة {interaction.user}")
        if hours is None:
            msg = f"تم منع <@{self.owner_id}> من فتح التذاكر بشكل دائم."
        else:
            expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)
            msg = f"تم منع <@{self.owner_id}> من فتح التذاكر لمدة {hours} ساعة. ينتهي الحظر <t:{int(expiry.timestamp())}:R>."
        await interaction.response.edit_message(content=msg, view=None)

class TicketBanDurationView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=120)
        self.add_item(TicketBanDurationSelect(owner_id))


class TicketManagementSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="إعادة تسمية التذكرة", value="rename", description="تغيير اسم هذه التذكرة"),
            discord.SelectOption(label="إضافة عضو", value="add_member", description="إضافة عضو إلى هذه التذكرة"),
            discord.SelectOption(label="إزالة عضو", value="remove_member", description="إزالة عضو من هذه التذكرة"),
            discord.SelectOption(label="استدعاء شخص", value="summon", description="إرسال تنبيه لصاحب التذكرة"),
            discord.SelectOption(label="منع صاحب التذكرة من فتح التذاكر", value="ban_owner", description="اختيار مدة منع فتح التذاكر"),
            discord.SelectOption(label="إغلاق التذكرة", value="close", description="حفظ السجل ثم إغلاق التذكرة"),
        ]
        super().__init__(placeholder="Choose an option", options=options, min_values=1, max_values=1,
                         custom_id="ticket_management_select")

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("هذا الخيار يعمل داخل قناة التذكرة فقط", ephemeral=True)
            return
        data = parse_topic(channel.topic)
        if data.get("type") not in {"inquiry", "complaint", "help", "special"}:
            await interaction.response.send_message("هذه القناة ليست تذكرة معتمدة", ephemeral=True)
            return
        if not is_staff(interaction.user) and interaction.user.id != SPECIAL_ADMIN_ID:
            await interaction.response.send_message("لا تملك صلاحية إدارة هذه التذكرة", ephemeral=True)
            return

        choice = self.values[0]
        if choice == "rename":
            await interaction.response.send_modal(RenameTicketModal())
        elif choice == "add_member":
            await interaction.response.send_message("اختر العضو الذي تريد إضافته:", view=TicketMemberActionView("add"), ephemeral=True)
        elif choice == "remove_member":
            await interaction.response.send_message("اختر العضو الذي تريد إزالته:", view=TicketMemberActionView("remove"), ephemeral=True)
        elif choice == "summon":
            owner_id = data.get("user")
            if not owner_id:
                await interaction.response.send_message("تعذر العثور على صاحب التذكرة", ephemeral=True)
                return
            await interaction.response.send_message(f"تم استدعاء صاحب التذكرة <@{owner_id}>", ephemeral=True)
            await channel.send(f"<@{owner_id}>، أحد المسؤولين يطلب حضورك إلى التذكرة.", allowed_mentions=discord.AllowedMentions(users=True))
        elif choice == "ban_owner":
            owner_id = data.get("user")
            if not owner_id:
                await interaction.response.send_message("تعذر العثور على صاحب التذكرة", ephemeral=True)
                return
            if not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
                await interaction.response.send_message("تحتاج إلى صلاحية إدارة السيرفر لاستخدام هذا الخيار", ephemeral=True)
                return
            await interaction.response.send_message(
                f"اختر مدة منع صاحب التذكرة <@{owner_id}> من فتح تذاكر جديدة:",
                view=TicketBanDurationView(int(owner_id)), ephemeral=True
            )
        elif choice == "close":
            claimer_id = data.get("claimed")
            allowed = interaction.user.guild_permissions.administrator or (claimer_id and str(interaction.user.id) == claimer_id)
            if data.get("type") == "special":
                allowed = interaction.user.guild_permissions.administrator or interaction.user.id == SPECIAL_ADMIN_ID
            if not allowed:
                await interaction.response.send_message("إغلاق التذكرة مسموح فقط لمستلمها أو للأدمنستريتر", ephemeral=True)
                return
            await interaction.response.send_message(
                content="هل أنت متأكد من أنك تريد إغلاق هذه التذكرة؟",
                view=ConfirmDeleteView(interaction.user.id, channel.id), ephemeral=True
            )


class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketManagementSelect())

    @discord.ui.button(label="استلام التذكرة", style=discord.ButtonStyle.secondary, custom_id="ticket_claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        guild = interaction.guild
        if not isinstance(channel, discord.TextChannel) or guild is None:
            await interaction.response.send_message("هذا الخيار يعمل داخل التذكرة فقط", ephemeral=True)
            return
        data = parse_topic(channel.topic)
        ticket_type = data.get("type")
        if ticket_type == "special":
            if interaction.user.id != SPECIAL_ADMIN_ID:
                await interaction.response.send_message("لا يمكنك استلام هذه التذكرة", ephemeral=True)
                return
        elif not is_staff(interaction.user):
            await interaction.response.send_message("لا تملك صلاحية استلام التذكرة", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            staff_role = guild.get_role(STAFF_ROLE_ID)
            if staff_role:
                await channel.set_permissions(staff_role, view_channel=False)
            await channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True)
        except discord.Forbidden:
            await interaction.followup.send("لا يملك البوت الصلاحيات الكافية لتعديل صلاحيات هذه القناة", ephemeral=True)
            return

        topic_data = parse_topic(channel.topic)
        topic_data["claimed"] = str(interaction.user.id)
        new_topic = "|".join(f"{key}:{val}" for key, val in topic_data.items())
        try:
            await channel.edit(topic=new_topic, reason=f"تم استلام التذكرة بواسطة {interaction.user}")
        except Exception:
            logger.exception("تعذر حفظ هوية مستلم التذكرة %s", channel.name)
            await interaction.followup.send("تم تعديل الصلاحيات لكن تعذر حفظ هوية المستلم", ephemeral=True)
            return

        button.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            logger.exception("فشل تعديل زر الاستلام بعد استلام التذكرة %s", channel.name)

        await interaction.followup.send(f"تم استلام التذكرة بواسطة {interaction.user.display_name}")
        logger.info("تم استلام التذكرة %s من قبل %s", channel.name, interaction.user)

        embed = discord.Embed(title="تم استلام التذكرة وقفلها", color=discord.Color.blue(), timestamp=datetime.datetime.utcnow())
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

        ticket_ban = get_ticket_ban(guild.id, user.id)
        if ticket_ban == "permanent":
            await interaction.edit_original_response(content="لا يمكنك فتح تذكرة جديدة؛ تم منعك من فتح التذاكر بشكل دائم.", view=None)
            return
        if ticket_ban:
            await interaction.edit_original_response(content=f"لا يمكنك فتح تذكرة جديدة حالياً. ينتهي المنع <t:{int(ticket_ban.timestamp())}:R>.", view=None)
            return

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
            if category is None or not isinstance(category, discord.CategoryChannel):
                await interaction.edit_original_response(content="معرف تصنيف التذاكر غير صحيح أو التصنيف غير موجود", view=None)
                return

        if guild.me is None:
            await interaction.edit_original_response(content="تعذر تحديد عضوية البوت داخل السيرفر؛ حاول لاحقًا", view=None)
            return

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

        ticket_type_ar = {
            "inquiry": "استفسار",
            "complaint": "شكوى",
            "help": "مساعدة",
            "special": "رتب خاصة",
        }[value]

        welcome_embed = discord.Embed(
            description=(
                "اهلا وسهلا يرجى كتابة موضوع طلبك وسيتم الرد عليك من قبل المسؤولين"
                f"\\n\\nنوع التذكرة : {ticket_type_ar}"
            ),
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
            logger.exception("فشل إرسال أو تثبيت رسالة الترحيب داخل التذكرة %s", ticket_channel.name)
            await safe_log_send(guild, content=f"تنبيه: تعذر إرسال/تثبيت رسالة الترحيب في {ticket_channel.mention}. يلزم تدخل إداري.")
        except Exception:
            logger.exception("فشل ارسال رسالة الترحيب داخل التذكرة %s", ticket_channel.name)
            await safe_log_send(guild, content=f"تنبيه: تعذر تجهيز رسالة الترحيب في {ticket_channel.mention}. يلزم تدخل إداري.")

        embed = discord.Embed(
            title="تم فتح تذكرة جديدة",
            color=discord.Color.green(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="صاحب التذكرة", value=user.mention, inline=True)
        embed.add_field(name="النوع", value=ticket_type_ar, inline=True)
        embed.add_field(name="القناة", value=ticket_channel.mention, inline=True)
        embed.add_field(name="رابط التذكرة", value=f"[اضغط هنا]({ticket_jump_url(ticket_channel)})", inline=False)
        await safe_log_send(guild, embed=embed)

        logger.info("تم فتح تذكرة جديدة (%s) من النوع %s بواسطة %s", ticket_channel.name, value, user)

        await interaction.edit_original_response(content=f"تم فتح تذكرتك هنا {ticket_channel.mention}", view=None)



class TicketTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())


# =========================================================
# ===================== لوحة فتح التذكرة =====================
# =========================================================



# =========================================================
# ========================= الاوامر =========================
# =========================================================

@bot.tree.command(name="panel", description="ارسال لوحة فتح التذاكر")
@app_commands.checks.has_permissions(administrator=True)
async def panel(interaction: discord.Interaction):
    panel_embed = discord.Embed(color=discord.Color.dark_theme())
    if PANEL_IMAGE_URL:
        panel_embed.set_image(url=PANEL_IMAGE_URL)

    # تظهر قائمة "اختر طلبك" مباشرة تحت الصورة، دون زر وسيط.
    await interaction.channel.send(embed=panel_embed, view=TicketTypeView())
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

    if data.get("type") not in {"inquiry", "complaint", "help", "special"} or not data.get("user"):
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
async def setup_hook():
    init_ticket_ban_db()
    bot.add_view(TicketPanelView())
    bot.add_view(TicketTypeView())
    bot.add_view(TicketActionsView())
    if GUILD_ID:
        guild_obj = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild_obj)
        await bot.tree.sync(guild=guild_obj)
    else:
        await bot.tree.sync()


@bot.event
async def on_ready():
    global ticket_counter
    for guild in bot.guilds:
        ticket_counter = max(ticket_counter, next_ticket_number(guild))
    logger.info("تم تسجيل الدخول كـ %s (معرف: %s)", bot.user, bot.user.id)
    logger.info("عدد السيرفرات المتصلة: %s | عداد التذاكر الحالي: %s", len(bot.guilds), ticket_counter)


if __name__ == "__main__":
    bot.run(BOT_TOKEN)
