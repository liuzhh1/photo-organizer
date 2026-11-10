# 拾光 · FRAME

运行在本机的照片素材整理软件。使用本地 SigLIP 2 完成图文语义匹配、候选分类和文字找图；SHA-256、感知哈希及图像统计完成去重和质量检查。界面和处理服务都在本机，不使用 CDN、外部字体或云端推理。

## 在这台电脑启动

从 GitHub 下载的是源码，模型、依赖目录与个人素材库不会随仓库提供。首次使用可以创建环境：

```powershell
conda env create -f environment.yml
conda activate frame-photo
python download_model.py
powershell -ExecutionPolicy Bypass -File .\start.ps1 -CondaEnv frame-photo
```

然后在「模型与设置」选择 `.models\siglip2-base-patch16-224`。也可以复用已有 Conda 环境，安装基础依赖和匹配设备的 PyTorch，再通过 `install-ai.ps1` 安装本项目依赖。下面的 `matanyone2` 是开发时已有环境的示例名称，可替换为自己的环境名。

双击 **启动拾光.cmd**，或在 PowerShell 中运行：

```powershell
cd '你的项目目录\photo-organizer'
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

默认复用已有 Conda 环境 **matanyone2**，不会新建环境。页面地址为 `http://127.0.0.1:8765`，启动后自动打开。服务以独立后台进程运行，启动窗口可以关闭；再次启动会复用已运行的服务。只有健康检查通过才会打开页面。日志保存在 `.data/server-*.log` 和 `.data/server-*.error.log`。

需要停止时运行 `powershell -ExecutionPolicy Bypass -File .\stop.ps1`。有任务运行时会提示先取消任务；停止脚本会核对项目和进程身份。如需前台排查，先停止后台服务，再运行 `.\start.ps1 -Foreground -NoBrowser`，此模式按 Ctrl+C 停止。关闭网页不会停止后台服务；Windows 重启后需再次双击启动脚本。

指定其他已有 Conda 环境或端口：

```powershell
.\start.ps1 -CondaEnv matanyone2 -Port 8766
```

项目按 Python 版本使用独立依赖目录：Python 3.10 使用 `.vendor-py310`，原 Python 3.12 安装使用 `.vendor`。这些目录优先于共享环境中的同名依赖，只供本软件使用。当前默认环境为现有 `matanyone2`：Python 3.10、PyTorch 2.10.0 / CUDA 12.8；本软件单独使用 Transformers 4.51.3。原 `Py3.12` 环境的旧 PyTorch DLL 在 2026-11-09 被 Windows 应用程序控制策略阻止加载，所以切换到已验证可运行的已有环境，没有修改系统安全策略或其他项目的环境文件。照片处理使用 Pillow，不依赖 torchvision 的可选检测算子。

若需要重新安装本软件的 AI / RAW 依赖（需要联网）：

```powershell
.\install-ai.ps1 -CondaEnv matanyone2
```

安装脚本默认使用清华 PyPI 镜像；如需官方源，可加 `-IndexUrl https://pypi.org/simple`。默认通过已有 Py3.12 环境中的 pip 下载目标 Python 版本的 Windows wheel，只写入项目依赖目录；可通过 -InstallerEnv 指定其他安装器环境，软件运行环境仍由 -CondaEnv 指定。

其他电脑可以复用已有 Python >=3.10 的 Conda 环境。该环境需包含 `requirements.txt` 中的基础依赖和合适的 PyTorch；然后运行安装脚本。`environment.yml` 仅作为在其他电脑新建环境的可选方案。

## 使用流程

1. 点击「导入素材」，选择文件夹。默认扫描子目录；同一路径、大小和修改时间未变化的图片直接复用分析结果。
2. 在「重复与相似」逐组对比。完全重复依据原文件 SHA-256；相似图片依据 63 位 pHash、长宽比和亮度分组。
3. 在「质量待检查」查看可能模糊、欠曝、过曝或分辨率偏低的图片。点击缩略图可查看大图与指标。
4. 标记「保留」或「排除」。排除只改变数据库中的状态，不会删除、移动或改写原片；可随时恢复待整理。
5. 配好本地模型后运行「AI 分类」。在搜索栏切换到「AI 语义」，输入如「海边的日落」「穿红色衣服的人」寻找画面。
6. 选择照片后导出所选；未选择时导出所有「已保留」。导出复制原始文件，逐文件核对 SHA-256，可按分类建立目录，同时生成 UTF-8 CSV 清单。同名文件自动区分；每次导出使用新的独立文件夹。

预览快捷键：`←` / `→` 切换，`P` 保留，`X` 排除，`Esc` 关闭。主界面按 `/` 聚焦搜索。

AI 分类处理「待整理」和「已保留」照片，跳过「已排除」照片。恢复某张照片为待整理后，可重新运行分类补充其向量。

## NAS 和文件夹浏览

导入、导出及模型目录旁的「浏览」会打开软件内的文件夹浏览器。左侧可选本地盘、Windows 映射网络盘和最近使用的位置；上方地址栏可粘贴完整 UNC 路径，例如 `\\nas.example\photos\旅行\示例相册`，按 Enter 或点击「前往」，点击子文件夹即可进入，再点击「选择此文件夹」确认。支持上级目录、子文件夹筛选和分页；也可直接在导入或导出的路径栏填写 UNC、`smb://` 或 `file://` 地址。

输入 `\\nas.example` 可列出当前 Windows 用户可枚举的共享。部分 NAS 禁止共享枚举，此时直接输入已知的完整共享路径即可。访问权限沿用当前 Windows 登录会话；需要认证时，请先在 Windows 资源管理器打开相同路径并登录 NAS，然后使用同一 Windows 用户启动拾光。软件不接收或保存 NAS 密码。

文件夹选择器只浏览一层目录，选择确认后才记住最近位置；不会自动扫描或写入 NAS。点击「开始导入」才建立照片索引。NAS 文件断开连接或权限不足时会显示具体提示。

## 配置本地 SigLIP 2

在「模型与设置」选择一个完整的 Hugging Face 格式 SigLIP 2 模型目录。推荐 `google/siglip2-base-patch16-224`。注意固定分辨率 SigLIP 2 的配置架构为 `siglip`，NaFlex 版本为 `siglip2`；两种架构均支持。目录需包含：

- `config.json`、处理器配置、分词器配置及对应分词器文件。
- `model.safetensors`，或 safetensors 分片及其索引。

软件不会在推理时自动下载模型，也不执行模型仓库中的远程 Python 代码。缺少模型时，导入、去重、质量筛选、标记和导出仍然可用；AI 分类和语义检索会提示配置。

如果尚未下载模型，可主动运行下面的一次性联网下载命令：

```powershell
conda run --no-capture-output -n matanyone2 python download_model.py
```

默认保存至本项目 `.models\siglip2-base-patch16-224`。如网络无法访问 Hugging Face，可通过你可用的模型来源手动准备完整目录；下载脚本支持 `--destination`、`--model` 和 `--revision`。

下载脚本还支持 `--endpoint https://hf-mirror.com` 指定镜像，且下载结束后会检查权重是否完整。软件会区分「未配置模型」「权重未下载完整」与「本地服务已断开」；服务重启后，页面会自动重连并更新会话令牌，已经导入的素材无需重新导入。

推理设备可以选「自动」「CUDA」「CPU」。自动模式在 CUDA 可用时选择 NVIDIA GPU；模型加载失败或显存不足时会报告原因，可改为 CPU。当前实现按照片逐张推理，归一化后保存图像向量；检索复用这些向量。修改模型目录会清除旧向量及分类；修改分类描述会清除旧分类，重新运行分类时可复用图像向量。权重、处理器或分词器文件的大小或修改时间变化会改变缓存指纹；替换模型文件后需重新运行 AI 分类。

分类描述可用中文或英文。软件将每张图片分配给候选描述中余弦相似度最高的一类；**余弦相似度不是正确率**，也不是开放世界识别。分类候选应覆盖你的拍摄内容，分类结果需要复核。SigLIP 2 提供图文向量匹配，不生成长篇图片说明，也不进行 OCR。

## 支持格式与判断范围

普通图像：JPEG、PNG、WebP、BMP、TIFF。TIFF 目前只分析第一帧；相机旋转标记会用于显示方向。

RAW：NEF、NRW、ARW、CR2、CR3、DNG、RAF、RW2、ORF、PEF、SRW，通过可选 `rawpy` / LibRaw 解码，具体机型支持取决于其版本。优先使用内嵌预览；无预览时尝试半尺寸解码。显示尺寸和质量指标来自所分析的预览或解码图，不能当作 RAW 传感器原始数据的测量。RAW 与 JPEG 仍是独立资产；是否归入同一相似组由预览哈希决定。本版本面向照片，不支持视频、HEIC 或旁车文件联动。

质量指标以最长边 1024 的灰度图计算 Laplacian 方差和曝光比例，并结合分析图像尺寸给出辅助评分。清晰度 <65 提示可能模糊；像素亮度 <12 / >245 的比例超过 42%，或均值 <38 / >222 时提示曝光风险；短边 <800 提示分辨率偏低。分数低于设置阈值，**或**出现上述任何提示，都会进入待检查列表。夜景、高调摄影、虚化背景和本来缺少纹理的场景可能误报；不会自动删除照片。

相似组是满足哈希距离、亮度差和长宽比条件的连通分组。A 与 B 接近、B 与 C 接近时，三张图可能同组，A 与 C 不一定直接接近。纯色或极低对比图像不进行感知哈希合并，但相同原文件仍能识别为完全重复。「选择建议排除项」只选择每组中质量较低的待整理照片，包含其他分页上的建议项；不会自动标记或删除。请逐组检查。

## 数据与项目结构

数据库、设置和缩略图位于 `.data/`；图像向量存储在 SQLite 中。原片留在导入位置。程序监听 `127.0.0.1`，写操作使用会话令牌并校验请求来源。适用于单个本机服务实例，请勿把该端口暴露到公网。

已导入但随后被移动的原片仍会保留索引，预览和导出会报告文件缺失。重新导入现有目录可刷新变化文件；原图变化时旧 AI 分类、向量和人工标记会重置。软件只导出图像本体，不联动复制 XMP、音频注释等旁车文件。

```text
app.py              本机 HTTP 服务与程序入口
bootstrap.py        项目局部依赖加载
library.py          SQLite 素材库、后台任务、导出
folders.py          本地盘 / UNC / NAS 文件夹浏览
imaging.py          图像读取、pHash、质量指标
model.py            本地 SigLIP 2 图文推理
web/                无需构建的前端
tests/              素材、导出、接口及推理验证
start.ps1           复用已有 Conda 环境启动
install-ai.ps1      安装到项目目录的可选依赖
download_model.py   主动下载模型的独立工具
```

运行自动测试：

```powershell
conda run --no-capture-output -n matanyone2 python -m unittest discover -s tests -v
```

自动测试在临时目录生成测试图像，不会访问你的实际照片。当前已通过 26 项自动测试和 Playwright 界面流程验证（含断线重连、会话续期和缺少模型时的错误提示）。自动推理测试使用本地生成的小型随机权重模型，覆盖固定分辨率与 NaFlex 架构、CPU / CUDA 图文向量、分类及语义检索；这是接口和流程验证，不代表预训练模型在实际素材上的准确率。正式模型在用户素材上的验证情况记录在 `VERIFICATION.md`。尚未使用真实相机 RAW 文件进行机型兼容性验证。

浏览器测试需要 Node.js 和 Playwright。通过 `QA_PYTHON` 指向已有 Conda 环境的解释器，可选 `QA_BROWSER` 指向 Chrome；未设置时使用 Playwright 安装的 Chromium。NAS 实测需要主动设置 `QA_NAS_PATH`，不会默认连接任何网络位置。

## GitHub 源码与本地备份

Git 忽略 `.data`、`.models`、`.vendor*`、`qa`、日志、压缩包及凭据文件。它们分别包含个人素材索引、缩略图、图像向量、模型和本机依赖。不要把带个人素材库的本地打包版作为公开 Release 上传。

`package_release.py` 用于个人本地备份，会包含当前素材库与缩略图；默认写入项目同级的 `releases`。它与纯源码仓库的内容不同。上传源码不会启动照片服务。
