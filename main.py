import asyncio
import datetime as dt
import json
import pathlib
import random
import re
import time
from typing import Dict, List, Optional

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import BaseMessageComponent, Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .core.api import QzoneAPI
from .core.post import Post, PostManager
from .core.utils import (
    get_image_urls,
    get_reply_message_str,
    parse_qzone_visitors,
)


class ScheduledPostManager:
    """
    AI自动发说说与互动管理器
    - 根据日程自动发说说。
    - 发完说说后，自动与其他人的说说进行互动（点赞/评论）。
    """

    def __init__(self, plugin: "QzonePlugin"):
        """初始化管理器"""
        self.plugin = plugin
        self.context = plugin.context
        self.qzone = plugin.qzone
        self.pm = plugin.pm

        self.bot_instance = None  # 为bot实例准备一个存储位

        self._task: Optional[asyncio.Task] = None
        self._scheduled_times: List[dt.datetime] = []

        plugin_config = self.plugin.config
        self.enabled = plugin_config.get("scheduled_post_enabled", False)
        self.posts_per_day = plugin_config.get("scheduled_post_posts_per_day", 1)
        self.persona_name = plugin_config.get("scheduled_post_persona_name", "")
        self.interaction_enabled = plugin_config.get("scheduled_post_enable_interaction", False)

        self.workflow_state_path = StarTools.get_data_dir("astrbot_plugin_qzone") / "workflow_state.json"

        initiative_path_str = plugin_config.get("scheduled_post_initiative_dialogue_path")
        if not initiative_path_str:
            self.enabled = False
            if plugin_config.get("scheduled_post_enabled", False):
                logger.warning("未配置 initiative_dialogue_path，AI自动发说说功能已禁用。")
            return

        qzone_plugin_dir = pathlib.Path(__file__).parent.resolve()
        self.schedule_file_path = (
                    qzone_plugin_dir / initiative_path_str / "data" / "schedules" / "ai_schedules.json").resolve()

        if self.enabled and not self.schedule_file_path.exists():
            logger.warning(f"在路径 {self.schedule_file_path} 未找到日程文件，AI自动发说说功能可能无法正常工作。")

    async def start(self):
        """启动自动发说说任务"""
        if not self.enabled:
            logger.info("AI自动发说说功能未启用。")
            return
        if self.posts_per_day <= 0:
            logger.info("每日说说数量配置为0或更少，AI自动发说说功能不启动。")
            return

        self._task = asyncio.create_task(self._schedule_loop())
        logger.info(f"AI自动发说说任务已启动，将从 {self.schedule_file_path} 读取日程。")

    async def stop(self):
        """停止自动发说说任务"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("AI自动发说说任务已停止。")

    def _generate_daily_times(self):
        """生成今天内要发送的随机时间点"""
        now = dt.datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + dt.timedelta(days=1)

        self._scheduled_times = []
        for _ in range(self.posts_per_day):
            random_timestamp = random.uniform(start_of_day.timestamp(), end_of_day.timestamp())
            self._scheduled_times.append(dt.datetime.fromtimestamp(random_timestamp))

        self._scheduled_times.sort()

        self._scheduled_times = [t for t in self._scheduled_times if t > now]
        logger.info(f"今日剩余AI自动说说时间点: {[t.strftime('%H:%M:%S') for t in self._scheduled_times]}")

    async def _schedule_loop(self):
        """调度的主要循环任务"""
        try:
            await asyncio.sleep(20)

            while True:
                self._generate_daily_times()
                if not self._scheduled_times:
                    now = dt.datetime.now()
                    tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=5)
                    wait_seconds = (tomorrow - now).total_seconds()
                    logger.info("今日AI自动说说任务已全部完成，等待到明天。")
                    await asyncio.sleep(wait_seconds)
                    continue

                for scheduled_time in self._scheduled_times:
                    now = dt.datetime.now()
                    wait_seconds = (scheduled_time - now).total_seconds()
                    if wait_seconds > 0:
                        logger.info(
                            f"下一次AI自动发说说将在 {scheduled_time.strftime('%Y-%m-%d %H:%M:%S')} 进行, 等待 {wait_seconds:.0f} 秒")
                        await asyncio.sleep(wait_seconds)
                    try:
                        await self._generate_and_publish_post(scheduled_time)
                    except Exception as e:
                        logger.error(f"自动生成并发布说说时出错: {e}")

                now = dt.datetime.now()
                tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=5)
                wait_seconds = (tomorrow - now).total_seconds()
                logger.info("今日AI自动说说任务已全部完成，等待到明天。")
                await asyncio.sleep(wait_seconds)
        except asyncio.CancelledError:
            logger.info("AI自动发说说循环任务被取消。")
            raise
        except Exception as e:
            logger.error(f"AI自动发说说循环任务发生严重错误: {e}")

    def _get_schedule_for_time(self, scheduled_time: dt.datetime) -> Optional[str]:
        """根据给定时间从文件中读取对应的日程活动"""
        try:
            if not self.schedule_file_path.exists():
                logger.warning(f"日程文件不存在: {self.schedule_file_path}")
                return None
            with open(self.schedule_file_path, 'r', encoding='utf-8') as f:
                schedules_data = json.load(f)
            today_str = scheduled_time.date().isoformat()
            if today_str not in schedules_data:
                logger.warning(f"在日程文件中未找到日期为 {today_str} 的安排。")
                return None
            today_schedule = schedules_data[today_str]
            hour = scheduled_time.hour
            period_key = None
            if 6 <= hour < 8:
                period_key = "morning"
            elif 8 <= hour < 11:
                period_key = "forenoon"
            elif 11 <= hour < 13:
                period_key = "lunch"
            elif 13 <= hour < 17:
                period_key = "afternoon"
            elif 17 <= hour < 19:
                period_key = "dinner"
            elif 19 <= hour < 23:
                period_key = "evening"
            else:
                period_key = "night"
            activity = today_schedule.get(period_key)
            if not activity:
                logger.warning(f"在 {today_str} 的日程中未找到 {period_key} 时间段的活动。")
                return None
            logger.info(f"成功获取到日程活动 ({period_key}): {activity}")
            return activity
        except Exception as e:
            logger.error(f"读取或解析日程文件时出错: {e}")
            return None

    async def _generate_and_publish_post(self, scheduled_time: dt.datetime):
        """生成内容并发布一条说说的核心函数"""
        logger.info("开始自动生成说说内容...")

        if not self.bot_instance:
            logger.warning("尚未捕获到 Bot 实例（机器人启动后还没收到任何消息），本次自动任务跳过。")
            return
        bot = self.bot_instance

        current_activity = self._get_schedule_for_time(scheduled_time)
        if not current_activity:
            logger.error("未能获取到当前日程活动，取消本次自动发布。")
            return

        try:
            await self.qzone.login(bot)
            if not self.qzone._auth:
                logger.error("Qzone登录失败。")
                return
            my_uin = self.qzone._auth.uin
        except Exception as e:
            logger.error(f"尝试登录Qzone时出错: {e}")
            return

        system_prompt = self.get_persona_system_prompt()
        if self.persona_name:
            logger.info(f"使用人格 '{self.persona_name}' 的系统提示词生成说说")

        # --- 修改开始 ---
        # 1. 获取最近的一条历史说说
        history_prompt_part = ""
        try:
            latest_post = await self.pm.get_latest_approved_by_uin(uin=my_uin)
            if latest_post:
                history_prompt_part = f"作为参考，这是你上一条发布过的说说，请确保新内容和句式与它不同：\n- \"{latest_post.text}\"\n\n"
                logger.info("已成功获取上一条说说，将嵌入到提示词中。")
        except Exception as e:
            logger.warning(f"获取历史说说失败，将不带历史记录生成: {e}")

        # 2. 构建包含历史记录的新提示词
        time_str = scheduled_time.strftime('%Y年%m月%d日 %H:%M')
        prompt = (
            f"{history_prompt_part}"
            f"现在是 {time_str}，根据你的日程安排，你正在进行 '{current_activity}' 这项活动。\n"
            "请你基于这个人设和当前的活动，用生活化的语气，写一条简短精炼、适合发布在QQ空间的动态/说说，分享你此刻的感受或想法。\n\n"
            "要求：\n"
            "- 直接返回你想要发布的内容，不要包含任何解释性文字、开场白或结尾。\n"
            "- 内容要自然、生动，就像一个真正在分享日常的人。\n"
            "- 请避免与你上一条发布的内容和句式过于相似。\n"
            "- 无法被解析，不要使用&&happy&&这样的标记或特殊语法。说说会给所有人看到，注意隐私。"
        )
        try:
            func_tools_mgr = self.context.get_llm_tool_manager()
            llm_response = await self.context.get_using_provider().text_chat(
                prompt=prompt, session_id=None, contexts=[], image_urls=[], func_tool=func_tools_mgr,
                system_prompt=system_prompt
            )
        except Exception as e:
            logger.error(f"调用LLM时出错: {e}")
            return
        if llm_response.role != "assistant" or not llm_response.completion_text:
            logger.error("LLM未能生成有效的说说内容。")
            return
        post_text = llm_response.completion_text.strip()
        if post_text.startswith(('"', '“')) and post_text.endswith(('"', '”')):
            post_text = post_text[1:-1].strip()
        logger.info(f"AI生成的说说内容: {post_text}")
        post = Post(
            uin=my_uin, name="AIBot", gin=0, text=post_text, images=[], anon=False, status="approved",
            create_time=int(time.time()),
        )
        try:
            tid = await self.qzone.publish_emotion(client=bot, post=post)
            if not tid:
                logger.error("发布说说失败，API未返回tid。")
                return
            post.tid = tid
            post_id = await self.pm.add(post)
            await self.pm.update(post_id, key="tid", value=tid)
            logger.info(f"已自动发布说说#{post_id} (tid: {tid})")

            if self.interaction_enabled:
                logger.info("发说说成功，开始执行互动工作流...")
                asyncio.create_task(self._execute_interaction_workflow(bot=bot))
        except Exception as e:
            logger.error(f"自动发布说说时失败: {e}")
            return

    async def _execute_interaction_workflow(self, bot):
        """执行点赞和评论他人说说的流程"""
        if not bot or not self.qzone._auth:
            logger.error("Bot或Qzone登录状态无效，无法执行互动。")
            return
        my_uin = self.qzone._auth.uin

        last_processed_tid = None
        if self.workflow_state_path.exists():
            try:
                with open(self.workflow_state_path, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    last_processed_tid = state_data.get("last_processed_tid")
            except Exception as e:
                logger.warning(f"读取互动状态文件失败: {e}")

        try:
            # 从 QzonePlugin 实例获取好友列表
            watched_friends = self.plugin._get_watched_friends()
            if not watched_friends:
                logger.info("未配置任何关注好友，互动任务跳过。")
                return

            logger.info(f"开始为 {len(watched_friends)} 位关注好友拉取动态...")

            all_friends_posts: List[Post] = []
            fetch_tasks = []
            for friend_uin in watched_friends:
                # 每个好友拉取最新的3条说说
                fetch_tasks.append(self.qzone.get_emotion(client=bot, num=3, target_uin=friend_uin))

            results = await asyncio.gather(*fetch_tasks)
            for post_list in results:
                all_friends_posts.extend(post_list)

            # 按时间倒序排序所有好友的说说
            all_friends_posts.sort(key=lambda p: p.create_time, reverse=True)

            # 为了后续逻辑兼容，将变量名保持为 recent_posts
            recent_posts = all_friends_posts

        except Exception as e:
            logger.error(f"拉取好友说说列表时发生严重错误: {e}", exc_info=True)
            return

        new_posts_from_others = []
        is_first_run = not last_processed_tid
        one_day_ago = int(time.time()) - 24 * 60 * 60

        for post in recent_posts:
            if post.uin == my_uin:
                continue

            if is_first_run:
                if post.create_time >= one_day_ago:
                    new_posts_from_others.append(post)
            else:
                if post.tid == last_processed_tid:
                    break
                new_posts_from_others.append(post)

        if not new_posts_from_others:
            logger.info("没有发现新的朋友说说，互动结束。")
            return

        logger.info(f"发现了 {len(new_posts_from_others)} 条新的朋友说说。")

        try:
            newest_tid = new_posts_from_others[0].tid
            with open(self.workflow_state_path, 'w', encoding='utf-8') as f:
                json.dump({"last_processed_tid": newest_tid}, f)
        except Exception as e:
            logger.error(f"更新互动状态文件失败: {e}")

        logger.info("开始批量点赞...")
        like_tasks = [self.qzone.like(client=bot, tid=p.tid, target_uin=p.uin) for p in new_posts_from_others]
        like_results = await asyncio.gather(*like_tasks, return_exceptions=True)
        success_likes = sum(1 for r in like_results if isinstance(r, bool) and r)
        logger.info(f"点赞完成，成功 {success_likes}/{len(new_posts_from_others)}。")

        posts_to_comment = new_posts_from_others[:5]
        if not posts_to_comment:
            return

        logger.info(f"准备为 {len(posts_to_comment)} 条说说生成评论。")

        post_list_str = ""
        for i, post in enumerate(posts_to_comment, 1):
            post_list_str += f"{i}. {post.name}: \"{post.text}\"\n"

        comment_prompt = (
            "你正在浏览朋友们的QQ空间动态。下面是几条你刚刚看到的朋友动态，请你扮演好自己的角色，为每一条都写一句简短、自然、友好的评论。\n"
            "动态列表：\n"
            f"{post_list_str}\n"
            "要求：\n"
            "- 你的评论要符合你的人设，语气要生活化。\n"
            "- 以一个JSON对象的格式返回所有评论，不要有任何其他多余的文字。对象的键是动态的序号（字符串格式，例如 '1'），值是对应的评论内容。\n"
            "例如：{\"1\": \"这个看起来太棒了！\", \"2\": \"玩得开心呀！\"}。千万注意，在这里禁止使用emotion，无法被解析，不要使用&&happy&&这样的标记或特殊语法"
        )

        try:
            system_prompt = self.get_persona_system_prompt()
            func_tools_mgr = self.context.get_llm_tool_manager()
            llm_response = await self.context.get_using_provider().text_chat(
                prompt=comment_prompt, session_id=None, contexts=[], image_urls=[], func_tool=func_tools_mgr,
                system_prompt=system_prompt
            )

            if llm_response.role == "assistant" and llm_response.completion_text:
                json_text = llm_response.completion_text
                json_start = json_text.find('{')
                json_end = json_text.rfind('}') + 1
                if json_start != -1 and json_end != -1:
                    clean_json_text = json_text[json_start:json_end]
                    comments_dict = json.loads(clean_json_text)

                    comment_tasks = []
                    for index_str, content in comments_dict.items():
                        try:
                            post_index = int(index_str) - 1
                            if 0 <= post_index < len(posts_to_comment) and content:
                                target_post = posts_to_comment[post_index]
                                logger.info(f"准备评论说说(tid: {target_post.tid}): {content}")
                                comment_tasks.append(
                                    self.qzone.comment(client=bot, tid=target_post.tid, content=str(content),
                                                       target_uin=target_post.uin))
                        except (ValueError, IndexError):
                            logger.warning(f"LLM返回了无效的评论序号: {index_str}")

                    if comment_tasks:
                        await asyncio.gather(*comment_tasks, return_exceptions=True)
                        logger.info("批量评论任务已执行。")
                else:
                    logger.error("LLM返回的内容不包含有效的JSON对象。")
            else:
                logger.error("LLM未能为评论生成有效回复。")

        except json.JSONDecodeError:
            logger.error(f"解析LLM返回的评论JSON失败。原始文本: {llm_response.completion_text}")
        except Exception as e:
            logger.error(f"执行评论流程时发生错误: {e}")

    def get_persona_system_prompt(self) -> str:
        try:
            if not self.persona_name:
                return "你是一个AI助手。"
            personas = self.context.provider_manager.personas
            if not personas:
                return "你是一个AI助手。"
            for persona in personas:
                persona_name = persona["name"] if isinstance(persona, dict) else getattr(persona, "name", None)
                persona_prompt = persona["prompt"] if isinstance(persona, dict) else getattr(persona, "prompt", "")
                if persona_name == self.persona_name:
                    return persona_prompt + "\n请严格按照要求输出，不要添加额外解释。千万注意，在这里禁止使用emotion，无法被解析，不要使用&&happy&&这样的标记或特殊语法。说说会给所有人看到，注意隐私。"
            default_persona = getattr(self.context.provider_manager, "selected_default_persona", None)
            default_persona_name = default_persona.get("name", "") if isinstance(default_persona, dict) else None
            if default_persona_name:
                for persona in personas:
                    persona_name = persona["name"] if isinstance(persona, dict) else getattr(persona, "name", None)
                    persona_prompt = persona["prompt"] if isinstance(persona, dict) else getattr(persona, "prompt", "")
                    if persona_name == default_persona_name:
                        return persona_prompt + "\n请严格按照要求输出，不要添加额外解释。千万注意，在这里禁止使用emotion，无法被解析，不要使用&&happy&&这样的标记或特殊语法。说说会给所有人看到，注意隐私。"
            return "你是一个AI助手。"
        except Exception as e:
            logger.error(f"获取人格系统提示词时出错: {str(e)}")
            return "你是一个AI助手。"


@register(
    "astrbot_plugin_qzone",
    "Zhalslar",
    "QQ空间对接插件",
    "v1.0.5",
)
class QzonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # --- 这是针对 manage_group 的最终修复 ---
        # 1. 从配置中获取 manage_group 的原始字符串值。
        manage_group_str = self.config.get("manage_group")

        self.manage_group: int = int(manage_group_str) if manage_group_str else 0

        # 管理员QQ号列表，审批信息会私发给这些人
        self.admins_id: list[str] = list(set(context.get_config().get("admins_id", [])))
        # 数据库文件
        db_path = StarTools.get_data_dir("astrbot_plugin_qzone") / "posts.db"
        # 数据库管理类
        self.pm = PostManager(db_path)
        # QQ空间API类
        self.qzone = QzoneAPI()
        # AI自动发说说管理器
        self.scheduler = ScheduledPostManager(self)
        self.watched_friends_path = StarTools.get_data_dir("astrbot_plugin_qzone") / "watched_friends.json"

    async def initialize(self):
        await self.pm.init_db()
        await self.scheduler.start()

    def post_to_chain(self, title: str, post: Post) -> list[BaseMessageComponent]:
        status_map = {"pending": "待审核", "approved": "已发布", "rejected": "被拒绝"}
        lines = [f"{title}{status_map.get(post.status, post.status)}"]
        lines += [
            f"时间：{dt.datetime.fromtimestamp(post.create_time).strftime('%Y-%m-%d %H:%M')}",
            f"用户：{post.name}({post.uin})",
        ]
        if post.gin: lines.append(f"群聊：{post.gin}")
        if post.anon: lines.append("匿名：是")
        lines += ["------------------", f"{post.text}"]
        chain: list[BaseMessageComponent] = [Plain("\n".join(lines))]
        for url in post.images:
            chain.append(Image.fromURL(url))
        return chain

    async def notice_admin(self, event: AiocqhttpMessageEvent, chain: list[BaseMessageComponent]):
        client = event.bot
        obmsg = await event._parse_onebot_json(MessageChain(chain))

        async def send_to_admins():
            for admin_id in self.admins_id:
                if admin_id.isdigit():
                    try:
                        await client.send_private_msg(user_id=int(admin_id), message=obmsg)
                    except Exception as e:
                        logger.error(f"无法反馈管理员：{e}")

        if self.manage_group:
            try:
                await client.send_group_msg(group_id=int(self.manage_group), message=obmsg)
            except Exception as e:
                logger.error(f"无法反馈管理群：{e}")
                await send_to_admins()
        elif self.admins_id:
            await send_to_admins()

    async def notice_user(self, event: AiocqhttpMessageEvent, chain: list[BaseMessageComponent], group_id: int = 0,
                          user_id: int = 0):
        client = event.bot
        obmsg = await event._parse_onebot_json(MessageChain(chain))

        async def send_to_user():
            if not user_id: return
            try:
                await client.send_private_msg(user_id=int(user_id), message=obmsg)
            except Exception as e:
                logger.error(f"无法通知投稿者：{e}")

        if group_id:
            try:
                await client.send_group_msg(group_id=int(group_id), message=obmsg)
            except Exception as e:
                logger.error(f"无法通知投稿者的群：{e}")
                await send_to_user()
        elif user_id:
            await send_to_user()

    def extract_post_id(self, event: AiocqhttpMessageEvent) -> int | None:
        content = get_reply_message_str(event)
        if not content: return None
        match = re.search(r"(?:新投稿|说说)#(\d+)", content)
        return int(match.group(1)) if match else None

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发说说")
    async def publish_emotion(self, event: AiocqhttpMessageEvent):
        post = Post(uin=int(event.get_sender_id()), name=event.get_sender_name(), gin=int(event.get_group_id() or 0),
                    text=event.message_str.removeprefix("发说说").strip(), images=await get_image_urls(event),
                    anon=False, status="approved", create_time=int(time.time()), )
        post_id = await self.pm.add(post)
        tid = await self.qzone.publish_emotion(client=event.bot, post=post)
        await self.pm.update(post_id, key="tid", value=tid)
        yield event.plain_result(f"已发布说说#{post_id}")
        logger.info(f"已发布说说#{post_id}, 说说tid: {tid}")

    @filter.command("投稿")
    async def submit(self, event: AiocqhttpMessageEvent):
        post = Post(uin=int(event.get_sender_id()), name=event.get_sender_name(), gin=int(event.get_group_id() or 0),
                    text=event.message_str.removeprefix("投稿").strip(), images=await get_image_urls(event), anon=False,
                    status="pending", create_time=int(time.time()), )
        post_id = await self.pm.add(post)
        yield event.plain_result(f"您的稿件#{post_id}已提交，请耐心等待审核")
        title = f"【新投稿#{post_id}】"
        chain = self.post_to_chain(title, post)
        await self.notice_admin(event, chain)
        event.stop_event()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("通过")
    async def approve(self, event: AiocqhttpMessageEvent):
        post_id = self.extract_post_id(event)
        if not post_id:
            yield event.plain_result("未检测到稿件ID")
            return
        post = await self.pm.get(key="id", value=post_id)
        if not post:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return
        if post.status == "approved":
            yield event.plain_result(f"稿件#{post_id}已经是“已发布”状态，无需重复操作。")
            return
        await self.pm.update(post_id, key="status", value="approved")
        post = await self.pm.get(key="id", value=post_id)
        if not post: return
        tid = await self.qzone.publish_emotion(client=event.bot, post=post)
        await self.pm.update(post_id, key="tid", value=tid)
        yield event.plain_result(f"已发布说说#{post_id}")
        title = f"【您的投稿#{post_id}】"
        await self.notice_user(event, chain=self.post_to_chain(title, post), group_id=post.gin, user_id=post.uin, )
        logger.info(f"已发布说说#{post_id}, 说说tid: {tid}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("不通过")
    async def reject(self, event: AiocqhttpMessageEvent):
        post_id = self.extract_post_id(event)
        if not post_id:
            yield event.plain_result("未检测到稿件ID")
            return
        post = await self.pm.get(key="id", value=post_id)
        if not post:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return
        await self.pm.update(post_id, key="status", value="rejected")
        reason = event.message_str.removeprefix("不通过").strip()
        admin_msg = f"已拒绝稿件#{post_id}"
        if reason: admin_msg += f"\n理由：{reason}"
        yield event.plain_result(admin_msg)
        user_msg = f"您的投稿#{post_id}未通过"
        if reason: user_msg += f"\n理由：{reason}"
        await self.notice_user(event, chain=[Plain(user_msg)], group_id=post.gin, user_id=post.uin, )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看稿件")
    async def check_post(self, event: AiocqhttpMessageEvent, post_id: int = -1):
        post = await self.pm.get(key="id", value=post_id)
        if not post:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return
        title = f"【稿件#{post_id}】"
        chain = self.post_to_chain(title, post)
        yield event.chain_result(chain)

    def _get_watched_friends(self) -> List[int]:
        """读取监控好友列表"""
        if not self.watched_friends_path.exists():
            return []
        try:
            with open(self.watched_friends_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _save_watched_friends(self, friends_list: List[int]):
        """保存监控好友列表"""
        with open(self.watched_friends_path, 'w', encoding='utf-8') as f:
            json.dump(friends_list, f, indent=4)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("关注好友")
    async def add_watched_friend(self, event: AiocqhttpMessageEvent, uin: int):
        """(管理员) 添加一个需要监控互动的好友QQ号"""
        if uin <= 0:
            yield event.plain_result("请输入有效的QQ号。")
            return

        friends = self._get_watched_friends()
        if uin in friends:
            yield event.plain_result(f"QQ号 {uin} 已在关注列表中。")
            return

        friends.append(uin)
        self._save_watched_friends(friends)
        yield event.plain_result(f"成功添加好友 {uin} 到关注列表。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("取关好友")
    async def remove_watched_friend(self, event: AiocqhttpMessageEvent, uin: int):
        """(管理员) 从监控互动列表中移除一个好友QQ号"""
        if uin <= 0:
            yield event.plain_result("请输入有效的QQ号。")
            return

        friends = self._get_watched_friends()
        if uin not in friends:
            yield event.plain_result(f"QQ号 {uin} 不在关注列表中。")
            return

        friends.remove(uin)
        self._save_watched_friends(friends)
        yield event.plain_result(f"成功从关注列表中移除好友 {uin}。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看关注列表")
    async def list_watched_friends(self, event: AiocqhttpMessageEvent):
        """(管理员) 查看当前监控的好友列表"""
        friends = self._get_watched_friends()
        if not friends:
            yield event.plain_result("当前没有关注任何好友。")
            return

        msg = "当前关注的好友列表：\n" + "\n".join(map(str, friends))
        yield event.plain_result(msg)

    @filter.command("查看说说")
    async def emotion(self, event: AiocqhttpMessageEvent, num: int = 1):
        posts = await self.qzone.get_emotion(client=event.bot, num=num)
        if not posts:
            yield event.plain_result("未能获取到说说。")
            return
        post = posts[-1]
        p = await self.pm.get(key="tid", value=post.tid)
        post_id = p.id if p else await self.pm.add(post)
        title = f"【说说#{post_id}】"
        chain = self.post_to_chain(title, post)
        yield event.chain_result(chain)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看访客")
    async def visitor(self, event: AiocqhttpMessageEvent):
        data = (await self.qzone.get_visitor(client=event.bot))["data"]
        msg = parse_qzone_visitors(data)
        yield event.plain_result(msg)

    @filter.command("点赞说说")
    async def like(self, event: AiocqhttpMessageEvent, num: int = 10):
        posts = await self.qzone.get_emotion(client=event.bot, num=num)
        results = await asyncio.gather(*[self.qzone.like(client=event.bot, tid=p.tid, target_uin=p.uin) for p in posts],
                                       return_exceptions=False)
        msg = f"点赞完成，成功 {sum(results)}/{len(posts)} 次"
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("评论说说")
    async def comment(self, event: AiocqhttpMessageEvent, content: str = ""):
        post_id = self.extract_post_id(event)
        if not post_id:
            match = re.search(r"评论说说\s+#?(\d+)\s*(.*)", event.message_str, re.DOTALL)
            if not match:
                yield event.plain_result("请引用要评论的说说，或使用格式：评论说说 #ID 内容")
                return
            post_id = int(match.group(1))
            content = match.group(2).strip()
        if not content:
            yield event.plain_result("评论内容不能为空。")
            return
        post = await self.pm.get(key="id", value=post_id)
        if not post or not post.tid:
            yield event.plain_result(f"稿件/说说#{post_id}不存在或未成功发布，无法评论。")
            return
        res = await self.qzone.comment(client=event.bot, tid=post.tid, content=content, target_uin=post.uin, )
        msg = (f"已评论说说#{post_id}: {content}" if "succ" in str(res) else "评论失败，可能遇到风控或内容不合规。")
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("手动发说说")
    async def manual_publish_and_interact(self, event: AiocqhttpMessageEvent):
        """(管理员) 手动触发一次“发说说->互动”的完整工作流"""

        # 1. 检查 bot 实例是否已被捕获
        if not self.scheduler.bot_instance:
            yield event.plain_result("错误：尚未捕获到 Bot 实例。请先让机器人收到任意一条消息后再试。")
            return

        yield event.plain_result("正在手动触发【发说说并互动】的完整工作流，请稍后...")
        logger.info("由管理员手动触发【发说说并互动】的完整工作流...")

        try:
            # 2. 直接调用自动发说说的核心方法
            #    它内部已经包含了成功后自动触发互动流程的逻辑
            await self.scheduler._generate_and_publish_post(scheduled_time=dt.datetime.now())

            yield event.plain_result("完整工作流执行完毕，请检查后台日志和QQ空间确认效果。")
            logger.info("手动触发的完整工作流执行完毕。")

        except Exception as e:
            logger.error(f"手动触发完整工作流时发生错误: {e}", exc_info=True)
            yield event.plain_result(f"执行出错，请检查后台日志。错误: {e}")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def _capture_bot_instance_from_group(self, event: AiocqhttpMessageEvent):
        """(一次性) 从群聊消息中捕获bot实例，供后台任务使用。"""
        if self.scheduler.bot_instance is None:
            self.scheduler.bot_instance = event.bot
            logger.info(f"Bot 实例已从群聊({event.get_group_id()})中成功捕获，后台任务现在可以正常工作。")


    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def _capture_bot_instance_from_private(self, event: AiocqhttpMessageEvent):
        """(一次性) 从私聊消息中捕获bot实例，供后台任务使用。"""
        if self.scheduler.bot_instance is None:
            self.scheduler.bot_instance = event.bot
            logger.info(f"Bot 实例已从私聊({event.get_sender_id()})中成功捕获，后台任务现在可以正常工作。")

    async def terminate(self):
        await self.scheduler.stop()
        await self.qzone.terminate()
        logger.info("已关闭Qzone API网络连接")