import dataclasses
import json
import os
import re
import sys

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Union

import discord

from discord.ext import commands

root_dir = Path(os.path.dirname(os.path.realpath(__file__)))

ZC_GUILD_ID = 876899628556091432
CHANNELS_TO_SUMMARIZE = {
    # Top bugs.
    1021382849603051571: 1286523088900591699,
    # Top feature requests.
    1021385902708248637: 1286512335829336146,
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

    def has_tag(self, name: str):
        return next((t for t in self.tags if t.name == name), None) != None


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

    return reaction.emoji.name in ['this', 'heart', 'thumbsup']


async def get_issues_from_channel(
    bot: commands.Bot, channel: discord.ForumChannel, summary_thread_id: int
):
    issues: List[Issue] = []
    for thread in await get_all_threads(channel):
        if thread.id == summary_thread_id or thread.parent_id == summary_thread_id:
            continue
        if thread.name == 'Top Bug Reports' or thread.name == 'Top Feature Requests':
            continue

        closed_tag_names = [
            'Already Exists',
            'Closed',
            'Denied',
            'Fixed',
            'Stale',
        ]
        is_open = next((t for t in thread.applied_tags if t.name == 'Open'), None)
        is_closed = next(
            (t for t in thread.applied_tags if t.name in closed_tag_names), None
        )
        dev_disc = next(
            (t for t in thread.applied_tags if t.name == 'DevDiscussion'), None
        )

        if dev_disc and not is_open:
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
        # if len(issues) > 25:
        #     break

    return issues


def split_message_content(content: str):
    # https://stackoverflow.com/a/72943629/2788187
    start_idx = 0
    length = 1999
    end_idx = 0
    chunks = []
    while end_idx < len(content):
        end_idx = content.rfind("\n", start_idx, length + start_idx) + 1
        chunks.append(content[start_idx:end_idx])
        start_idx = end_idx
    return chunks


def create_section(label: str, issues: List[Issue], this_emoji) -> str:
    content = f'# {label} ({len(issues)})\n'
    for issue in issues:
        content += f'`{str(issue.votes).rjust(2, " ")}` {this_emoji} [{issue.name}]({issue.url}) {issue.get_tag_str()}\n'
    if not issues:
        return 'None\n'
    return content


async def process_channel(bot: commands.Bot, channel_id: int, summary_thread_id: int):
    guild = bot.get_guild(ZC_GUILD_ID)
    channel = guild.get_channel(channel_id)
    # await channel.create_thread(name='Top Bug Reports', content='Top Bug Reports')
    # sys.exit(1)
    summary_thread = channel.get_thread(summary_thread_id)
    this_emoji = guild.get_emoji(877358416992030731)

    print('collecting issues')

    issues: List[Issue] = []
    for thread in await get_issues_from_channel(bot, channel, summary_thread):
        issues.append(thread)
    issues = sorted(issues, key=lambda issue: -issue.votes)

    print('done collecting issues')

    open_issues = []
    pending_issues = []
    unknown_issues = []
    highprio_issues = []
    lowprio_issues = []
    for issue in issues:
        if issue.status == 'pending':
            pending_issues.append(issue)
        elif issue.status == 'unknown':
            unknown_issues.append(issue)
        elif issue.status == 'open':
            if issue.has_tag('High Priority'):
                highprio_issues.append(issue)
            elif issue.has_tag('Low Priority'):
                lowprio_issues.append(issue)
            else:
                open_issues.append(issue)
                issue.tags = [
                    t for t in issue.tags if t.name != 'Open' and t.name != 'Unassigned'
                ]

    content = ''

    if pending_issues:
        content += create_section('Pending', pending_issues, this_emoji)
    if highprio_issues:
        content += create_section('Open - High Priority', highprio_issues, this_emoji)
    content += create_section('Open', open_issues, this_emoji)
    if lowprio_issues:
        content += create_section('Open - Low Priority', lowprio_issues, this_emoji)
    if unknown_issues:
        content += create_section('Unknown', unknown_issues, this_emoji)

    # TODO
    # content += f'# Fixed in the last month ({len(pending_issues)})\n'

    print(content)
    # sys.exit(1)

    chunks = split_message_content(content)
    print(f'update content: {len(chunks)} messages needed')

    existing_messages = [
        x
        async for x in summary_thread.history(oldest_first=True, limit=None)
        if not x.is_system()
    ]
    first_message = existing_messages[0]
    existing_messages = existing_messages[1:]

    # Legend.
    content = ''
    for i, tag in enumerate(channel.available_tags):
        content += f'{tag.emoji}  {tag.name}\n'
    await first_message.edit(content=content)

    for i, chunk in enumerate(chunks):
        if i >= len(existing_messages):
            await summary_thread.send(content=chunk)
        else:
            await existing_messages[i].edit(content=chunk)
    for m in existing_messages[len(chunks) :]:
        await m.delete()

    print(f'done updating content')


@bot.event
async def on_ready():
    for channel_id, summary_thread_id in CHANNELS_TO_SUMMARIZE.items():
        print(f'processing channel {channel_id}')
        await process_channel(bot, channel_id, summary_thread_id)

    print('done')
    await bot.close()


bot.run(sys.argv[1])
