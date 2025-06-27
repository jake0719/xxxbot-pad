import asyncio
import aiohttp
import json
import os
import sys
import time
import tomllib
from pathlib import Path
from datetime import datetime, timedelta

from loguru import logger

import WechatAPI
from database.XYBotDB import XYBotDB
from database.keyvalDB import KeyvalDB
from database.messsagDB import MessageDB
from utils.decorators import scheduler
from utils.plugin_manager import plugin_manager
from utils.xybot import XYBot
from utils.notification_service import init_notification_service, get_notification_service

# 导入管理后台模块
try:
    # 正确设置导入路径
    admin_path = str(Path(__file__).resolve().parent)
    if admin_path not in sys.path:
        sys.path.append(admin_path)

    # 导入管理后台服务器模块
    try:
        from admin.server import set_bot_instance as admin_set_bot_instance
        logger.debug("成功导入admin.server.set_bot_instance")
    except ImportError as e:
        logger.error(f"导入admin.server.set_bot_instance失败: {e}")
        # 创建一个空函数
        def admin_set_bot_instance(bot):
            logger.warning("admin.server.set_bot_instance未导入，调用被忽略")
            return None

    # 直接定义状态更新函数，不依赖导入
    def update_bot_status(status, details=None, extra_data=None):
        """更新bot状态，供管理后台读取"""
        try:
            # 使用统一的路径写入状态文件 - 修复路径问题
            status_file = Path(admin_path) / "admin" / "bot_status.json"
            root_status_file = Path(admin_path) / "bot_status.json"

            # 读取当前状态
            current_status = {}
            if status_file.exists():
                with open(status_file, "r", encoding="utf-8") as f:
                    current_status = json.load(f)

            # 更新状态
            current_status["status"] = status
            current_status["timestamp"] = time.time()
            if details:
                current_status["details"] = details

            # 添加额外数据
            if extra_data and isinstance(extra_data, dict):
                for key, value in extra_data.items():
                    current_status[key] = value

            # 确保目录存在
            status_file.parent.mkdir(parents=True, exist_ok=True)

            # 写入status_file
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump(current_status, f)

            # 写入root_status_file
            with open(root_status_file, "w", encoding="utf-8") as f:
                json.dump(current_status, f)

            logger.info(f"成功更新bot状态: {status}, 路径: {status_file} 和 {root_status_file}")

            # 输出更多调试信息
            if "nickname" in current_status:
                logger.info(f"状态文件包含昵称: {current_status['nickname']}")
            if "wxid" in current_status:
                logger.info(f"状态文件包含微信ID: {current_status['wxid']}")
            if "alias" in current_status:
                logger.info(f"状态文件包含微信号: {current_status['alias']}")

        except Exception as e:
            logger.error(f"更新bot状态失败: {e}")

    # 定义设置bot实例的函数
    def set_bot_instance(bot):
        """设置bot实例到管理后台"""
        # 先调用admin模块的设置函数
        admin_set_bot_instance(bot)

        # 更新状态
        update_bot_status("initialized", "机器人实例已设置")
        logger.success("成功设置bot实例并更新状态")

        return bot

except ImportError as e:
    logger.error(f"导入管理后台模块失败: {e}")
    # 创建空函数，防止程序崩溃
    def set_bot_instance(bot):
        logger.warning("管理后台模块未正确导入，set_bot_instance调用被忽略")
        return None

    # 创建一个空的状态更新函数
    def update_bot_status(status, details=None):
        logger.debug(f"管理后台模块未正确导入，状态更新被忽略: {status}")


async def bot_core():
    # 设置工作目录
    script_dir = Path(__file__).resolve().parent
    os.chdir(script_dir)

    # 更新初始化状态
    update_bot_status("initializing", "系统初始化中")

    # 读取配置文件
    config_path = script_dir / "main_config.toml"
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
        logger.success("读取主设置成功")
    except Exception as e:
        logger.error(f"读取主设置失败: {e}")
        return

    # 启动WechatAPI服务
    # server = WechatAPI.WechatAPIServer()
    api_config = config.get("WechatAPIServer", {})
    api_host = api_config.get("host", "127.0.0.1")  # 获取自定义的API主机地址
    redis_host = api_config.get("redis-host", "127.0.0.1")
    redis_port = api_config.get("redis-port", 6379)
    logger.info("WechatAPI 服务器地址: {}", api_host)
    logger.info("Redis 主机地址: {}:{}", redis_host, redis_port)
    # server.start(port=api_config.get("port", 9000),
    #              mode=api_config.get("mode", "release"),
    #              redis_host=redis_host,
    #              redis_port=redis_port,
    #              redis_password=api_config.get("redis-password", ""),
    #              redis_db=api_config.get("redis-db", 0))

    # 读取协议版本设置
    protocol_version = config.get("Protocol", {}).get("version", "849")
    logger.info(f"使用协议版本: {protocol_version}")

    # 实例化WechatAPI客户端
    if protocol_version == "855":
        # 855版本使用Client2
        try:
            # 尝试导入Client2
            import sys
            import importlib.util
            client2_path = Path(__file__).resolve().parent / "WechatAPI" / "Client2"
            if str(client2_path) not in sys.path:
                sys.path.append(str(client2_path))

            # 检查Client2是否存在
            if (client2_path / "__init__.py").exists():
                logger.info("WechatAPI Client2目录存在，使用855协议客户端")
                # 尝试导入客户端2
                # 使用直接导入的方式
                from WechatAPI.Client2 import WechatAPIClient as WechatAPIClient2

                # 使用Client2
                bot = WechatAPIClient2(api_host, api_config.get("port", 9011))
                logger.success("成功加载855协议客户端")
            else:
                logger.warning("WechatAPI Client2目录不存在，回退使用默认客户端")
                bot = WechatAPI.WechatAPIClient(api_host, api_config.get("port", 9000))
        except Exception as e:
            logger.error(f"加载855协议客户端失败: {e}")
            logger.warning("回退使用默认客户端")
            bot = WechatAPI.WechatAPIClient(api_host, api_config.get("port", 9000))
    elif protocol_version == "ipad" or protocol_version == "Mac":
        # iPad/Mac版本使用Client3
        try:
            # 尝试导入Client3
            import sys
            import importlib.util
            client3_path = Path(__file__).resolve().parent / "WechatAPI" / "Client3"
            if str(client3_path) not in sys.path:
                sys.path.append(str(client3_path))

            # 检查Client3是否存在
            if (client3_path / "__init__.py").exists():
                logger.info(f"WechatAPI Client3目录存在，使用{protocol_version}协议客户端")
                # 尝试导入客户端3
                # 使用直接导入的方式
                from WechatAPI.Client3 import WechatAPIClient as WechatAPIClient3

                # 使用Client3
                bot = WechatAPIClient3(api_host, api_config.get("port", 9011))
                logger.success(f"成功加载{protocol_version}协议客户端")
            else:
                logger.warning("WechatAPI Client3目录不存在，回退使用默认客户端")
                bot = WechatAPI.WechatAPIClient(api_host, api_config.get("port", 9011))
        except Exception as e:
            logger.error(f"加载{protocol_version}协议客户端失败: {e}")
            logger.warning("回退使用默认客户端")
            bot = WechatAPI.WechatAPIClient(api_host, api_config.get("port", 9011))
    else:
        # 849版本使用默认Client
        bot = WechatAPI.WechatAPIClient(api_host, api_config.get("port", 9000))
        logger.info("使用849协议客户端")

    # 设置客户端属性
    bot.ignore_protect = config.get("XYBot", {}).get("ignore-protection", False)

    # 等待WechatAPI服务启动
    # time_out = 30  # 增加超时时间
    # while not await bot.is_running() and time_out > 0:
    #     logger.info("等待WechatAPI启动中")
    #     await asyncio.sleep(2)
    #     time_out -= 2

    # if time_out <= 0:
    #     logger.error("WechatAPI服务启动超时")
    #     # 更新状态
    #     update_bot_status("error", "WechatAPI服务启动超时")
    #     return None

    # if not await bot.check_database():
    #     logger.error("Redis连接失败，请检查Redis是否在运行中，Redis的配置")
    #     # 更新状态
    #     update_bot_status("error", "Redis连接失败")
    #     return None

    logger.success("WechatAPI服务已启动")

    # 更新状态
    update_bot_status("waiting_login", "等待微信登录")

    # 检查并创建robot_stat.json文件
    robot_stat_path = script_dir / "resource" / "robot_stat.json"
    if not os.path.exists(robot_stat_path):
        default_config = {
            "wxid": "",
            "device_name": "",
            "device_id": ""
        }
        os.makedirs(os.path.dirname(robot_stat_path), exist_ok=True)
        with open(robot_stat_path, "w") as f:
            json.dump(default_config, f)
        robot_stat = default_config
    else:
        with open(robot_stat_path, "r") as f:
            robot_stat = json.load(f)

    wxid = robot_stat.get("wxid", None)
    device_name = robot_stat.get("device_name", None)
    device_id = robot_stat.get("device_id", None)

    if not await bot.is_logged_in(wxid):
        while not await bot.is_logged_in(wxid):
            # 需要登录
            try:
                get_cached_info = await bot.get_cached_info(wxid)
                # logger.info("获取缓存登录信息:{}",get_cached_info)
                if get_cached_info:
                    #二次登录
                    twice = await bot.twice_login(wxid)
                    logger.info("二次登录:{}",twice)
                    if not twice:
                        logger.error("二次登录失败，请检查微信是否在运行中，或重新启动机器人")
                        # 尝试唤醒登录
                        logger.info("尝试唤醒登录...")
                        try:
                            # 准备唤醒登录
                            # 注意：awaken_login 方法只接受 wxid 参数
                            # 实际的 API 调用会将其作为 JSON 请求体中的 Wxid 字段发送

                            # 直接使用 aiohttp 调用 API，而不是使用 awaken_login 方法
                            # 这样我们可以更好地控制错误处理
                            async with aiohttp.ClientSession() as session:
                                # 根据协议版本选择不同的 API 路径
                                if protocol_version == "855":
                                    api_base = "/api"
                                elif protocol_version == "ipad" or protocol_version == "Mac":
                                    api_base = "/api"
                                else:
                                    api_base = "/VXAPI"
                                api_url = f'http://{api_host}:{api_config.get("port", 9011)}{api_base}/Login/Awaken'

                                # 准备请求参数
                                json_param = {
                                    "OS": device_name if device_name else "iPad",
                                    "Proxy": {
                                        "ProxyIp": "",
                                        "ProxyPassword": "",
                                        "ProxyUser": ""
                                    },
                                    "Url": "",
                                    "Wxid": wxid
                                }

                                logger.debug(f"发送唤醒登录请求到 {api_url} 参数: {json_param}")

                                try:
                                    # 发送请求
                                    response = await session.post(api_url, json=json_param)

                                    # 检查响应状态码
                                    if response.status != 200:
                                        logger.error(f"唤醒登录请求失败，状态码: {response.status}")
                                        raise Exception(f"服务器返回状态码 {response.status}")

                                    # 解析响应内容
                                    json_resp = await response.json()
                                    logger.debug(f"唤醒登录响应: {json_resp}")

                                    # 检查是否成功
                                    if json_resp and json_resp.get("Success"):
                                        # 尝试获取 UUID
                                        data = json_resp.get("Data", {})
                                        qr_response = data.get("QrCodeResponse", {}) if data else {}
                                        uuid = qr_response.get("Uuid", "") if qr_response else ""

                                        if uuid:
                                            logger.success(f"唤醒登录成功，获取到登录uuid: {uuid}")
                                            # 更新状态，记录UUID但没有二维码
                                            update_bot_status("waiting_login", f"等待微信登录 (UUID: {uuid})")
                                        else:
                                            logger.error("唤醒登录响应中没有有效的UUID")
                                            raise Exception("响应中没有有效的UUID")
                                    else:
                                        # 如果请求不成功，获取错误信息
                                        error_msg = json_resp.get("Message", "未知错误") if json_resp else "未知错误"
                                        logger.error(f"唤醒登录失败: {error_msg}")
                                        raise Exception(error_msg)

                                except Exception as e:
                                    logger.error(f"唤醒登录过程中出错: {e}")
                                    logger.error("将尝试二维码登录")
                                # 如果唤醒登录失败，回退到二维码登录
                                if not device_name:
                                    device_name = bot.create_device_name()
                                if not device_id:
                                    device_id = bot.create_device_id()
                                uuid, url = await bot.get_qr_code(device_id=device_id, device_name=device_name, print_qr=True)
                                logger.success("获取到登录uuid: {}", uuid)
                                logger.success("获取到登录二维码: {}", url)
                                # 更新状态，记录二维码URL
                                update_bot_status("waiting_login", "等待微信扫码登录", {
                                    "qrcode_url": url,
                                    "uuid": uuid,
                                    "expires_in": 240, # 默认240秒过期
                                    "timestamp": time.time()
                                })
                        except Exception as e:
                            logger.error("唤醒登录失败: {}", e)
                            # 如果唤醒登录出错，回退到二维码登录
                            if not device_name:
                                device_name = bot.create_device_name()
                            if not device_id:
                                device_id = bot.create_device_id()
                            uuid, url = await bot.get_qr_code(device_id=device_id, device_name=device_name, print_qr=True)
                            logger.success("获取到登录uuid: {}", uuid)
                            logger.success("获取到登录二维码: {}", url)
                            # 更新状态，记录二维码URL
                            update_bot_status("waiting_login", "等待微信扫码登录", {
                                "qrcode_url": url,
                                "uuid": uuid,
                                "expires_in": 240, # 默认240秒过期
                                "timestamp": time.time()
                            })

                else:
                    # 二维码登录
                    if not device_name:
                        device_name = bot.create_device_name()
                    if not device_id:
                        device_id = bot.create_device_id()
                    uuid, url = await bot.get_qr_code(device_id=device_id, device_name=device_name, print_qr=True)
                    logger.success("获取到登录uuid: {}", uuid)
                    logger.success("获取到登录二维码: {}", url)
                    # 更新状态，记录二维码URL
                    update_bot_status("waiting_login", "等待微信扫码登录", {
                        "qrcode_url": url,
                        "uuid": uuid,
                        "expires_in": 240, # 默认240秒过期
                        "timestamp": time.time()
                    })

                    # 检查状态文件是否正确更新
                    try:
                        status_file = script_dir / "admin" / "bot_status.json"
                        if status_file.exists():
                            with open(status_file, "r", encoding="utf-8") as f:
                                current_status = json.load(f)
                                if current_status.get("qrcode_url") != url:
                                    logger.warning("状态文件中的二维码URL与实际不符，尝试重新更新状态")
                                    # 再次更新状态
                                    update_bot_status("waiting_login", "等待微信扫码登录", {
                                        "qrcode_url": url,
                                        "uuid": uuid,
                                        "expires_in": 240,
                                        "timestamp": time.time()
                                    })
                    except Exception as e:
                        logger.error(f"检查状态文件失败: {e}")

                # 显示倒计时
                logger.info("等待登录中，过期倒计时：240")

            except Exception as e:
                logger.error("发生错误: {}", e)
                # 出错时重新尝试二维码登录
                if not device_name:
                    device_name = bot.create_device_name()
                if not device_id:
                    device_id = bot.create_device_id()
                uuid, url = await bot.get_qr_code(device_id=device_id, device_name=device_name, print_qr=True)
                logger.success("获取到登录uuid: {}", uuid)
                logger.success("获取到登录二维码: {}", url)
                # 更新状态，记录二维码URL
                update_bot_status("waiting_login", "等待微信扫码登录", {
                    "qrcode_url": url,
                    "uuid": uuid,
                    "expires_in": 240, # 默认240秒过期
                    "timestamp": time.time()
                })

            while True:
                stat, data = await bot.check_login_uuid(uuid, device_id=device_id)
                if stat:
                    break
                # 计算剩余时间
                expires_in = data
                logger.info("等待登录中，过期倒计时：{}", expires_in)
                # 更新状态，包含倒计时
                update_bot_status("waiting_login", f"等待微信扫码登录 (剩余{expires_in}秒)", {
                    "qrcode_url": url if 'url' in locals() else None,
                    "uuid": uuid,
                    "expires_in": expires_in,
                    "timestamp": time.time()
                })
                await asyncio.sleep(5)

        # 保存登录信息
        robot_stat["wxid"] = bot.wxid
        robot_stat["device_name"] = device_name
        robot_stat["device_id"] = device_id
        with open("resource/robot_stat.json", "w") as f:
            json.dump(robot_stat, f)

        # 获取登录账号信息
        bot.wxid = data.get("acctSectResp").get("userName")
        bot.nickname = data.get("acctSectResp").get("NickName")
        bot.alias = data.get("acctSectResp").get("Alais")
        bot.phone = data.get("acctSectResp").get("Mobile")
        # update_worker_success = await db.update_worker_db(bot.wxid, bot.nickname, bot.phone)
        logger.info("登录账号信息: wxid: {}  昵称: {}  微信号: {}  手机号: {}", bot.wxid, bot.nickname, bot.alias,
                    bot.phone)

        # 登录微信
        try:
            # 等待登录，获取个人信息
            # await bot.login() - 这个方法不存在
            # 直接使用之前获取的个人信息即可，因为在 check_login_uuid 成功后已经设置了 wxid
            # 登录成功后更新状态
            update_bot_status("online", f"已登录：{bot.nickname}", {
                "nickname": bot.nickname,
                "wxid": bot.wxid,
                "alias": bot.alias
            })
        except Exception as e:
            logger.error(f"登录失败: {e}")
            update_bot_status("error", f"登录失败: {str(e)}")
            return None

    else:  # 已登录
        bot.wxid = wxid
        profile = await bot.get_profile()

        bot.nickname = profile.get("userInfo").get("NickName").get("string")
        bot.alias = profile.get("userInfo").get("Alias")
        bot.phone = profile.get("userInfo").get("BindMobile").get("string")
        # 不需要使用头像图片URL

        logger.info("profile登录账号信息: wxid: {}  昵称: {}  微信号: {}  手机号: {}", bot.wxid, bot.nickname, bot.alias,
                    bot.phone)

    logger.info("登录设备信息: device_name: {}  device_id: {}", device_name, device_id)

    logger.success("登录成功")

    # 更新状态为在线
    update_bot_status("online", f"已登录：{bot.nickname}", {
        "nickname": bot.nickname,
        "wxid": bot.wxid,
        "alias": bot.alias
    })

    # 先初始化通知服务，再发送重连通知
    # 初始化通知服务
    notification_config = config.get("Notification", {})
    notification_service = init_notification_service(notification_config)
    logger.info(f"通知服务初始化完成，启用状态: {notification_service.enabled}")

    # 发送微信重连通知
    if notification_service and notification_service.enabled and notification_service.triggers.get("reconnect", False):
        if notification_service.token:
            logger.info(f"发送微信重连通知，微信ID: {bot.wxid}")
            asyncio.create_task(notification_service.send_reconnect_notification(bot.wxid))
        else:
            logger.warning("PushPlus Token未设置，无法发送重连通知")

    # ========== 登录完毕 开始初始化 ========== #

    # 开启自动心跳
    try:
        success = await bot.start_auto_heartbeat()
        if success:
            logger.success("已开启自动心跳")
        else:
            logger.warning("开启自动心跳失败")
    except ValueError:
        logger.warning("自动心跳已在运行")
    except Exception as e:
        logger.warning("自动心跳已在运行:{}",e)

    # 初始化机器人
    xybot = XYBot(bot)
    xybot.update_profile(bot.wxid, bot.nickname, bot.alias, bot.phone)

    # 设置机器人实例到管理后台
    set_bot_instance(xybot)

    # 初始化数据库
    XYBotDB()

    message_db = MessageDB()
    await message_db.initialize()

    keyval_db = KeyvalDB()
    await keyval_db.initialize()

    # 通知服务已在前面初始化完成

    # 启动调度器
    scheduler.start()
    logger.success("定时任务已启动")

    # 添加图片文件自动清理任务
    try:
        from utils.files_cleanup import FilesCleanup

        # 获取清理天数配置
        cleanup_days = config.get("XYBot", {}).get("files-cleanup-days", 7)

        if cleanup_days > 0:
            # 创建清理任务
            cleanup_task = FilesCleanup.schedule_cleanup(config)

            # 添加到定时任务，每天执行一次
            scheduler.add_job(
                cleanup_task,
                'interval',
                hours=24,
                id='files_cleanup',
                next_run_time=datetime.now() + timedelta(minutes=5)  # 系统启动5分钟后执行第一次清理
            )
            logger.success(f"已添加图片文件自动清理任务，清理天数: {cleanup_days}天，每24小时执行一次")
        else:
            logger.info("图片文件自动清理功能已禁用 (files-cleanup-days = 0)")
    except Exception as e:
        logger.error(f"添加图片文件自动清理任务失败: {e}")

    # 加载插件目录下的所有插件
    loaded_plugins = await plugin_manager.load_plugins_from_directory(bot, load_disabled_plugin=False)
    logger.success(f"已加载插件: {loaded_plugins}")

    # ========== 开始接受消息 ========== #

    # 先接受堆积消息
    logger.info("处理堆积消息中")
    count = 0
    while True:
        ok,data = await bot.sync_message()
        data = data.get("AddMsgs")
        if not data:
            if count > 2:
                break
            else:
                count += 1
                continue

        logger.debug("接受到 {} 条消息", len(data))
        await asyncio.sleep(1)
    logger.success("处理堆积消息完毕")

    # 更新状态为就绪
    update_bot_status("ready", "机器人已准备就绪")

    # 启动自动重启监控器
    try:
        from utils.auto_restart import start_auto_restart_monitor
        start_auto_restart_monitor()
        logger.success("自动重启监控器已启动")
    except Exception as e:
        logger.error(f"启动自动重启监控器失败: {e}")

    logger.success("开始处理消息")

    # 添加重连检测变量
    message_failure_count = 0
    max_failure_count = 3  # 连续失败超过这个数量则认为离线
    is_offline = False

    while True:
        # 不需要记录当前时间

        try:
            ok,data = await bot.sync_message()

            # 如果成功获取消息，重置失败计数
            if ok:
                # 如果之前处于离线状态，现在恢复了，发送重连通知
                if is_offline and message_failure_count > 0:
                    is_offline = False
                    message_failure_count = 0

                    # 发送重连通知
                    notification_service = get_notification_service()
                    if notification_service and notification_service.enabled and notification_service.triggers.get("reconnect", False):
                        if notification_service.token:
                            logger.info(f"发送微信重连通知，微信ID: {bot.wxid}")
                            asyncio.create_task(notification_service.send_reconnect_notification(bot.wxid))
                        else:
                            logger.warning("PushPlus Token未设置，无法发送重连通知")

                # 正常情况下重置计数器
                if message_failure_count > 0:
                    message_failure_count = 0

        except Exception as e:
            logger.warning("获取新消息失败 {}", e)
            # 增加失败计数
            message_failure_count += 1

            # 如果连续失败超过阈值，标记为离线状态
            if message_failure_count >= max_failure_count and not is_offline:
                is_offline = True
                logger.warning(f"连续 {message_failure_count} 次获取消息失败，微信可能已离线")

            # 等待一段时间后重试
            await asyncio.sleep(5)
            logger.info("5秒后继续尝试获取消息")
            continue

            # 以下代码已注释，不再自动重新登录
            # update_bot_status("waiting_login", "等待微信登录")
            # 清除所有定时任务
            # scheduler.remove_all_jobs()
            # logger.success("所有定时任务已清除")
            # await bot_core()
            # break

        # 如果成功获取消息但没有数据，处理消息数据

        # 检查data是否为字典类型
        if isinstance(data, dict):
            messages = data.get("AddMsgs")
            if messages:
                for message in messages:
                    asyncio.create_task(xybot.process_message(message))
        elif data:  # 如果data不是字典但有值，记录日志
            logger.warning(f"Unexpected data type: {type(data)}, value: {data}")

            # 检测特定的错误消息
            if isinstance(data, str) and "用户可能退出" in data:
                # 如果检测到用户退出消息，增加失败计数
                message_failure_count += 1

                # 如果连续失败超过阈值，标记为离线状态
                if message_failure_count >= max_failure_count and not is_offline:
                    is_offline = True
                    logger.warning(f"检测到用户退出消息，微信可能已离线")

                    # 发送离线通知
                    notification_service = get_notification_service()
                    if notification_service and notification_service.enabled and notification_service.triggers.get("offline", False):
                        if notification_service.token:
                            logger.info(f"发送微信离线通知，微信ID: {bot.wxid}")
                            asyncio.create_task(notification_service.send_offline_notification(bot.wxid))
                        else:
                            logger.warning("PushPlus Token未设置，无法发送离线通知")

                    # 更新状态为离线
                    update_bot_status("offline", "微信已离线")
        # 使用异步睡眠替代忙等待循环
        await asyncio.sleep(0.5)

    # 返回机器人实例（此处不会执行到，因为上面的无限循环）
    return xybot