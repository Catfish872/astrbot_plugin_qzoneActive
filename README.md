
<div align="center">


# astrbot_plugin_qzoneActive
【重要】
该插件由astrbot_plugin_qzone魔改而来，在原插件的基础上，使用InitiativeDialogue作为前置插件（需要使用其生成的日程表），使bot自动地经过
LLM，发表说说，并对可配置的关注列表中发表的说说进行点赞和评论
原插件链接
https://github.com/Zhalslar/astrbot_plugin_qzone
使用InitiativeDialogue作为前置插件：
https://github.com/advent259141/astrbot_plugin_InitiativeDialogue
</div>

## 🤝 介绍

魔改后的QQ空间对接插件, 利用InitiativeDialogue插件生成的日程表，自动发说说，以及点赞与评论关注的ID

魔改后会自动读取InitiativeDialogue插件生成的日程表，每天在随机的时间，参考日程表发表说说，在发表完说说后，会对关注列表中的每个人点赞三条最新的说说（如果没有点赞过），并最终选择所有人加起来共计5条说说进行评论（一次请求完成）

## 📦 安装

- 直接在astrbot的插件市场搜索astrbot_plugin_qzoneActive，点击安装，等待完成即可

- 也可以克隆源码到插件文件夹：

```bash
# 克隆仓库到插件目录
cd /AstrBot/data/plugins
git clone https://github.com/Zhalslar/astrbot_plugin_qzone

# 控制台重启AstrBot
```

## ⌨️ 配置

请前往插件配置面板进行配置

## 🐔 使用说明

### 命令表

| 命令       | 参数                           | 说明               | 权限要求 |
|:----------:|:----------------------------:|:-------------------:|:-----------------------:|
| 发说说     | 文字 + 图片（可选）            | 直接发布一条 QQ 空间说说，无需审核         | 管理员  |
| 投稿       | 文字 + 图片（可选）            | 向管理员投稿，等待审核                | 所有人     |
| 通过       | （引用待审核稿件）             | 审核通过该稿件，并自动发布到 QQ 空间 | 管理员     |  
| 不通过     | （引用稿件）+ 理由（可选）     | 审核不通过该稿件，并向投稿人说明原因   | 管理员     |  
| 查看稿件   | 稿件 ID（整数）                | 查看本地数据库中指定稿件的详细内容      | 管理员     |
| 查看说说   | 序号（可选，默认 1）           | 拉取空间说说列表，并显示其中第 N 条   | 所有人     |
| 查看访客   | 无                             | 获取并显示 QQ 空间最近访客列表   | 管理员     |
| 关注好友   | 要关注的qq号             | 将qq号添加到点赞和评论的列表       | 管理员    |
| 取关好友   | 要取关的qq号             | 取消列表中的该qq号       | 管理员    |
| 查看关注列表   | 无             | 查看点赞和评论的qq号列表       | 管理员    |
| 手动发说说   | 无             | 手动触发一次LLM发说说+点赞评论的工作过程       | 管理员    |
