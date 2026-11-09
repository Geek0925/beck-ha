# 博克门锁（BOKE）· Home Assistant 集成

把博克（小峰管家）Wi-Fi 门锁接入 Home Assistant。

> 本集成走厂商私有云，非官方支持。当前版本：**0.9.4**

---

## 已实现功能

### 账号与设备

- 手机号 + 密码登录（账号密码加密提交，登录态自动续期）
- 登录失败自动重试，切换备用入口
- 自动发现账号下所有门锁设备
- 支持只同步指定 SN（多设备场景）

### 状态数据（来自云端 REST）

| 数据       | 实体                      | 说明              |
| -------- | ----------------------- | --------------- |
| 电量       | `sensor.<设备>_电量`        | 百分比，实测可用        |
| Wi-Fi 名称 | `sensor.<设备>_wi_fi_名称`  | 设备当前连接的 Wi-Fi   |
| Wi-Fi 信号 | `sensor.<设备>_wi_fi_信号`  | dBm（默认隐藏）       |
| 在线状态     | `binary_sensor.<设备>_在线` | 来自 statusGlobal |
| 房间       | `sensor.<设备>_房间`        | 设备所属房间（默认隐藏）    |

### 开锁记录

- 从云端拉取开锁记录，包含：**开锁人、开锁方式、开锁时间**
- 开锁方式已归一化：指纹 / 人脸 / 密码 / 卡片 / 钥匙 / 遥控 / 临时密码 / App
- 三个传感器常驻显示最近一次开锁：
  - `sensor.<设备>_最近开锁用户`
  - `sensor.<设备>_最近开锁方式`
  - `sensor.<设备>_最近开锁时间`（格式 `yyyy-MM-dd HH:mm:ss`，北京时间）
- 事件实体 `event.<设备>_开锁事件`：每次新开锁触发一次，可直接做自动化触发器
- 总线事件 `boke_doorlock_unlock`：携带 `sn` / `user` / `method` / `timestamp`
- 服务 `boke_doorlock.get_unlock_records`：按时间窗查询历史记录

### 手动刷新

- 按钮实体 `button.<设备>_立即刷新`：一键拉取最新状态与开锁记录，不受轮询间隔限制

### 轮询策略

- 状态轮询默认**关闭**（0 = 不轮询），可配置 0–600 秒
- 开锁记录拉取默认 300 秒，可配置 0–3600 秒（0 = 关闭）
- 两种间隔都可设为 0，完全依赖手动刷新按钮

---

## 不支持的功能

以下能力走设备 P2P 加密通道，云端 REST 取不到，本集成不提供：

- 远程开锁 / 上锁
- 视频流（RTMP/实时画面）
- 实时门磁状态、防拆、强制开门、故障
- 锁的开合状态（`lock` 实体状态会是「未知」）
- 充电状态、临时密码数量
- 报警抓拍图

---

## 安装

### HACS（推荐）

1. HACS → 集成 → 右上角 `⋮` → 自定义仓库
2. 添加 `https://github.com/Geek0925/beck-ha`，类型选「集成」
3. 搜索「博克门锁」安装
4. 重启 Home Assistant

### 手动安装

1. 下载本仓库，把 `custom_components/boke_doorlock` 整个目录复制到 HA 的 `/config/custom_components/`
2. 重启 Home Assistant

---

## 配置

设置 → 设备与服务 → 添加集成 → 搜索「博克门锁」

| 字段    | 说明                      |
| ----- | ----------------------- |
| 手机号   | iCSee Home / 小峰管家 登录手机号 |
| 密码    | 对应密码                    |
| 国家区号  | 默认 86                   |
| SN 过滤 | 可选，留空表示同步全部设备           |

### 选项（集成卡片 → 配置）

| 选项     | 范围       | 说明             |
| ------ | -------- | -------------- |
| 状态轮询间隔 | 0–600 秒  | 0 = 不轮询（推荐）    |
| 开锁记录间隔 | 0–3600 秒 | 0 = 不拉取；建议 300 |
| SN 过滤  | 文本       | 留空 = 全部设备      |

---

## 实体一览

| 平台            | 实体       | 默认  |
| ------------- | -------- | --- |
| sensor        | 电量       | 启用  |
| sensor        | Wi-Fi 名称 | 启用  |
| sensor        | Wi-Fi 信号 | 隐藏  |
| sensor        | 房间       | 隐藏  |
| sensor        | 最近开锁用户   | 启用  |
| sensor        | 最近开锁方式   | 启用  |
| sensor        | 最近开锁时间   | 启用  |
| binary_sensor | 在线       | 启用  |
| button        | 立即刷新     | 启用  |
| event         | 开锁事件     | 启用  |

---

## 服务

### `boke_doorlock.get_unlock_records`

查询指定设备的开锁记录。

```yaml
service: boke_doorlock.get_unlock_records
data:
  sn: "10000748294389457514"
  hours: 24
```

---

## 自动化示例

有人开门时推送通知：

```yaml
trigger:
  - platform: state
    entity_id: event.boke_10000748294389457514_unlock
action:
  - service: notify.mobile_app_phone
    data:
      title: "门锁开启"
      message: >-
        {{ trigger.to_state.attributes.user }} 用
        {{ trigger.to_state.attributes.method }} 开门
```

电量低于 20% 提醒：

```yaml
trigger:
  - platform: numeric_state
    entity_id: sensor.boke_10000748294389457514_电量
    below: 20
action:
  - service: notify.mobile_app_phone
    data:
      message: "门锁电量低，请及时更换电池"
```

---

## 调试

日志里搜 `boke_doorlock` 可看到

---

## 免责声明

本集成通过逆向厂商 App 通信协议实现，与厂商无任何关系。使用风险自负：

- 接口可能随时变更导致失效
- 请勿用于任何非法用途
- 账号密码仅保存在本地 HA 配置中
