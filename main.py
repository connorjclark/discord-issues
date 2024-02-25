from dataclasses import dataclass
import dataclasses
from datetime import datetime, timedelta
from discord.ext import commands
from pathlib import Path
from types import SimpleNamespace
from typing import List
import discord
import json
import os
import re
import sys

root_dir = Path(os.path.dirname(os.path.realpath(__file__)))

ZC_GUILD_ID = 876899628556091432
CHANNELS = [
	1021382849603051571,
	1021385902708248637,
	876906434829353071,
	876906577825763328,
	876908472728453161,
	880735841020960818,
	888250124483059713,
	905816367498919937,
	932919361415577661,
]

@dataclass
class Issue:
	thread_id: int
	name: str
	channel_name: str
	author: str
	created_at: datetime
	tags: List[str]
	gh_issue_num: int
	content: str

	def to_serializable_dict(self):
		dic = dataclasses.asdict(self)
		dic['created_at'] = str(self.created_at)
		return dic


def load_state():
	return json.loads((root_dir/'state.json').read_text('utf-8'))


def save_state():
	return (root_dir/'state.json').write_text(json.dumps(state, indent=2))


state = load_state()
thread_ids_to_gh_num = state['thread_ids_to_gh_num']
next_gh_num = 912
for x in thread_ids_to_gh_num.values():
	next_gh_num = max(next_gh_num, x)

def get_gh_num(thread_id: int):
	global next_gh_num

	key = str(thread_id)
	if key in thread_ids_to_gh_num:
		return thread_ids_to_gh_num[key]

	gh_num = next_gh_num
	thread_ids_to_gh_num[key] = gh_num
	next_gh_num += 1
	return gh_num


def create_bot():
	intents = discord.Intents.default()
	intents.message_content = True
	intents.members = True
	return commands.Bot('.', intents=intents)

bot = create_bot()

def trim_string(s: str, limit: int, ellipsis='…') -> str:
	s = s.strip()
	if len(s) > limit:
		return s[:limit-1].strip() + ellipsis
	return s


async def process_thread(guild: discord.Guild, channel: discord.ForumChannel, thread: discord.Thread):
	print(f'{thread.id} {thread.created_at} {thread.name}', file=sys.stderr)
	
	lines = []
	messages = [x async for x in thread.history(limit=None)]
	messages.reverse()
	
	previous_message = None
	for message in messages:
		if previous_message == None or previous_message.author.id != message.author.id or message.created_at - previous_message.created_at > timedelta(minutes=1):
			gh_username = get_author_github_name(message.author)
			timestamp = message.created_at.strftime("%m/%d/%Y %H:%M")
			lines.append(f'\n=== {gh_username} {timestamp}\n')
		previous_message = message

		if message.type == discord.MessageType.default:
			lines.append(message.content)
		elif message.type == discord.MessageType.thread_starter_message:
			starter = await channel.fetch_message(message.reference.message_id)
			lines.append(starter.content)
			# I think this is always blank, but just in case it isn't...
			if message.content:
				lines.append(message.content)
		elif message.type == discord.MessageType.reply:
			try:
				replying_to = await thread.fetch_message(message.reference.message_id)    
				replying_prefix = f'(replying to {get_author_github_name(replying_to.author)} "{trim_string(replying_to.content, 30)}"):'
				lines.append(f'{replying_prefix} {message.content}')
			except:
				replying_prefix = f'(replying to deleted comment):'
				lines.append(f'{replying_prefix} {message.content}')
		elif message.type == discord.MessageType.channel_name_change:
			lines.append(f'(meta) thread name was changed: {message.content}')
		else:
			lines.append(f'(meta, {message.type}) {message.content}')

		for attachment in message.attachments:
			if attachment.width != None:
				lines.append(f'![image]({attachment.url})')
			else:
				lines.append(attachment.url)

	return Issue(
		thread_id=thread.id,
		name=thread.name,
		channel_name=channel.name,
		author=get_author_github_name(messages[0].author),
		created_at=thread.created_at if thread.created_at else messages[0].created_at,
		tags=[tag.name for tag in thread.applied_tags],
		gh_issue_num=get_gh_num(thread.id),
		content='\n'.join(lines),
	)


async def get_all_threads(channel: discord.ForumChannel):
	threads = []
	threads.extend(channel.threads)
	async for thread in channel.archived_threads(limit=None):
		threads.append(thread)
	return threads


async def process_channel(bot: commands.Bot, id: int):
	guild = bot.get_guild(ZC_GUILD_ID)
	channel = guild.get_channel(id)

	issues: List[Issue] = []
	for thread in await get_all_threads(channel):
		issues.append(await process_thread(guild, channel, thread))
		# TODO
		# if len(issues) > 2:
		# 	break
	issues = sorted(issues, key=lambda issue: issue.thread_id)

	return issues


async def assign_gh_nums():
	threads = []
	for channel_id in CHANNELS:
		guild = bot.get_guild(ZC_GUILD_ID)
		channel = guild.get_channel(channel_id)
		for thread in await get_all_threads(channel):
			threads.append(thread)
	threads = sorted(threads, key=lambda thread: thread.id)
	for thread in threads:
		get_gh_num(thread.id)


@bot.event
async def on_ready():
	# await assign_gh_nums()
	# save_state()
	# sys.exit(0)

	issues: List[Issue] = []
	for channel_id in CHANNELS:
		for thread in await process_channel(bot, channel_id):
			issues.append(thread)

	guild = bot.get_guild(ZC_GUILD_ID)
	issues = sorted(issues, key=lambda issues: issues.gh_issue_num)
	save_state()

	print(f'found {len(issues)} issue threads\n\n')
	(root_dir / 'dump.json').write_text(json.dumps([i.to_serializable_dict() for i in issues], indent=2))

	converted_threads = []
	for issue in issues:
		print(issue.thread_id)

		def replace_id_tags(m):
			if m.group(1) == '@' or m.group(1) == '@!':
				user_id = int(m.group(2))
				user = bot.get_user(user_id)
				if user == None:
					return f'@ DeletedUser'
				return get_author_github_name(user)
			elif m.group(1) == '#':
				channel_id = m.group(2)
				if channel_id in thread_ids_to_gh_num:
					return f'#{thread_ids_to_gh_num[channel_id]}'

				# Not a link to a bug thread, so let's link to the discord channel.
				the_channel = bot.get_channel(channel_id)
				if the_channel:
					return f'[#{the_channel.name}](https://discord.com/channels/{the_channel.guild.id}/{the_channel.id})'
				else:
					return '#deleted-channel'
			elif m.group(1) == '@&':
				role_id = int(m.group(2))
				role = guild.get_role(role_id)
				return f'@<role: {role.name}>'
			else:
				return m.group(0)

		try:
			chat_log = re.sub(r'<(@|#|@!|@&)(\d+)>', replace_id_tags, issue.content).strip()

			status = 'unknown'
			if '🔒' in issue.name:
				if '✅' in issue.name:
					status = 'fixed'
				elif '❌' in issue.name:
					status = 'wont-fix'
				else:
					status = 'closed'
			elif '🔓' in issue.name:
				if '💊' in issue.name:
					status = 'needs-testing'
				else:
					status = 'open'
			else:
				# Often the locks aren't present...
				if '✅' in issue.name:
					status = 'fixed'
				elif '❌' in issue.name:
					status = 'wont-fix'
				elif '💊' in issue.name:
					status = 'needs-testing'
			
			# For threads that are too toxic to make public.
			threads_to_elide_chat_log = [
				877733602668970015,
			]
			if issue.thread_id in threads_to_elide_chat_log:
				chat_log = f'chat log not migrated. to see contents, go to: https://discord.com/channels/{ZC_GUILD_ID}/{issue.thread_id}'

			converted_threads.append(SimpleNamespace(
				discord_thread_id=issue.thread_id,
				title=thread.name,
				created_at=issue.created_at,
				gh_id=issue.gh_issue_num,
				gh_author=issue.author,
				chat_log=chat_log,
				status=status,
				issue=issue,
			))
		except Exception as e:
			print(e)

	for thread in converted_threads:
		timestamp = thread.created_at.strftime("%m/%d/%Y")
		with open(f'issues/{thread.gh_id}.md', 'w') as f:
			print(f'## {thread.title} (#{thread.gh_id})', file=f)
			print(f'{thread.gh_author} opened this issue on {timestamp}', file=f)
			print(f'Status: {thread.status}', file=f)
			print(f'Tags: {",".join(thread.issue.tags)}', file=f)
			print(f'Source: #{thread.issue.channel_name} https://discord.com/channels/{ZC_GUILD_ID}/{thread.discord_thread_id}', file=f)
			print('\n', file=f)
			print(thread.chat_log, file=f)

	await bot.close()

# Return a fake handle like `@ FakeAccount` if there is not a known Github account.
def get_author_github_name(author):
	mapping = {
		121155061551202304: '', # Orithan
		121184671777161216: 'TheBlueTophat', # Coolgamer012345
		122462879923437570: '', # Lejes
		127232294145622016: 'arceusplayer11', # Deedee
		152983098500448257: '', # Ether
		187300000135512064: '', # Moosh
		188418153741549568: '', # Changeling
		200811601522196480: '', # FireSeraphim
		206596786331058176: '', # jman2050
		226163329352204288: 'connorjclark', # connorclark
		226885367767236608: '', # InfinityD4
		237743531375067138: '', # naturesucks
		242422436262313986: 'EmilyV99', # EmilyV
		250486281988079618: '', # NightmareJames
		259076371584647171: '', # Tabletpillow
		278342629643517952: '', # Russ
		347808146456313858: '', # Deathrider
		387478496924008448: '', # HeroOfFireZC
		397733275906342915: '', # tacochopper
		424317852947054595: '', # xenomicx
		458089188563353603: '', # P-Tux7
		470443062909468672: '', # Majora
		492893092605853697: '', # cbailey78
		505116196828348417: '', # Bagel Meister
		546824565985247253: '', # SkyLizardGirl
		638889207766712330: '', # bigjoe
		701625420277350412: '', # Kirbsblue
		738890034870222898: '', # vlamart
		740387675990786058: '', # carnch
		82603692557078528: '', # Lunaria
		914153766620626945: '', # Lost Attempt
		988168758369607710: '', # C1
		877305063679352917: '', # Saffith
		208727646530437120: '', # 4matsy
		716343681317077002: '', # Alucard648
		277877023966494721: '', # Anthus
		121027900123119616: '', # Einsiety
		160816622767046658: '', # Jared
		896784355375058944: '', # TwilightKnight
		715280883980173364: '', # Guinevere
		225341954018246656: '', # Mitsukara
		126960520812036096: '', # Rambly
		316187823403171840: 'ZoriaRPG', # Timelord
		307150586145669120: '', # Architect Abdiel
		798282693896700004: '', # Bagu
		342451330238906391: '', # Lincolnpepper
		172116882671796225: '', # mitchfork
		275474251111464961: '', # OmegaX
		409803372288409610: '', # bleugriffon2
		154294750248173569: '', # DarkMatt
		852311758982348811: '', # Kampy
		233553576620851200: '', # likelike on fire
		121281283958505472: '', # Rekiron
		116958200351293446: '', # Shane
		249795502864990210: '', # Zaidyer
		456226577798135808: '', # Deleted User
		120939230393401347: '', # Runa 💜🌸
		167018008596840448: '', # aslion
		153057871485992960: '', # Zeo
		180412467673825280: '', # Matthew
		799102708119896094: '', # Chadward Chaddington the 143rd
	}

	if author.id not in mapping:
		print(f'unknown author: {author.name} {author.id}', file=sys.stderr)
	elif mapping[author.id]:
		# Some names are very similar across github/discord. For ones that are not,
		# also show the discord username.
		# if mapping[author.id] in ['connorjclark', 'EmilyV99']:
		#     return f'@{mapping[author.id]}'
		# else:
		return f'@{mapping[author.id]} (discord: {author.display_name})'

	return f'@ {author.display_name}'

bot.run(sys.argv[1])
