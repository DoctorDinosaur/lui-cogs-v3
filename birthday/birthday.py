"""Birthday cog
Automatically add users to a specified birthday role on their birthday.
"""
import os
import logging
import time # To auto remove birthday role on the next day.
import asyncio
from datetime import datetime, timedelta
from threading import Lock
import discord
from redbot.core import Config, checks, commands
from redbot.core.commands.context import Context
from redbot.core.utils import paginator
from redbot.core.bot import Red

#Global variables
KEY_BDAY_ROLE = "birthdayRole"
KEY_BDAY_USERS = "birthdayUsers"
KEY_BDAY_MONTH = "birthdateMonth"
KEY_BDAY_DAY = "birthdateDay"
KEY_IS_ASSIGNED = "isAssigned"
KEY_DATE_SET_MONTH = "dateAssignedMonth"
KEY_DATE_SET_DAY = "dateAssignedDay"
LOGGER = None
SAVE_FOLDER = "data/lui-cogs/birthday/" #Path to save folder.
SAVE_FILE = "settings.json"

BASE_GUILD_MEMBER = \
{
    KEY_BDAY_DAY: None,
    KEY_BDAY_MONTH: None,
    KEY_DATE_SET_DAY: None,
    KEY_DATE_SET_MONTH: None,
    KEY_IS_ASSIGNED: False
}

BASE_GUILD = \
{
    KEY_BDAY_ROLE: None
}


class Birthday(commands.Cog):
    """Adds a role to someone on their birthday, and automatically remove them from
    this role after the day is over.
    """

    # Class constructor
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5842647, force_registration=True)
        # Register default (empty) settings.
        self.config.register_guild(**BASE_GUILD)
        self.config.register_member(**BASE_GUILD_MEMBER)

        #The JSON keys for the settings:
        self.settingsLock = Lock()

        # On cog load, we want the loop to run once.
        self.lastChecked = datetime.now() - timedelta(days=1)
        self.bgTask = self.bot.loop.create_task(self.birthdayLoop())

    # Cancel the background task on cog unload.
    def __unload(self): # pylint: disable=invalid-name
        self.bgTask.cancel()

    @commands.group(name="birthday")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def _birthday(self, ctx: Context):
        """Birthday role assignment settings"""
        if not ctx.invoked_subcommand:
            await self.bot.send_help_for(ctx, self._birthday)

    @_birthday.command(name="setrole")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def setRole(self, ctx, role: discord.Role):
        """Set the role to assign to a birthday user.
        Make sure this role can be assigned and removed by the bot by placing it in
        the correct hierarchy location.

        Parameters:
        -----------
        role: discord.Role
            A role (name or mention) to set as the birthday role.
        """

        await self.config.guild(ctx.message.guild).birthdayRole.set(role.id)
        await ctx.send(":white_check_mark: **Birthday - Role**: **{}** has been set "
                       "as the birthday role!".format(role.name))

    @_birthday.command(name="add")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def addMember(self, ctx, member: discord.Member):
        """Immediately add a member to the birthday role.

        Parameters:
        -----------
        member: discord.Member
            The guild member that you want to add to the birthday role.
        """
        sid = ctx.message.guild.id

        rid = await self.config.guild(ctx.message.guild).birthdayRole()
        if not rid:
            await ctx.send(":negative_squared_cross_mark: **Birthday - Add**: This "
                           "server is not configured, please set a role!")
            return

        try:
            # Find the Role object to add to the user.
            role = discord.utils.get(ctx.message.guild.roles, id=rid)

            # Add the role to the guild member.
            await member.add_roles(role)
        except discord.Forbidden:
            LOGGER.error("Could not add %s#%s (%s) to birthday role, does the bot "
                         "have enough permissions?",
                         member.name, member.discriminator, member.id, exc_info=True)
            await ctx.send(":negative_squared_cross_mark: **Birthday - Add**: Could "
                           "not add **{}** to the list, the bot does not have enough "
                           "permissions to do so!".format(member.name))
            return

        # Save settings
        async with self.config.member(member).all() as userConfig:
            userConfig[KEY_IS_ASSIGNED] = True
            userConfig[KEY_DATE_SET_MONTH] = int(time.strftime("%m"))
            userConfig[KEY_DATE_SET_DAY] = int(time.strftime("%d"))

        await self.bot.say(":white_check_mark: **Birthday - Add**: Successfully added "
                           "**{}** to the list and assigned the role.".format(user.name))

        LOGGER.info("%s#%s (%s) added %s#%s (%s) to the birthday role.",
                    ctx.message.author.name,
                    ctx.message.author.discriminator,
                    ctx.message.author.id,
                    member.name,
                    member.discriminator,
                    member.id)
        return

    @_birthday.command(name="set")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def setMemberBirthday(self, ctx: Context, month: int,
                                day: int, forMember: discord.Member = None):
        """Set a user's birth date.  Defaults to you.  On the day, the bot will
        automatically add the user to the birthday role.

        Parameters:
        -----------
        month: int
            The birthday month, between 1 and 12 inclusive.

        day: int
            The birthday day, range between 1 and 31 inclusive, depending on month.

        forMember: discord.Member (optional)
            The user this birthday is being assigned to.  If not specified. it
            defaults to you.
        """
        rid = await self.config.guild(ctx.message.guild).birthdayRole()

        # Check if guild is initialized.
        if not rid:
            await ctx.send(":negative_squared_cross_mark: **Birthday - Set**: "
                           "This server is not configured, please set a role!")
            return

        if not forMember:
            forMember = ctx.message.author

        # Check inputs here.
        try:
            userBirthday = datetime(2020, month, day)
        except ValueError:
            await ctx.send(":negative_squared_cross_mark: **Birthday - Set**: "
                           "Please enter a valid birthday!")
            return

        # Save settings
        async with self.config.member(forMember).all() as userConfig:
            userConfig[KEY_BDAY_MONTH] = month
            userConfig[KEY_BDAY_DAY] = day

        confMsg = await ctx.send(":white_check_mark: **Birthday - Set**: Successfully "
                                 "set **{0}**'s birthday to **{1:%B} {1:%d}**. "
                                 "The role will be assigned automatically on this "
                                 "day.".format(forMember.name, userBirthday))

        # Explicitly check to see if user should be added to role, if the month
        # and day just so happen to be the same as it is now.
        await self.checkBirthday()

        await asyncio.sleep(5) # pylint: disable=no-member

        await confMsg.edit(content=":white_check_mark: **Birthday - Set**: Successfully "
                           "set **{0}**'s birthday, and the role will be automatically "
                           "assigned on the day.".format(forMember.name))

        LOGGER.info("%s#%s (%s) set the birthday of %s#%s (%s) to %s",
                    ctx.message.author.name,
                    ctx.message.author.discriminator,
                    ctx.message.author.id,
                    forMember.name,
                    forMember.discriminator,
                    forMember.id,
                    userBirthday.strftime("%B %d"))
        return

    @_birthday.command(name="list", aliases=["ls"])
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def list(self, ctx: Context):
        """Lists the birthdays of users."""
        serverID = ctx.message.server.id
        serverName = ctx.message.server.name
        user = ctx.message.author

        sortedList = [] # List to sort by month, day.
        display = [] # List of text for paginator to use.  Will be constructed from sortedList.

        # Add only the users we care about (e.g. the ones that have birthdays set).
        async for user, items in self.config.guild(ctx.message.guild).all():
            # Check if the birthdate keys exist, and they are not null.
            # If true, add an ID key and append to list.
            if KEY_BDAY_DAY in items.keys() and \
                    KEY_BDAY_MONTH in items.keys() and \
                    items[KEY_BDAY_DAY] and \
                    items[KEY_BDAY_MONTH]:
                items["ID"] = user
                sortedList.append(items)

        # Sort by month, day.
        sortedList.sort(key=lambda x: (x[KEY_BDAY_MONTH], x[KEY_BDAY_DAY]))

        for user in sortedList:
            # Get the associated user Discord object.
            userObject = discord.utils.get(ctx.message.guild.members, id=user["ID"])

            # Skip if user is no longer in server.
            if not userObject:
                continue

            # The year below is just there to accommodate leap year.  Not used anywhere else.
            userBirthday = datetime(2020, user[KEY_BDAY_MONTH], user[KEY_BDAY_DAY])
            text = "{0:%B} {0:%d}: {1}".format(userBirthday, userObject.name)
            display.append(text)

        page = paginator.Pages(ctx=ctx, entries=display,
                               show_entry_count=True)
        page.embed.title = "Birthdays in **{}**".format(serverName)
        page.embed.colour = discord.Colour.red()
        await page.paginate()

    @_birthday.command(name="del", pass_context=True, no_pm=True,
                       aliases=["remove", "delete", "rm"])
    @checks.mod_or_permissions(administrator=True)
    async def _birthdayDel(self, ctx, user: discord.Member):
        """Remove a user from the birthday role manually."""
        sid = ctx.message.server.id
        if sid not in self.settings.keys():
            await self.bot.say(":negative_squared_cross_mark: **Birthday - Delete**: This "
                               "server is not configured, please set a role!")
            return
        if KEY_BDAY_ROLE not in self.settings[sid].keys() or \
                not self.settings[sid][KEY_BDAY_ROLE]:
            await self.bot.say(":negative_squared_cross_mark: **Birthday - Delete**: Please "
                               "set a role before removing a user from the role!")
            return

        if sid not in self.settings.keys() or \
                KEY_BDAY_USERS not in self.settings[sid].keys() or \
                user.id not in self.settings[sid][KEY_BDAY_USERS].keys():
            await self.bot.say(":negative_squared_cross_mark: **Birthday - Delete**: The "
                               "user is not on the list!")
            return


        try:
            # Find the Role object to add to the user.
            role = discord.utils.get(ctx.message.server.roles,
                                     id=self.settings[ctx.message.server.id][KEY_BDAY_ROLE])

            # Add the role to the user.
            await self.bot.remove_roles(user, role)
        except discord.errors.Forbidden as error:
            LOGGER.error("Could not remove %s#%s (%s) from the birthday role, does "
                         "the bot have enough permissions?",
                         user.name, user.discriminator, user.id)
            LOGGER.error(error)
            await self.bot.say(":negative_squared_cross_mark: **Birthday - Delete**: "
                               "Could not remove **{}** from the role, the bot does not "
                               "have enough permissions to do so!".format(user.name))
            return

        self.settingsLock.acquire()
        try:
            self.loadSettings()
            self.settings[sid][KEY_BDAY_USERS][user.id][KEY_IS_ASSIGNED] = False
            self.settings[sid][KEY_BDAY_USERS][user.id][KEY_DATE_SET_MONTH] = None
            self.settings[sid][KEY_BDAY_USERS][user.id][KEY_DATE_SET_DAY] = None

            self.saveSettings()
        except Exception as error: # pylint: disable=broad-except
            LOGGER.error("Could not save settings!")
            LOGGER.error(error)
            await self.bot.say(":negative_squared_cross_mark: **Birthday - Delete**: "
                               "Could not remove **{}** from the list, but the role was "
                               "removed!  Please try again.".format(user.name))
        finally:
            self.settingsLock.release()
        await self.bot.say(":white_check_mark: **Birthday - Delete**: Successfully removed "
                           "**{}** from the list and removed the role.".format(user.name))

        LOGGER.info("%s#%s (%s) removed %s#%s (%s) from the birthday role",
                    ctx.message.author.name,
                    ctx.message.author.discriminator,
                    ctx.message.author.id,
                    user.name,
                    user.discriminator,
                    user.id)
        return

    ########################################
    # Event loop - Try an absolute timeout #
    ########################################
    async def checkBirthday(self, *args): # ignore args pylint: disable=unused-argument
        """Check birthday list once."""
        await self._dailySweep()
        await self._dailyAdd()

    async def birthdayLoop(self):
        """The main event loop that will call the add and sweep methods"""
        while self == self.bot.get_cog("Birthday"):
            if self.lastChecked.day != datetime.now().day:
                self.lastChecked = datetime.now()
                await self.checkBirthday()
            await asyncio.sleep(60) # pylint: disable=no-member

    async def _dailySweep(self):
        self.settingsLock.acquire()
        try: # pylint: disable=too-many-nested-blocks
            # Check each server.
            for sid in self.settings:
                # Check to see if any users need to be removed.
                for userId, userDetails in self.settings[sid][KEY_BDAY_USERS].items():
                    # If assigned and the date is different than the date assigned, remove role.
                    try:
                        if userDetails[KEY_IS_ASSIGNED]:
                            if userDetails[KEY_DATE_SET_MONTH] != int(time.strftime("%m")) or \
                                    userDetails[KEY_DATE_SET_DAY] != int(time.strftime("%d")):
                                serverObject = discord.utils.get(self.bot.servers, id=sid)
                                roleObject = discord.utils.get(serverObject.roles,
                                                               id=self.settings[sid][KEY_BDAY_ROLE])
                                userObject = discord.utils.get(serverObject.members, id=userId)

                                if userObject:
                                    # Remove the role
                                    try:
                                        await self.bot.remove_roles(userObject, roleObject)
                                        LOGGER.info("Removed role from %s#%s (%s)",
                                                    userObject.name,
                                                    userObject.discriminator,
                                                    userObject.id)
                                    except discord.errors.Forbidden as error:
                                        LOGGER.error("Could not remove role from %s#%s (%s)!",
                                                     userObject.name,
                                                     userObject.discriminator,
                                                     userObject.id)
                                        LOGGER.error(error)
                                else:
                                    # Do not remove role, wait until user rejoins, in case
                                    # another cog saves roles.
                                    continue

                                # Update the list.
                                self.settings[sid][KEY_BDAY_USERS][userId][KEY_IS_ASSIGNED] = False
                                self.saveSettings()
                    except KeyError as error:
                        LOGGER.error(error)
                        self.settings[sid][KEY_BDAY_USERS][userId][KEY_IS_ASSIGNED] = False
                        self.saveSettings()
        except Exception as error: # pylint: disable=broad-except
            LOGGER.error("Broad exception: %s", error)
        finally:
            self.settingsLock.release()

    ##################################################################
    # Event Loop - Check to see if we need to add people to the role #
    ##################################################################
    async def _dailyAdd(self): # pylint: disable=too-many-branches
        self.settingsLock.acquire()
        try: # pylint: disable=too-many-nested-blocks
            # Check each server.
            for sid in self.settings:
                # Check to see if any users need to be removed.
                for userId, userDetails in self.settings[sid][KEY_BDAY_USERS].items():
                    # If today is the user's birthday, and the role is not assigned,
                    # assign the role.

                    # Check if the keys for birthdate day and month exist, and that
                    # they're not null.
                    if KEY_BDAY_DAY in userDetails.keys() and \
                            KEY_BDAY_MONTH in userDetails.keys() and \
                            userDetails[KEY_BDAY_DAY] is not None and \
                            userDetails[KEY_BDAY_MONTH] is not None:
                        birthdayDay = userDetails[KEY_BDAY_DAY]
                        birthdayMonth = userDetails[KEY_BDAY_MONTH]

                        if birthdayMonth == int(time.strftime("%m")) and \
                                birthdayDay == int(time.strftime("%d")):
                            # Get the necessary Discord objects.
                            serverObject = discord.utils.get(self.bot.servers,
                                                             id=sid)
                            roleObject = discord.utils.get(serverObject.roles,
                                                           id=self.settings[sid][KEY_BDAY_ROLE])
                            userObject = discord.utils.get(serverObject.members,
                                                           id=userId)

                            # Skip if user is no longer in server.
                            if not userObject:
                                continue

                            try:
                                if not userDetails[KEY_IS_ASSIGNED] and userObject is not None:
                                    try:
                                        await self.bot.add_roles(userObject, roleObject)
                                        LOGGER.info("Added birthday role to %s#%s (%s)",
                                                    userObject.name,
                                                    userObject.discriminator,
                                                    userObject.id)
                                        # Update the list.
                                        userDetails[KEY_IS_ASSIGNED] = True
                                        userDetails[KEY_DATE_SET_MONTH] = int(time.strftime("%m"))
                                        userDetails[KEY_DATE_SET_DAY] = int(time.strftime("%d"))
                                        self.settings[sid][KEY_BDAY_USERS][userId] = userDetails
                                        self.saveSettings()
                                    except discord.errors.Forbidden as error:
                                        LOGGER.error("Could not add role to %s#%s (%s)",
                                                     userObject.name,
                                                     userObject.discriminator,
                                                     userObject.id)
                                        LOGGER.error(error)
                            except Exception: # pylint: disable=broad-except
                                # This key error will happen if the isAssigned key does not exist.
                                if userObject is not None:
                                    try:
                                        await self.bot.add_roles(userObject, roleObject)
                                        LOGGER.info("Added birthday role to %s#%s (%s)",
                                                    userObject.name,
                                                    userObject.discriminator,
                                                    userObject.id)
                                        # Update the list.
                                        userDetails[KEY_IS_ASSIGNED] = True
                                        userDetails[KEY_DATE_SET_MONTH] = int(time.strftime("%m"))
                                        userDetails[KEY_DATE_SET_DAY] = int(time.strftime("%d"))
                                        self.settings[sid][KEY_BDAY_USERS][userId] = userDetails
                                        self.saveSettings()
                                    except discord.errors.Forbidden as error:
                                        LOGGER.error("Could not add role to %s#%s (%s)",
                                                     userObject.name,
                                                     userObject.discriminator,
                                                     userObject.id)
                                        LOGGER.error(error)
                            # End try/except block for isAssigned key.
                        # End if to check if today is the user's birthday.
                    # End if to check for birthdateMonth and birthdateDay keys.
                # End user loop.
            # End server loop.
        except Exception as error: # pylint: disable=broad-except
            LOGGER.error(error)
        finally:
            self.settingsLock.release()

def setup(bot):
    """Add the cog to the bot."""
    global LOGGER # pylint: disable=global-statement
    checkFolder()   #Make sure the data folder exists!
    checkFiles()    #Make sure we have settings!
    customCog = Birthday(bot)
    LOGGER = logging.getLogger("red.Birthday")
    if LOGGER.level == 0:
        # Prevents the LOGGER from being loaded again in case of module reload.
        LOGGER.setLevel(logging.INFO)
        handler = logging.FileHandler(filename=SAVE_FOLDER+"info.log",
                                      encoding="utf-8",
                                      mode="a")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s",
                                               datefmt="[%d/%m/%Y %H:%M:%S]"))
        LOGGER.addHandler(handler)
    bot.add_listener(customCog.checkBirthday, "on_member_join")
    bot.add_cog(customCog)