# v0.1.0 发布验收

本文定义 HTTP 首个稳定版本的封版边界。WebSocket 不在本次验收范围内。

## 官方插件设置核对

依据 [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
和 [插件发布指南](https://docs.astrbot.app/dev/star/plugin-publish.html)：

- 插件目录名和 `metadata.name` 均为 `astrbot_plugin_interconnect`。
- `main.py` 包含继承 `Star` 的插件入口类。
- `_conf_schema.json`、`requirements.txt`、`README.md` 和 `LICENSE` 已提供。
- `metadata.version` 使用语义化版本，Git 发布标签使用对应的 `v` 前缀。
- `support_platforms` 使用 AstrBot 官方适配器键。
- `astrbot_version` 覆盖配置文件选择器所需的 AstrBot 4.13 及以上版本。
- `short_desc` 和市场检索标签已提供。
- Logo、Skills、国际化和个人主页均为可选项，本版本不需要。
- 发布包远低于 16 MB 限制。

插件唯一发布仓库为
<https://github.com/tntexploding/astrbot_plugin_interconnect>；`metadata.repo` 和 Git
`origin` 必须同时指向该地址。

## 自动化检查

发布前在插件目录执行：

```powershell
..\..\..\.venv\Scripts\ruff.exe format --check . tests
..\..\..\.venv\Scripts\ruff.exe check . tests
..\..\..\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

测试覆盖以下关键行为：

- 配置迁移、WebUI Schema 和路由条件。
- QQ 消息规范化、原始前缀保留和媒体元数据。
- HTTP 服务真实监听、停启和健康检查。
- Bearer 鉴权、请求体上限、非法 JSON 与非法字段。
- QQ 投递失败时返回 HTTP 502 和逐目标结果。
- Webhook 标准包体、请求头、5xx 重试和超时隔离。
- 会话映射在存储对象重建后恢复。
- 自定义 JSON 文件模板及路径边界。

## 已完成的真实联调

- AstrBot 正确加载插件并展示 WebUI 配置。
- QQ 消息命中路由并发送到本地 HTTP 服务。
- 本地 HTTP 请求通过插件发送到 QQ 会话。
- 群聊来源限制和文本前缀限制能够共同生效。

## 发布前 Git 检查

- 确认 `metadata.yaml` 的版本与标签一致。
- 确认 `metadata.yaml` 的 `repo` 指向唯一发布仓库。
- 确认 Git `origin` 为 `tntexploding/astrbot_plugin_interconnect`，禁止推送到
  AstrBot 示例插件仓库。
- 确认运行配置、鉴权令牌、缓存和 AstrBot 持久化数据未进入提交。
- 完整测试通过后再创建并推送 `v0.1.0` 标签。
