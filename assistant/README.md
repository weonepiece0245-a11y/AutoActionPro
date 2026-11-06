# AI 小助理

一个在终端里运行的 AI 聊天助理,基于 Claude API(模型 `claude-opus-5-5`)。回答逐字流式显示,并且会记住上下文,支持连续追问。

## Windows 一键启动

双击 `assistant\start.bat` 就能启动,它会自动完成以下几步:

1. 查找 Python,没装的话会提示下载地址
2. 第一次运行时自动安装依赖
3. 如果没有 `.env`,自动创建并用记事本打开,填好 Key、保存并关闭记事本后接着启动

想把它变成桌面上的按钮:右键 `start.bat` →“发送到”→“桌面快捷方式”。如果想换个图标,右键这个快捷方式 →“属性”→“更改图标”。

## 私人助手“星期五”

“星期五”是一个更懂你、也能动手办事的私人助手:

- **了解你**:聊天中你提到的称呼、工作、城市、喜好、习惯、目标、重要的日子等,它会自动记下来,下次打开还记得,并按你的习惯给建议。
- **能上网**:需要最新信息(新闻、天气、价格、资料)时会自己搜索、读网页。
- **能动手**:可以在你的电脑上执行命令、读写文件,比如整理文件夹、查看文件内容、把写好的文章保存下来、打开程序或网页。
- **知道现在几点**:每次提问都会附上当前日期和时间,按时段跟你打招呼。
- **性格**:冷静干练,先给结论再给细节,会主动提醒风险和下一步。

### 启动

- **Windows**:双击 `assistant\friday.bat`(首次运行的安装和填 Key 步骤和 `start.bat` 一样)
- **命令行**:`python assistant/assistant.py --persona friday`

```
下午好,老板。星期五已上线,随时待命。
现在是 2026-11-06 星期五 17:26。
我们刚认识,多跟我说说你自己,我会记住的。

你:我叫阿杰,在杭州做设计,早上一般只喝美式。
星期五:
  〔记住了:用户叫阿杰〕
  〔记住了:用户在杭州做设计工作〕
  〔记住了:用户早上习惯喝美式咖啡〕
记下了,阿杰。……

你:帮我看看桌面上有哪些文件

  ⚠ 星期五请求执行命令:dir %USERPROFILE%\Desktop
  目的:列出桌面上的文件
  允许吗?[y] 允许  [n] 拒绝  [a] 本次运行都允许:y
```

### 语音对话

双击 `assistant\friday-voice.bat` 就能用嘴和“星期五”聊天,它的回答会读出来。第一次运行会自动安装语音相关的依赖。

- **说话**:直接按**回车**,看到“我在听,请说话……”后对着麦克风说,停顿 1 秒自动结束。
- **打字**:照常输入文字再回车,和平时一样。
- **结束**:说“退出”或“再见”,或者按 Ctrl+C。朗读时按 Ctrl+C 可以打断朗读。
- 语音模式下,“星期五”的回答会更口语、更简短;长内容(文章、代码)会存成文件告诉你位置。
- 执行命令、写文件仍然要你在键盘上按 `y` 确认,语音不能代替确认。

**语音识别引擎**(用 `--stt` 参数或在 `.env` 里写 `FRIDAY_STT=` 选择):

| 引擎 | 说明 |
|---|---|
| `google`(默认) | 在线识别,免费、准确度好,**需要能访问 Google** |
| `vosk` | 离线识别,不需要联网,准确度稍差;第一次使用会自动下载约 42MB 的中文模型到 `assistant/models/` |

如果识别时提示“在线语音识别连接失败”,在 `assistant/.env` 里加一行 `FRIDAY_STT=vosk` 改用离线识别。

**朗读**用的是系统自带的语音,不需要联网。如果提示“没有找到中文朗读语音”,在 Windows 的“设置 → 时间和语言 → 语音”里添加中文(简体)语音即可。

命令行用法:`python assistant/assistant.py --persona friday --voice`(普通小助理也可以加 `--voice`)。手动安装语音依赖:`python -m pip install -r assistant/requirements-voice.txt`。

### 安全

- **执行命令和写文件之前一定会先问你**,你输入 `y` 才会执行;输入 `a` 表示本次运行期间都不再询问(关掉程序后恢复询问)。读取文件、搜索网页不需要确认。
- 命令默认在你的用户主目录下执行,可以用 `--workdir 目录` 换成别的目录。
- 不想让它联网,加参数 `--no-web`。

### 记忆

记忆保存在 `assistant/friday_memory.json`(已被 `.gitignore` 忽略,不会提交到 Git)。也可以直接让它“忘掉某件事”,或者用命令管理:

| 命令 | 作用 |
|---|---|
| `/memory` | 查看它记得的所有事 |
| `/forget 关键词` | 删除包含这个关键词的记忆 |
| `/clear` | 开始新话题(记忆保留,对话历史清空) |

想把记忆放到别的位置(比如网盘同步目录),在 `assistant/.env` 里写 `FRIDAY_MEMORY_FILE=路径`,或加参数 `--memory-file 路径`。

### 称呼

“星期五”默认称呼你为“老板”。想换个称呼,任选一种方式:

- 在 `assistant/.env` 里加一行 `FRIDAY_CALL_ME=队长`
- 或者运行时加参数 `--call-me 队长`(优先于 `.env`)

下面是手动安装和运行的方法(macOS / Linux 也适用)。

## 安装

需要 Python 3.9 及以上版本。

```bash
pip install -r assistant/requirements.txt
```

## 设置 API Key

先到 <https://console.anthropic.com/> 获取 API Key,然后任选一种方式配置。

**方式一(推荐):写在 .env 文件里**,只需设置一次:

```bash
copy assistant\.env.example assistant\.env      # Windows
cp assistant/.env.example assistant/.env        # macOS / Linux
```

用记事本(或任意编辑器)打开 `assistant/.env`,把 `在这里填你的Key` 换成你的 Key,保存即可。`.env` 已被 `.gitignore` 忽略,不会被提交到 Git。

**方式二:设置环境变量**

```bash
export ANTHROPIC_API_KEY=你的Key          # macOS / Linux
set ANTHROPIC_API_KEY=你的Key             # Windows CMD
$env:ANTHROPIC_API_KEY="你的Key"          # Windows PowerShell
```

两种方式都配置了时,以环境变量为准。如果想用别的位置的文件,可以加参数 `--env-file 路径`。

## 运行

```bash
python assistant/assistant.py
```

对话示例:

```
你好!我是你的 AI 小助理,有什么问题尽管问我。
命令:/clear 清空对话  /exit 退出

你:Python 里列表和元组有什么区别?
助理:……
```

### 命令

| 命令 | 作用 |
|---|---|
| `/clear` | 清空对话历史,开始新话题 |
| `/exit` 或 `/quit` | 退出(也可以按 Ctrl+C 或 Ctrl+D) |

回答过程中按 Ctrl+C 可以中断当前回答,不会退出程序。

### 可选参数

```bash
python assistant/assistant.py --effort high        # 思考更仔细(更慢)
python assistant/assistant.py --model claude-sonnet-5-5
python assistant/assistant.py --persona friday     # 切换为“星期五”
python assistant/assistant.py --persona friday --call-me 队长
python assistant/assistant.py --persona friday --voice --stt vosk   # 语音对话,离线识别
```

`--effort` 可选 `low`、`medium`、`high`、`xhigh`、`max`。普通小助理默认 `low`(适合日常聊天),“星期五”默认 `medium`(要上网、动手做事,多想一点更稳)。

## 测试

```bash
python -m unittest assistant/test_assistant.py
```

测试使用假客户端,不会调用真实 API,也不会产生费用。
