import dataclasses
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import List, Union

import discord
from discord.ext import commands

root_dir = Path(os.path.dirname(os.path.realpath(__file__)))

ZC_GUILD_ID = 876899628556091432
CHANNEL = 1021382849603051571
SUMMARY_THREAD_ID = 1286204242951934016

CHANNELS_TO_SUMMARIZE = {
    # Top issues.
    1021382849603051571: 1286204242951934016,
}


@dataclass
class Issue:
    id: int
    name: str
    status: Union['open', 'closed', 'pending', 'unknown']
    url: str
    votes: int
    tags: List[discord.channel.ForumTag]

    def get_tag_str(self):
        return ' '.join(
            str(t.emoji) for t in self.tags if t.emoji and not isinstance(t.emoji, str)
        )


def create_bot():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    return commands.Bot('.', intents=intents)


bot = create_bot()


async def get_all_threads(channel: discord.ForumChannel):
    threads = []
    threads.extend(channel.threads)
    async for thread in channel.archived_threads(limit=None):
        threads.append(thread)
    return threads


def is_upvote_reaction(reaction: discord.Reaction):
    if isinstance(reaction.emoji, str):
        return False

    return reaction.emoji.name == 'this'


async def process_channel(bot: commands.Bot, id: int):
    guild = bot.get_guild(ZC_GUILD_ID)
    channel = guild.get_channel(id)

    issues: List[Issue] = []
    for thread in await get_all_threads(channel):
        if thread.id == SUMMARY_THREAD_ID:
            continue

        is_open = next((t for t in thread.applied_tags if t.name == 'Open'), None)
        is_closed = next((t for t in thread.applied_tags if t.name == 'Closed'), None)
        dev_disc = next(
            (t for t in thread.applied_tags if t.name == 'DevDiscussion'), None
        )

        if dev_disc:
            continue

        status = 'unknown'
        if is_closed and not is_open:
            status = 'closed'
        elif is_open and not is_closed:
            status = 'open'
        elif not is_open and not is_closed:
            status = 'pending'

        message = [x async for x in thread.history(oldest_first=True, limit=1)][0]
        this_reaction = next(
            (r for r in message.reactions if is_upvote_reaction(r)), None
        )
        votes = this_reaction.count if this_reaction else 0

        issues.append(
            Issue(
                id=thread.id,
                name=thread.name,
                status=status,
                url=thread.jump_url,
                votes=votes,
                tags=thread.applied_tags,
            )
        )

    return issues


async def send_and_split(messagable: discord.threads.Messageable, content: str):
    # https://stackoverflow.com/a/72943629/2788187
    start_idx = 0
    length = 1999
    end_idx = 0
    while end_idx < len(content):
        end_idx = content.rfind("\n", start_idx, length + start_idx) + 1
        await messagable.send(content=content[start_idx:end_idx])
        start_idx = end_idx


@bot.event
async def on_ready():
    guild = bot.get_guild(ZC_GUILD_ID)
    this_emoji = guild.get_emoji(877358416992030731)

    # channel = guild.get_channel(CHANNEL)
    # thread = channel.get_thread(SUMMARY_THREAD_ID)
    # await channel.create_thread(name='Top Issues', content='Top Issues')
    # sys.exit(1)

    issues: List[Issue] = []
    for thread in await process_channel(bot, CHANNEL):
        issues.append(thread)
    issues = sorted(issues, key=lambda issue: -issue.votes)

    open_issues = []
    pending_issues = []
    unknown_issues = []
    for issue in issues:
        if issue.status == 'open':
            open_issues.append(issue)
        elif issue.status == 'pending':
            pending_issues.append(issue)
        elif issue.status == 'unknown':
            unknown_issues.append(issue)

    content = ''

    content += f'# Open ({len(open_issues)})\n'
    for issue in open_issues:
        issue.tags = [t for t in issue.tags if t.name != 'Open']
        content += f'`{str(issue.votes).rjust(2, " ")}` {this_emoji} | [{issue.name}]({issue.url}) {issue.get_tag_str()}\n'
    if not open_issues:
        content += 'None\n'

    content += '\n# Pending\n'
    for issue in pending_issues:
        content += f'`{str(issue.votes).rjust(2, " ")}` {this_emoji} | [{issue.name}]({issue.url}) {issue.get_tag_str()}\n'
    if not pending_issues:
        content += 'None\n'

    if unknown_issues:
        content += '\n# ???\n'
        for issue in unknown_issues:
            content += f'`{str(issue.votes).rjust(2, " ")}` {this_emoji} | [{issue.name}]({issue.url}) {issue.get_tag_str()}\n'

    channel = guild.get_channel(CHANNEL)
    thread = channel.get_thread(SUMMARY_THREAD_ID)

    # TODO: edit in-place.
    messages = [x async for x in thread.history(oldest_first=True, limit=None)][1:]
    for m in messages:
        await m.delete()
    await send_and_split(thread, content)

    await bot.close()


bot.run(sys.argv[1])
