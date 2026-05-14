# Setup Guide（完整部署手冊）

本手冊適用**第一次拿到這個專案、要從零部署到能用**的情境。從一台全新 Ubuntu 機器開始，照著做就能跑出第一份會議摘要。

- **首次部署：** 約 30–60 分鐘（含 OS 套件安裝 + STT 模型下載）
- **每次處理一場會議：** 5–15 分鐘
- **適用場景：** 公司內部部署，單機或一台主機 + 一台 GPU 機器

---

## 目錄

1. [硬體需求](#一硬體需求)
2. [作業系統與軟體需求](#二作業系統與軟體需求)
3. [安裝前確認（Pre-flight Check）](#三安裝前確認pre-flight-check)
4. [安裝步驟](#四安裝步驟)
5. [設定 `.env`](#五設定-env)
6. [啟動服務](#六啟動服務)
7. [第一場會議：完整操作步驟](#七第一場會議完整操作步驟)
8. [日常使用](#八日常使用)
9. [常見問題排除](#九常見問題排除)
10. [備份與資料位置](#十備份與資料位置)

---

## 一、硬體需求

系統由三個吃資源的部分組成：**STT（語音轉文字）**、**LLM（摘要與校正）**、**Gateway / Worker / Redis**。最關鍵的瓶頸是 **STT 的 GPU**。

> **本手冊預設你有一張 NVIDIA GPU。** 不是不能用 CPU 跑，但 10 分鐘音檔在 CPU 上可能要轉 5–10 分鐘，會議多了完全不堪用，**所以強烈建議準備 GPU**。

### 推薦配置（單機跑全部）

| 元件 | 最低 | 推薦 | 說明 |
|---|---|---|---|
| **GPU** | NVIDIA GTX 1060 6GB | RTX 3060 12GB / RTX 4070 以上 | STT `large-v3` 模型約佔 5GB VRAM；若要同時跑本地 LLM，VRAM 需更大 |
| **CPU** | 4 核 | 8 核以上 | Celery worker 與 ffmpeg 解碼用 |
| **RAM** | 16 GB | 32 GB | 模型載入 + 多任務並行 |
| **硬碟** | 30 GB SSD 可用空間 | 100 GB+ SSD | 模型快取 ~5 GB，會議音檔依量增長 |
| **網路** | 100 Mbps | 1 Gbps | 首次下載模型約 3 GB；之後僅本機 |

### LLM 三種選擇對應的硬體

| 方案 | 額外硬體 | 適合誰 |
|---|---|---|
| **A. 用雲端 LLM**（OpenAI / Anthropic / Gemini） | 不需要 | 想最快跑起來，可接受會議內容送到外部 API |
| **B. 本機 Ollama** | 小模型靠 STT 那張 GPU 共用即可 | 內網保密、可接受品質稍弱 |
| **C. 本機 vLLM / 自架 OpenAI-compatible server** | 多一張 GPU 或更大的 GPU（建議 ≥ 16GB VRAM） | 內網保密、要高品質 |

> **內部使用建議：** 第一次先用 **A. 雲端 LLM**（最快驗證流程跑得通），之後再切到 B 或 C。切換只改 `.env`，不用改任何程式。

---

## 二、作業系統與軟體需求

> ℹ️ 下表的版本欄位部分為估計值，待實際部署環境驗證後會以實測值更新。

| 項目 | 最低版本 | 實測版本 | 備註 |
|---|---|---|---|
| 作業系統 | Ubuntu 22.04 LTS | _待填_ | Ubuntu 24.04 / Windows 11 + WSL2 亦可；**不支援裸 Windows / macOS**，因 NVIDIA Container Toolkit 需要 Linux |
| NVIDIA Driver | ≥ 525 | _待填_ | CUDA 12.2 相容；跑 `nvidia-smi` 確認 |
| Docker Engine | ≥ 24.0 | _待填_ | |
| Docker Compose plugin | ≥ v2.20 | _待填_ | 用 `docker compose version` 確認（注意是 `docker compose`，不是舊版 `docker-compose`） |
| NVIDIA Container Toolkit | 最新 | _待填_ | 讓 Docker 容器能用 GPU |
| Git | ≥ 2.25 | _待填_ | 用來 clone 專案 |
| 瀏覽器 | Chrome / Edge / Firefox 最新版 | — | 操作 Web UI |
| Python（選用） | ≥ 3.11 | _待填_ | 只有要跑 CLI 或開發模式才需要；純用 Docker + Web UI 可忽略 |

下面是 Ubuntu 22.04 / 24.04 的完整安裝指令，**從一台全新機器開始**也適用。其他發行版請對照官方文件。

### 2.1 安裝 Git

```bash
sudo apt-get update
sudo apt-get install -y git
git --version    # 確認裝好
```

### 2.2 安裝 Docker Engine + Docker Compose plugin

> 不要用 `sudo apt-get install docker.io` —— 那個版本太舊，且不含 `docker compose` plugin。請用 Docker 官方 repo。

```bash
# 1) 移除舊版（如果有）
for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
    sudo apt-get remove -y $pkg 2>/dev/null
done

# 2) 安裝必要工具
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

# 3) 加入 Docker 官方 GPG key
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# 4) 加入 Docker repo
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 5) 安裝 Docker Engine + Compose plugin
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 6) 把目前使用者加入 docker group（之後不用 sudo）
sudo usermod -aG docker $USER
# ⚠️ 執行完上面這行要登出再登入（或重開機）才會生效

# 7) 驗證
docker --version              # 應該看到 Docker version 26.x+ 之類
docker compose version        # 應該看到 Docker Compose version v2.x+
docker run --rm hello-world   # 第一次驗證能跑容器
```

### 2.3 安裝 NVIDIA Driver（若還沒裝）

```bash
# 用 ubuntu-drivers 自動挑合適的 driver
sudo apt-get install -y ubuntu-drivers-common
sudo ubuntu-drivers autoinstall
sudo reboot                    # 重開機後 driver 才會載入

# 重開機後驗證
nvidia-smi                     # 應該看到 GPU 名稱、Driver Version ≥ 525、CUDA Version ≥ 12.2
```

> 若 `nvidia-smi` 仍找不到指令或失敗，請參考 [NVIDIA 官方 driver 安裝文件](https://docs.nvidia.com/datacenter/tesla/tesla-installation-notes/index.html) 手動處理。

### 2.4 安裝 NVIDIA Container Toolkit（讓 Docker 用 GPU）

```bash
# 1) 加入 NVIDIA repo
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# 2) 安裝並設定 Docker runtime
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 3) 驗證 Docker 能看到 GPU
docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
# 預期：在容器內看到 GPU 資訊（和直接跑 nvidia-smi 一樣）
```

### 2.5 安裝 Python 3.11（選用，僅 CLI / 開發者需要）

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev
python3.11 --version
```

> Web UI 使用者跳過這一步即可——所有 Python 環境都包在 Docker 容器內。

---

## 三、安裝前確認（Pre-flight Check）

**先跑這 4 個指令**，全部 OK 才繼續安裝。任何一個失敗，請先解決。

### 1. GPU 與 Driver

```bash
nvidia-smi
```

預期看到你的 GPU 名稱、Driver Version（≥ 525）、CUDA Version（≥ 12.2）。

### 2. Docker

```bash
docker --version
docker compose version
```

兩個版本號都要出來。

### 3. Docker 能用 GPU（最重要的一關）

```bash
docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
```

預期：在容器內看到和步驟 1 一樣的 GPU 資訊。若失敗：回到第二章重新裝 NVIDIA Container Toolkit。

### 4. 連外網路（下載模型用）

```bash
curl -I https://huggingface.co
```

預期看到 `HTTP/2 200`。第一次啟動 `stt_service` 會從 HuggingFace 下載 `large-v3` 模型（約 3 GB）。

---

## 四、安裝步驟

### 1. 取得專案

```bash
git clone <你的 repo URL>
cd Meeting-Assistant
```

### 2. 建立 `.env` 設定檔

```bash
cp .env.example .env
```

下一節會教你怎麼編輯它。

### 3. 建立資料目錄

```bash
mkdir -p data/storage
```

這是 SQLite 資料庫和音檔的存放位置。

---

## 五、設定 `.env`

打開 [.env](../.env) 編輯。**唯一一定要動的是 LLM 三行設定**，其它預設值通常不用改。

### 範例 A：用 OpenAI（最簡單，推薦第一次跑）

```env
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-你的金鑰
LLM_BASE_URL=
```

> `LLM_BASE_URL` 留空，會自動用 OpenAI 官方端點。

### 範例 B：用 Anthropic Claude

```env
LLM_MODEL=anthropic/claude-sonnet-4-6
LLM_API_KEY=sk-ant-你的金鑰
LLM_BASE_URL=
```

### 範例 C：用本機 Ollama（內網保密）

先在主機上裝 Ollama 並下載模型：

```bash
# 安裝 Ollama（一次性）
curl -fsSL https://ollama.com/install.sh | sh

# 下載模型
ollama pull qwen3:4b
```

`.env` 改成：

```env
LLM_MODEL=ollama/qwen3:4b
LLM_API_KEY=
LLM_BASE_URL=http://host.docker.internal:11434
```

> `host.docker.internal` 是固定關鍵字，代表「Docker 容器要連到主機本身」。docker-compose.yml 已經設定好對應。

### 範例 D：用本機 vLLM（高品質、內網保密）

假設 vLLM 已經在 `8002` port 啟動：

```env
LLM_MODEL=Qwen/Qwen3-4B
LLM_API_KEY=no-key
LLM_BASE_URL=http://host.docker.internal:8002
```

> 注意 `LLM_API_KEY` 不能空字串，要填 `no-key`（任意非空字串都可）。

### STT 相關設定（通常不用改）

```env
STT_MODEL=large-v3       # 中文/英文最準
STT_DEVICE=cuda
STT_COMPUTE_TYPE=float16 # RTX 30/40 系列推薦；舊卡可改 int8_float16
```

> VRAM 不夠（< 6GB）可把 `STT_MODEL` 改成 `medium` 或 `small`，準度略降但記憶體省一半。

---

## 六、啟動服務

### 1. 第一次啟動（會花 5–15 分鐘 build + 下載模型）

```bash
docker compose up -d --build
```

### 2. 確認所有容器都起來

```bash
docker compose ps
```

預期看到 5 個 service，狀態都是 `running` 或 `healthy`：

```
NAME                                 STATUS
meeting-assistant-gateway-1          Up (healthy)
meeting-assistant-redis-1            Up (healthy)
meeting-assistant-stt_service-1      Up (healthy)
meeting-assistant-task_worker_llm-1  Up
meeting-assistant-task_worker_stt-1  Up
```

> **`stt_service` 第一次會卡在 "starting" 狀態 2–10 分鐘**——它正在下載 `large-v3` 模型。耐心等。

### 3. 看 STT 模型下載進度（可選）

```bash
docker compose logs -f stt_service
```

看到 `Application startup complete` 表示模型已載入完成，可以開始用。

### 4. 打開 Web UI

瀏覽器開：**http://localhost:8000**

看到 Meeting Assistant 介面（四個 tab：Meetings / Prompts / Aggregations / Settings）即代表成功。

### 5. 驗證 LLM 連線（重要！）

進入 **Settings** tab，最下方有 **「測試連線」** 按鈕。沒有過代表 `.env` 的 LLM 設定有誤——回第五章修正後重新 `docker compose up -d`。

---

## 七、第一場會議：完整操作步驟

以**「上傳一段會議錄音 → 拿到 Markdown 摘要」**為目標。

### Step 1：準備一段測試音檔

支援格式：`.mp3`、`.wav`、`.m4a`、`.mp4`、`.webm`、`.ogg`（任何 ffmpeg 能解的都可以）。

**建議第一次先用一段短的（1–3 分鐘）來驗證流程**，避免等太久。

### Step 2：建立會議

1. 進入 **Meetings** tab → 點右上角 **「+ 新增」**
2. 標題可留空（會自動用日期命名）
3. 點 **「建立」**

### Step 3：上傳音檔

1. 在會議詳情頁，確認在 **「逐字稿」** 子分頁
2. 確認 **「上傳音檔」** 模式（不是「輸入逐字稿」）
3. 點檔案選擇按鈕，選你的音檔
4. 點 **「上傳並轉錄」**
5. 畫面會出現狀態列：`pending` → `running` → `done`
   - 1 分鐘音檔約需 10–30 秒（GPU）；CPU 模式則 1–5 分鐘
   - 完成後會顯示「原始逐字稿」

### Step 4：校正逐字稿（選用但建議）

逐字稿常會把專有名詞（人名、公司名、技術詞）聽錯，例如把「Hugging Face」聽成「哈金費斯」。

1. 點 **「重新校正」**
2. 在 **「校正詞典（每行 `錯誤=正確`）」** 欄位填入修正規則，例如：

   ```
   哈金費斯=Hugging Face
   克勞德=Claude
   阿里巴巴=Alibaba
   ```

3. 點 **「開始校正」**，等待 `done`

> 詞典是用「先字典替換 → 再 LLM 上下文修補」兩階段。專有名詞用詞典最準，LLM 會處理上下文語氣與標點。

### Step 5：生成摘要

1. 切到 **「摘要」** 子分頁
2. **「逐字稿來源」** 選 「校正後」（如果沒做校正就選「原始逐字稿」）
3. **「場景」** 選一個內建模板：
   - `weekly_standup` — 週會（進度、決議、Action、Blocker）
   - `project_review` — 專案審查（狀態、風險、決議、Action）
   - `client_interview` — 客戶訪談（需求、痛點、討論、下步）
   - `general` — 通用（摘要、討論、決議、Action、未決議題）
4. **「補充資訊」**（選填）：可填參與者、會議背景，會幫助 LLM 寫得更準
5. 點 **「生成摘要」**，等待 `done`
6. 完成後底下「歷史摘要」會列出這份摘要

### Step 6：下載 Markdown

點摘要旁邊的 **「下載」** 按鈕，會得到 `{title}_{date}.md` 檔案。

**🎉 至此完成第一場會議全流程。**

---

## 八、日常使用

### 跨會議聚合報告（多場會議合併成一份）

例如把連續四週的週會摘要合併成月報：

1. 進入 **Aggregations** tab → **「+ 新增聚合」**
2. 勾選要合併的會議（≥ 2 場）
3. 在 「Labels」欄位給每場一個名稱（如 `W1, W2, W3, W4`）
4. 補充資訊欄可寫月報的格式要求
5. 點 **「開始聚合」**

### 自訂 Prompt（公司專屬模板）

1. 進入 **Prompts** tab → **「+ 新增自訂」**
2. 填入名稱（例：「技術週會-IT 部門」）和 System Prompt
3. 儲存後，這個模板會出現在 Meetings tab 的「場景」下拉選單中

### 切換 LLM（不重啟服務）

進入 **Settings** tab，可以為「目前這個瀏覽器 session」覆寫 LLM URL、Model、API Key——適合臨時測試不同模型，不影響其他人的設定。

### CLI 模式（不開 Docker，僅做檔案處理）

適合自動化批次處理：

```bash
pip install -e ".[cli]"

meeting-assistant transcribe recording.mp3 \
  --stt-url http://localhost:8080 \
  --output transcript.txt

meeting-assistant summarize transcript.txt \
  --llm-url http://localhost:11434/v1 \
  --model ollama/qwen3:4b \
  --scene weekly_standup \
  --output summary.md
```

詳細參數見 [docs/usage.md](usage.md)。

---

## 九、常見問題排除

### Q1：`docker compose up` 後 `stt_service` 一直 `unhealthy`

**最常見原因：** GPU 設定有問題。

```bash
# 看 log
docker compose logs stt_service | tail -50
```

- 看到 `could not select device driver "" with capabilities: [[gpu]]` → NVIDIA Container Toolkit 沒裝好，回第二章
- 看到模型下載卡住 → 檢查網路、HuggingFace 連線
- 看到 `CUDA out of memory` → VRAM 不夠，把 `.env` 的 `STT_MODEL` 改成 `medium` 或 `small`

### Q2：上傳音檔後一直 `pending`，不動

```bash
docker compose logs task_worker_stt | tail -30
```

- 沒看到任何訊息 → worker 沒連到 Redis，重啟 `docker compose restart task_worker_stt`
- 看到錯誤訊息 → 通常是 stt_service 沒準備好，等 STT 服務 `healthy` 再上傳

### Q3：摘要任務 `failed`

```bash
docker compose logs task_worker_llm | tail -30
```

- `401 Unauthorized` / `Invalid API key` → `.env` 的 `LLM_API_KEY` 錯了
- `Connection refused` 連 `host.docker.internal` → 本機的 Ollama / vLLM 沒開，或 port 不對
- `404 model not found` → `LLM_MODEL` 名稱拼錯，到 Settings tab 點 LLM 「測試連線」會列出可用 model

### Q4：改了 `.env` 但沒生效

要重新讀取 env，必須跑：

```bash
docker compose up -d
```

**不要用 `docker compose restart`** — 它不會重讀 `.env`。

### Q5：想完全重來（清掉所有資料）

```bash
docker compose down -v          # 停服務 + 刪 volume（Redis 資料）
rm -rf data/                    # 刪 SQLite 與所有音檔（慎重！）
docker compose up -d --build    # 重新啟動
```

### Q6：磁碟快滿了怎麼辦

最大宗是 `data/storage/` 下的音檔。可以：
- 在 Web UI 刪除舊會議（會 cascade 刪掉對應音檔）
- 或直接砍 `data/storage/audio/` 下不需要的檔案

模型快取 (`~/.cache/huggingface`) 不要刪——刪了下次啟動會重新下載 3 GB。

### Q7：STT 跑得超慢

- 確認 `.env` 是 `STT_DEVICE=cuda`、`STT_COMPUTE_TYPE=float16`
- 在轉錄時另開一個 terminal 跑 `nvidia-smi`，應該看得到 GPU 利用率拉高；若沒有 → GPU 沒被吃到，回 Q1 排查
- `large-v3` 本身較慢，急用可改 `medium`（速度快 1.5–2x，品質下降不多）

---

## 十、備份與資料位置

所有資料都在專案根目錄的 `data/`：

```
data/
├── meeting_assistant.db         # SQLite — 所有會議、摘要、Prompt
└── storage/
    └── audio/                   # 上傳的音檔
```

### 簡易備份（內部使用足夠）

```bash
# 每日跑一次就好
tar czf backup_$(date +%Y%m%d).tar.gz data/
```

### 恢復

```bash
docker compose down
tar xzf backup_YYYYMMDD.tar.gz
docker compose up -d
```

---

## 還有問題？

- 完整 CLI / API 文件：[docs/usage.md](usage.md)
- 架構細節：[docs/architecture.md](architecture.md)
- 開發者指南：[docs/development.md](development.md)
