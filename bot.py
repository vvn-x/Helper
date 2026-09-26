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
    bot.add_view(TicketTypeView())
    bot.add_view(TicketActionsView())
    if GUILD_ID:
        guild_obj = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild_obj)
        await bot.tree.sync(guild=guild_obj)
    else:
        await bot.tree.sync()
