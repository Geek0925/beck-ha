# 博克门锁（BOKE）Wi-Fi 锁 · Home Assistant 集成

从 `com.jf.jlinkiot` 2.5.1（**iCSee Home**，旧名 **JLink**，杰峰 JFTech 系）逆向出的
云控 SDK + HACS 集成。可把博克 WiFi 门锁接进 HA：**上锁/开锁**、电量、门磁/防拆/强开/故障、
开锁记录。

> ⚠️ 本集成走的是厂商**私有云**，非官方支持；接口随时可能变。请先读
> [`docs/逆向报告.md`](docs/逆向报告.md) 了解哪些环节已确证、哪些需要你的账号联调。

## 能力

| 平台 | 实体 | 说明 |
| --- | --- | --- |
| `lock` | 门锁本体 | `unlock` / `lock`，状态取自 `LockState` |
| `sensor` | 电量、开锁次数、最近开锁时间/用户、Wi-Fi 信号 | |
| `binary_sensor` | 防拆、强制开门、故障、门状态、锁芯盖、门未关提醒 | |
| `event` | **开锁事件**（每次开锁一条，`event_type` = 开锁方式） | 可直接做自动化触发 |
| 服务 | `boke_doorlock.get_unlock_records` | 按时间窗查询开锁记录（方式/用户/图片） |

## 安装

1. HACS → 集成 → 右上角 ⋮ → **自定义存储库** → 填入本仓库地址 → 类别选 **Integration**。
2. 安装后重启 Home Assistant。
3. 设置 → 设备与服务 → **添加集成** → 搜索「博克门锁」。
4. 直接填 **iCSee Home 账号**：手机号/邮箱 + 密码 + 国家区号（邮箱可留空区号）。
   登录成功后自动拉设备列表，SN 留空 = 全部门锁。

登录走的是 App 的账号接口：

```
POST https://<账号服务>/mobilephone/login/v3/{timeMillis}/{sign}.rs
body: {"loginName": "...", "password": "<md5>", "areaCode": "86"}   # 邮箱不带 areaCode
→ data.accessToken / data.uuid，之后请求带 Authorization + token 头
```

密码默认按 MD5 提交，可在**高级**步骤改成 `plain`；也能换 API 域名、覆盖签名用的
appKey/appSecret。token 会缓存，过期（401 / 40103 等）时自动重新登录，无需人工干预。

> ⚠️ 凭证已经从真机日志里取到（`uuid=xmeye`、appKey/appSecret、`moveCard=4`），
> **签名层已实测通过**；目前卡在请求/响应 body 的对称加密（密钥为会话协商或藏在
> 易盾保护层，3 万+ 组合已排除）。细节与收尾方案见
> [`docs/逆向报告.md`](docs/逆向报告.md) 第 9.5 节。
>
> 注意：密码会以明文保存在 HA 的 `.storage` 配置条目里（和其他云集成一样），
> 设备被他人接管的话建议改用开放平台凭证。

## 实时状态：MQTT 推送（实验）

App 的实时状态走私有 broker `jfmq-v2.xmcsrv.net`（XCloudSDK 内嵌 paho-mqtt C 实现），
topic 结构与鉴权包在加固层里，静态逆不出来。所以集成提供的是**可配置的订阅器**：

选项 → 打开「启用 MQTT 实时推送」，填 broker / 端口 / 用户名 / 密码（常用设备 token）/ topic，
默认订阅 `#` 并打开「记录每条 payload」。然后看日志：

```
MQTT dev/<SN>/up -> sn=SN123 props={'LockState': 1, 'Battery': 87}
```

凭据留空时会自动去云上要：用户名 = 登录返回的 uuid，密码 = `/v3/device/getDeviceToken`
拿到的设备 token（和原生 SDK 的做法一致）。也可以手填覆盖。

拿到真实 topic 后把 `#` 收窄成 `dev/<SN>/#`。收到 payload 会直接合并进对应门锁的物模型，
实体即时刷新，不必等下一轮轮询；轮询仍保留作为兜底。

排查用：下载集成诊断（设置 → 设备与服务 → 博克门锁 → 诊断）看 `mqtt` 段——
`messages` 是收到的条数、`last_topic`/`last_payload` 是最后一条，能直接看出连没连上、
要不要换 topic。

## 开锁记录

- 每 5 分钟（可改 `record_interval`）拉一次近 24h 的 `getRecordReport`，按 `recordId`
  去重，新记录会：① 触发 `event` 实体；② 在 HA 总线上发出 `boke_doorlock_unlock` 事件：
  ```yaml
  trigger:
    - platform: event
      event_type: boke_doorlock_unlock
      event_data:
        method: fingerprint
  ```
- 手动查询历史：
  ```yaml
  service: boke_doorlock.get_unlock_records
  data: {sn: "SN123", hours: 48, limit: 100}
  ```
  返回 `{count, records:[{sn, timestamp, method, user, user_type, image_url, raw}]}`。

## 状态变化与「谁开的门」是怎么同步的

App 的即时弹窗走的是**云 → 手机**的推送，锁本身不直连手机：

```
锁 ──常连 TCP──► 厂商云 ──► ① 厂商推送(HMS/MiPush/OPPO/vivo/Honor/Umeng/Getui/FCM) → 后台/前台都能弹
                        └─► ② MQTT(jfmq-v2) 前台在线时走这条 → 应用内横幅
                        └─► ③ 通知日志 /v3/message/getNotifyLogList → 消息列表
```

HA 拿不到厂商推送（需要 App 的推送证书与注册流程），所以本集成用 ③ 复刻：

- 每轮轮询都会顺手拉一次**通知日志**（`size=20`，很便宜），按 `messageId` 去重；
  启动首轮只做“已知消息”播种，不会把历史消息全喷成事件。
- 命中解锁类消息时同时发两个总线事件：`boke_doorlock_notification`（全部消息）
  和 `boke_doorlock_unlock`（只解锁，带 `user`）。
- **用户名**有两个来源，按可靠性排序：开锁记录 `getRecordReport` 的 `nickName`
  → 通知日志的 `nickName/userName` → 物模型属性里的 `last_user`。
- 实体侧：`sensor.…_last_unlock_user` / `sensor.…_last_unlock_method` /
  `sensor.…_last_unlock_at` 取自最新记录；`lock` 实体的属性也带
  `last_unlock_user` / `last_unlock_method` / `last_notification`。

### 告警实体（撬锁 / 强开 / 低电 / 离线 / 故障 / 门未关）

通知日志里 `msgType` 命中关键词就点亮对应 `binary_sensor`，`alert_reset`
秒（默认 300，0=常亮）后自动复位，属性里带触发时间和最后一条消息：

| 实体 | 触发关键词（不区分大小写） |
| --- | --- |
| `alert_tamper` 防撬 | tamper / pry / 撬 / 防拆 |
| `alert_forced_open` 强开 | forced / break / 暴力 / 强开 |
| `alert_low_battery` 低电 | lowbattery / battery / 低电 |
| `alert_offline` 离线 | offline / disconnect / 掉线 |
| `alert_fault` 故障 | fault / malfunction / 故障 / 堵转 |
| `alert_door_open` 门未关 | dooropen / ajar / 未关（默认关闭，够用就开） |

延迟 = 轮询间隔。想要“开门 5 秒内反应”，把选项里的 `scan_interval` 调到 10 秒
（通知日志是增量拉取，压力比反复拉记录小）。如果 MQTT 的 topic 打通了，就完全不用等轮询。

## SDK 独立使用

`custom_components/boke_doorlock/jfcloud` 是一个不依赖 HA 的纯 Python 包：

```python
import asyncio, sys
sys.path.insert(0, "custom_components/boke_doorlock")

from jfcloud import JFCloudClient
from jfcloud import APP_KEY, APP_SECRET  # APK 内提取的候选值，可换成开放平台凭证

async def main():
    async with JFCloudClient(
        app_key=APP_KEY, app_secret=APP_SECRET,
        login_name="13800000000", password="your-password",
    ) as c:
        await c.login()                       # 账号密码登录，后面全自动带 token
        print(await c.list_devices())
        print(await c.get_prop_status(["SN123"]))
        await c.send_command("SN123", "OPLOCK", {"Action": 1})   # 1=开锁 0=上锁

asyncio.run(main())
```

只依赖 `aiohttp`。签名实现见 `jfcloud/signature.py`，**已用官方文档校验值验证**：

```
uuid=test appKey=test appSecret=password
timeMillis=00000011461748332239 movedCard=5
→ 285a38b2ebf8787e42f047e0b711297b  ✔
```

## 图标

品牌图放在集成目录里，HA 会自动用（较新的 HA 支持本地 `brand/`，老版本忽略、不影响功能）：

```
custom_components/boke_doorlock/brand/
├── icon.png        256×256   集成列表 / 设备页图标
├── icon@2x.png     512×512   高分屏
├── logo.png        512×256   集成详情横幅
├── dark_icon.png   256×256   深色主题
└── dark_logo.png   512×256   深色主题横幅
```

配色取自厂商自己的高亮色 `#12B5B0`（APK 资源里就是它），图形是「挂锁 + Wi-Fi 弧」，
对应博克 WiFi 锁。想进 HACS 默认仓库的标志库（`home-assistant/brands`）需要另提 PR，
自定义存储库只用本地 `brand/` 就够。

实体图标：锁用 HA 内置的开/关锁图标；`event` 实体 `mdi:door-open`；
传感器分别是电池 / 计数 / 钥匙 / 时钟 / 信号；二进制传感器按设备类型自动（防拆、门、安全、故障）。

## 已知限制

- 只轮询（`cloud_polling`），实时性取决于轮询间隔（默认 30s）；App 内用的是 MQTT 推送，
  本集成暂未复刻。
- 开锁指令名 `OPLOCK`/`Action` 字段是按协议族推断的，若你的固件不回 `LockState`，
  请开 debug 日志看 `props` 原始键名，再改 `const.py`。
- 云 P2P 视频（CAPS）不在本集成范围内。

## 目录

```
custom_components/boke_doorlock/
├── __init__.py        # 装配
├── config_flow.py     # 账号/设备配置
├── coordinator.py     # 轮询编排
├── lock.py sensor.py binary_sensor.py
├── diagnostics.py     # 诊断（密钥已脱敏）
├── const.py           # 指令名/默认值（可按固件改）
└── jfcloud/           # ★ 提取出来的门锁云 SDK
    ├── signature.py   # 移位+交错+MD5 签名（含校验值）
    ├── client.py      # 签名请求 / 设备 / 令牌 / 属性 / 指令 / 记录
    ├── models.py      # 门锁物模型解析
    └── errors.py
```

## 免责声明

仅用于你自己账号下的自有设备。请遵守厂商服务条款，勿用于批量控制他人设备。
